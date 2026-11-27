"""Add link history to a built link index: for each past Common Crawl domain-graph release, every indexed domain's
referring-domain count and authority, streamed straight from data.commoncrawl.org (nothing is kept on disk).

Output in <index dir>/history/: <release>.refs (u32) and <release>.auth (u16), aligned with keys.u64, plus index.json.
Run: py -3.12 scripts/build-linkhistory.py <index dir> [release ...]
"""
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pcsv

BASE = os.environ.get("LH_BASE", "https://data.commoncrawl.org/projects/hyperlinkgraph/{r}/domain/{r}-domain-{part}.txt.gz")
RELEASES = ["cc-main-2017-aug-sep-oct", "cc-main-2018-aug-sep-oct", "cc-main-2019-aug-sep-oct", "cc-main-2020-jul-aug-sep",
            "cc-main-2021-jun-jul-sep", "cc-main-2022-may-jun-aug", "cc-main-2023-may-sep-nov", "cc-main-2024-aug-sep-oct",
            "cc-main-2025-feb-mar-apr", "cc-main-2025-aug-sep-oct", "cc-main-2026-feb-mar-apr", "cc-main-2026-may-jun-jul"]
T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:6.0f}s] {m}", flush=True)


def stream(release, part, names, types):
    url = BASE.format(r=release, part=part)
    resp = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "apex-flow-linkgraph/1.0"}), timeout=120)
    src = pa.CompressedInputStream(pa.PythonFile(resp, mode="r"), "gzip")
    return pcsv.open_csv(src, read_options=pcsv.ReadOptions(column_names=names, block_size=1 << 26),
                         parse_options=pcsv.ParseOptions(delimiter="\t", quote_char=False),
                         convert_options=pcsv.ConvertOptions(column_types=types))


def hashes(arr):
    out = np.empty(len(arr), dtype=np.uint64)
    for i, n in enumerate(arr.to_pylist()):
        out[i] = int.from_bytes(hashlib.blake2b(n.encode(), digest_size=8).digest(), "little")
    return out


def one(release, keys, out):
    log(f"{release}: vertices")
    ids_h = []
    for b in stream(release, "vertices", ["id", "name", "hosts"], {"id": pa.int64(), "name": pa.large_string(), "hosts": pa.string()}):
        ids_h.append(hashes(b.column(1)))
    vh = np.concatenate(ids_h)
    total = len(vh)
    j = np.searchsorted(keys, vh)
    j = np.minimum(j, len(keys) - 1)
    hit = keys[j] == vh
    log(f"{release}: {total:,} domains, {hit.sum():,} in today's index; ranks")
    pos = np.full(total, 0, dtype=np.uint32)
    rank_hash, rank_pos = [], []
    for b in stream(release, "ranks", ["hc_pos", "hc_val", "pr_pos", "pr_val", "name", "hosts"],
                    {k: pa.string() for k in ("hc_pos", "hc_val", "pr_pos", "pr_val", "hosts")} | {"name": pa.large_string()}):
        keep = pc.invert(pc.starts_with(b.column(0), "#"))
        rank_pos.append(pc.cast(pc.filter(b.column(0), keep), pa.int64()).to_numpy())
        rank_hash.append(hashes(pc.filter(b.column(4), keep)))
    rh, rp = np.concatenate(rank_hash), np.concatenate(rank_pos)
    order = np.argsort(vh)
    k = np.searchsorted(vh[order], rh)
    k = np.minimum(k, total - 1)
    ok = vh[order][k] == rh
    pos[order[k[ok]]] = rp[ok].astype(np.uint32)
    log(f"{release}: edges")
    indeg = np.zeros(total, dtype=np.uint32)
    pending, n = [], 0
    for b in stream(release, "edges", ["f", "t"], {"f": pa.int64(), "t": pa.int64()}):
        t = b.column(1).to_numpy()
        n += len(t)
        pending.append(t)
        if sum(len(x) for x in pending) > 150_000_000:
            indeg += np.bincount(np.concatenate(pending), minlength=total).astype(np.uint32)
            pending.clear()
    if pending:
        indeg += np.bincount(np.concatenate(pending), minlength=total).astype(np.uint32)
    refs = np.zeros(len(keys), dtype="<u4")
    auth = np.zeros(len(keys), dtype="<u2")
    refs[j[hit]] = indeg[hit]
    p = pos[hit].astype(np.float64)
    a = np.where(p > 0, np.clip(np.round(1000 * (1 - np.log(np.maximum(p, 1)) / math.log(total))), 0, 1000), 0)
    auth[j[hit]] = a.astype("<u2")
    refs.tofile(out / f"{release}.refs")
    auth.tofile(out / f"{release}.auth")
    log(f"{release}: done, {n:,} links")
    return {"release": release, "domains": int(total), "links": int(n)}


def main(index, releases):
    index = Path(index)
    keys = np.fromfile(index / "keys.u64", dtype="<u8")
    out = index / "history"
    out.mkdir(exist_ok=True)
    meta_path = out / "index.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"releases": []}
    for r in releases:
        if any(x["release"] == r for x in meta["releases"]):
            continue
        try:
            meta["releases"].append(one(r, keys, out))
        except Exception as exc:
            log(f"{r}: skipped ({type(exc).__name__}: {exc})")
            continue
        meta["releases"].sort(key=lambda x: x["release"])
        meta_path.write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:] or RELEASES)
