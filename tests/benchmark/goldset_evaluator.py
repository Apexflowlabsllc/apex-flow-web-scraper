"""Evaluate a scraper run against a signed, permissioned JSON gold set.

The evaluator is offline: it never fetches target URLs. Gold-set reviewers sign
the canonical manifest with APEX_GOLDSET_SIGNING_KEY; each run is a separate
JSON file containing one observation per case. Metrics are kept separate so
reach, coverage and returned-value accuracy cannot hide one another.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
ALGORITHM = "hmac-sha256"
VALID_STATUSES = {"success", "blocked", "error", "cancelled"}


class GoldSetError(ValueError):
    """Invalid, unauthorized, expired, or untrusted benchmark input."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _now(value: str) -> datetime:
    if not isinstance(value, str):
        raise GoldSetError("timestamps must be ISO-8601 strings")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise GoldSetError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise GoldSetError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _manifest_payload(manifest: dict) -> dict:
    return {key: value for key, value in manifest.items() if key != "signature"}


def signature_for(manifest: dict, key: bytes) -> str:
    if len(key) < 32:
        raise GoldSetError("signing key must be at least 32 bytes")
    return hmac.new(key, _canonical(_manifest_payload(manifest)), hashlib.sha256).hexdigest()


def sign_manifest(manifest: dict, key: bytes) -> dict:
    signed = dict(manifest)
    signed.pop("signature", None)
    signed["signature"] = {"algorithm": ALGORITHM, "value": signature_for(signed, key)}
    return signed


def verify_manifest(manifest: dict, key: bytes, *, now: datetime | None = None) -> dict:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise GoldSetError("unsupported gold-set schema_version")
    sig = manifest.get("signature")
    if not isinstance(sig, dict) or sig.get("algorithm") != ALGORITHM or not isinstance(sig.get("value"), str):
        raise GoldSetError("gold set has no supported signature")
    expected = signature_for(manifest, key)
    if not hmac.compare_digest(sig["value"], expected):
        raise GoldSetError("gold-set signature is invalid")

    required = ("dataset_id", "version", "created_at", "expires_at", "permission", "cases")
    if any(not manifest.get(field) for field in required):
        raise GoldSetError("gold set is missing required metadata")
    created, expires = _now(manifest["created_at"]), _now(manifest["expires_at"])
    current = now or datetime.now(timezone.utc)
    if created > current or expires <= current or expires <= created:
        raise GoldSetError("gold set is not currently valid")

    permission = manifest["permission"]
    if not isinstance(permission, dict) or permission.get("explicit_owner_consent") is not True:
        raise GoldSetError("gold set must record explicit owner permission")
    if not permission.get("owner") or not permission.get("evidence_ref"):
        raise GoldSetError("gold set must identify the permission owner and evidence reference")
    domains = permission.get("allowed_domains")
    if not isinstance(domains, list) or not domains or any(not isinstance(d, str) or not d for d in domains):
        raise GoldSetError("permission.allowed_domains must be a non-empty list")
    allowed = {d.lower().rstrip(".") for d in domains}

    cases = manifest["cases"]
    if not isinstance(cases, list) or not cases:
        raise GoldSetError("gold set must contain at least one case")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not all(case.get(k) for k in ("id", "url", "category", "expected_fields")):
            raise GoldSetError("each case needs id, url, category, and expected_fields")
        if case["id"] in ids:
            raise GoldSetError(f"duplicate case id: {case['id']}")
        ids.add(case["id"])
        parts = urlsplit(case["url"])
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise GoldSetError(f"case {case['id']} has an invalid or credential-bearing URL")
        host = parts.hostname.lower().rstrip(".")
        if host not in allowed:
            raise GoldSetError(f"case {case['id']} host is outside the signed permission scope")
        fields = case["expected_fields"]
        if not isinstance(fields, dict) or not fields:
            raise GoldSetError(f"case {case['id']} expected_fields must be a non-empty object")
        for path, rule in fields.items():
            if not isinstance(path, str) or not path or not isinstance(rule, dict) or "value" not in rule:
                raise GoldSetError(f"case {case['id']} has an invalid expected field")
            evidence = rule.get("evidence")
            if not isinstance(evidence, dict) or not evidence.get("quote"):
                raise GoldSetError(f"case {case['id']} field {path} needs a human-reviewed source quote")
            if rule.get("normalize", "exact") not in {"exact", "casefold", "whitespace", "casefold_whitespace"}:
                raise GoldSetError(f"case {case['id']} field {path} has an unsupported normalizer")
    return manifest


def _lookup(record: dict, path: str):
    value = record
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _normalize(value, mode):
    if isinstance(value, str):
        if mode in ("whitespace", "casefold_whitespace"):
            value = " ".join(value.split())
        if mode in ("casefold", "casefold_whitespace"):
            value = value.casefold()
    return value


def wilson(successes: int, total: int, z: float = 1.959963984540054):
    """95% Wilson score interval for a binomial proportion; None at n=0."""
    if total <= 0:
        return None
    p = successes / total
    den = 1 + z * z / total
    center = (p + z * z / (2 * total)) / den
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / den
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def _rate(successes: int, total: int):
    return {"successes": successes, "total": total,
            "rate": round(successes / total, 4) if total else None,
            "ci95_wilson": wilson(successes, total)}


def _case_metrics(case: dict, observation: dict):
    expected = case["expected_fields"]
    observed = observation.get("fields") if isinstance(observation.get("fields"), dict) else {}
    returned = correct = 0
    field_rows = {}
    for path, rule in expected.items():
        present, actual = _lookup(observed, path)
        ok = present and _normalize(actual, rule.get("normalize", "exact")) == _normalize(
            rule["value"], rule.get("normalize", "exact"))
        returned += int(present)
        correct += int(ok)
        field_rows[path] = {"present": bool(present), "correct": bool(ok)}
    return {"expected": len(expected), "returned": returned, "correct": correct, "fields": field_rows}


def evaluate(manifest: dict, run: dict) -> dict:
    if not isinstance(run, dict) or run.get("dataset_id") != manifest["dataset_id"]:
        raise GoldSetError("run dataset_id does not match the signed gold set")
    observations = run.get("pages")
    if not isinstance(observations, list):
        raise GoldSetError("run.pages must be a list")
    cases = {case["id"]: case for case in manifest["cases"]}
    by_id = {}
    for item in observations:
        if not isinstance(item, dict) or item.get("id") not in cases:
            raise GoldSetError("run contains an unknown or malformed case id")
        ident = item["id"]
        if ident in by_id:
            raise GoldSetError(f"run contains duplicate case id: {ident}")
        if item.get("url") != cases[ident]["url"]:
            raise GoldSetError(f"run URL does not match the signed case {ident}")
        if item.get("status") not in VALID_STATUSES:
            raise GoldSetError(f"case {ident} status must be one of {sorted(VALID_STATUSES)}")
        by_id[ident] = item

    reach_ok = expected_total = returned_total = correct_total = 0
    usd_correct = credits_correct = 0
    category_counts = defaultdict(lambda: Counter())
    field_counts = defaultdict(Counter)
    failures = Counter()
    per_case = []
    for ident, case in cases.items():
        observation = by_id.get(ident, {"status": "error", "error_code": "missing_result", "fields": {}})
        success = observation["status"] == "success"
        reach_ok += int(success)
        metric = _case_metrics(case, observation) if success else {"expected": len(case["expected_fields"]), "returned": 0, "correct": 0, "fields": {}}
        expected_total += metric["expected"]
        returned_total += metric["returned"]
        correct_total += metric["correct"]
        cost = observation.get("cost") if success else None
        if isinstance(cost, dict):
            if isinstance(cost.get("usd"), (int, float)) and cost["usd"] >= 0:
                usd_correct += metric["correct"]
            if isinstance(cost.get("credits"), (int, float)) and cost["credits"] >= 0:
                credits_correct += metric["correct"]
        cat = category_counts[case["category"]]
        cat.update(expected=metric["expected"], returned=metric["returned"], correct=metric["correct"],
                   pages=1, reached=int(success))
        if not success:
            cause = str(observation.get("error_code") or observation.get("error") or observation["status"])[:80]
            failures[cause] += 1
        for path in case["expected_fields"]:
            detail = metric["fields"].get(path, {"present": False, "correct": False})
            counts = field_counts[(case["category"], path)]
            counts.update(expected=1, returned=int(detail["present"]), correct=int(detail["correct"]))
        per_case.append({"id": ident, "category": case["category"], "status": observation["status"],
                         "expected_fields": metric["expected"], "returned_fields": metric["returned"],
                         "correct_fields": metric["correct"],
                         "latency_ms": observation.get("latency_ms") if success else None,
                         "cost": observation.get("cost") if success else None,
                         "failure_cause": None if success else cause})

    n_pages = len(cases)
    per_category = {}
    for category, counts in sorted(category_counts.items()):
        per_category[category] = {
            "page_reach": _rate(counts["reached"], counts["pages"]),
            "field_coverage": _rate(counts["returned"], counts["expected"]),
            "field_accuracy": _rate(counts["correct"], counts["returned"]),
            "correct_fields": counts["correct"], "expected_fields": counts["expected"],
        }
    per_field = {}
    for (category, path), counts in sorted(field_counts.items()):
        per_field[f"{category}.{path}"] = {
            "coverage": _rate(counts["returned"], counts["expected"]),
            "accuracy": _rate(counts["correct"], counts["returned"]),
        }
    latencies = sorted(item["latency_ms"] for item in per_case if isinstance(item["latency_ms"], (int, float)) and item["latency_ms"] >= 0)
    costs = [item.get("cost") for item in by_id.values() if item.get("status") == "success"]
    usd = [item["usd"] for item in costs if isinstance(item, dict) and isinstance(item.get("usd"), (int, float)) and item["usd"] >= 0]
    credits = [item["credits"] for item in costs if isinstance(item, dict) and isinstance(item.get("credits"), (int, float)) and item["credits"] >= 0]
    return {
        "schema_version": 1,
        "dataset": {"id": manifest["dataset_id"], "version": manifest["version"],
                    "permission_owner": manifest["permission"]["owner"], "evidence_ref": manifest["permission"]["evidence_ref"]},
        "run": {"label": run.get("label"), "engine": run.get("engine"), "engine_version": run.get("engine_version"),
                "started_at": run.get("started_at"), "completed_at": run.get("completed_at")},
        "page_reach": _rate(reach_ok, n_pages),
        "field_coverage": _rate(returned_total, expected_total),
        "field_accuracy": _rate(correct_total, returned_total),
        "counts": {"pages": n_pages, "pages_reported": len(by_id), "expected_fields": expected_total,
                   "returned_fields": returned_total, "correct_fields": correct_total},
        "latency_ms": {"count": len(latencies), "median": (latencies[(len(latencies) - 1) // 2] + latencies[len(latencies) // 2]) / 2 if latencies else None,
                       "p95": latencies[min(len(latencies) - 1, math.ceil(len(latencies) * .95) - 1)] if latencies else None},
        "cost": {"reported_usd": round(sum(usd), 8) if usd else None,
                 "reported_credits": sum(credits) if credits else None,
                 "usd_per_correct_field": round(sum(usd) / usd_correct, 8) if usd and usd_correct else None,
                 "credits_per_correct_field": round(sum(credits) / credits_correct, 6) if credits and credits_correct else None,
                 "correct_fields_with_usd": usd_correct, "correct_fields_with_credits": credits_correct,
                 "successful_pages_with_usd": len(usd), "successful_pages_with_credits": len(credits)},
        "failure_causes": dict(sorted(failures.items())),
        "by_category": per_category,
        "by_field": per_field,
        "cases": per_case,
        "limitations": ["Confidence intervals are descriptive binomial Wilson intervals; cases are not necessarily independent.",
                        "Page reach and field quality use only the signed cases and permission scope in this dataset.",
                        "The evaluator does not verify the scraper run's source receipts or the truth of human gold labels."],
    }


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GoldSetError(f"cannot read JSON {path}: {exc}") from exc


def _key_from_env(name: str) -> bytes:
    value = os.environ.get(name)
    if not value:
        raise GoldSetError(f"environment variable {name} is not set")
    key = value.encode("utf-8")
    if len(key) < 32:
        raise GoldSetError(f"environment variable {name} must contain at least 32 UTF-8 bytes")
    return key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sign_parser = sub.add_parser("sign", help="sign a permissioned manifest in place")
    sign_parser.add_argument("manifest", type=Path)
    sign_parser.add_argument("--key-env", default="APEX_GOLDSET_SIGNING_KEY")
    eval_parser = sub.add_parser("evaluate", help="verify permission/signature and score an offline run")
    eval_parser.add_argument("manifest", type=Path)
    eval_parser.add_argument("run", type=Path)
    eval_parser.add_argument("--key-env", default="APEX_GOLDSET_SIGNING_KEY")
    eval_parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        key = _key_from_env(args.key_env)
        manifest = _read_json(args.manifest)
        if args.command == "sign":
            if not isinstance(manifest, dict):
                raise GoldSetError("manifest root must be an object")
            # Validate permission and cases before signing; expiry is evaluated at score time.
            payload = dict(manifest)
            payload.pop("signature", None)
            payload.setdefault("signature", {"algorithm": ALGORITHM, "value": "0" * 64})
            verify_manifest(sign_manifest(payload, key), key)
            signed = sign_manifest(payload, key)
            args.manifest.write_text(json.dumps(signed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps({"signed": True, "dataset_id": signed["dataset_id"], "algorithm": ALGORITHM}))
            return 0
        verify_manifest(manifest, key)
        run = _read_json(args.run)
        result = evaluate(manifest, run)
        output = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
        else:
            sys.stdout.write(output)
        return 0
    except GoldSetError as exc:
        parser.exit(2, f"goldset_evaluator: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
