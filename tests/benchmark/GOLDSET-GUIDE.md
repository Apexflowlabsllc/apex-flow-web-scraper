# Permissioned extraction benchmark gold sets

`goldset_evaluator.py` is an offline scoring tool for comparisons between Apex,
Firecrawl, and other engines. It does not request pages, call an AI model, or
send data anywhere. Benchmark operators collect results only from sources they
own or have explicit permission to test.

## Signed manifest

### Local synthetic regression fixtures

`fixtures/` contains six source pages authored in this repository: two product
layouts, an article, a job posting, an event and a directory table. The fixture
test runs the production HTML and recipe extractor, checks that expected values
and evidence quotes appear in each fixture, and scores page reach, field
coverage and field accuracy with the evaluator. Run it with:

```powershell
python -m pytest tests/test_owned_fixture_benchmark.py -q
```

These pages are synthetic and never fetched from a network. This is a
repeatable parser regression gate, not evidence of live-site coverage or an
Apex-versus-Firecrawl result. The test-only HMAC key exists only to exercise
signature validation and must never be reused for a real benchmark.

Create a JSON manifest with `schema_version: 1`, a unique `dataset_id`, a
semantic `version`, timezone-qualified `created_at` and `expires_at`, and a
`permission` object that records `explicit_owner_consent: true`, the owner,
an evidence reference, and exact allowed hostnames. Each case has a unique ID,
category, exact URL, and expected fields. Every expected value must include a
human-reviewed `evidence.quote` copied from the owned fixture or approved test
source. The quote and permission record become part of the signature.

Use a randomly generated secret of at least 32 bytes, stored outside the repo
in the benchmark operator's secret manager or environment. HMAC is a keyed
integrity check for a trusted evaluator team; it is not a public-key signature
that strangers can verify without access to the shared key. Never put the key
in a manifest, test result, source tree, or chat.

```powershell
$env:APEX_GOLDSET_SIGNING_KEY = '<load a 32+ byte secret from your secret manager>'
python tests/benchmark/goldset_evaluator.py sign path/to/owned-gold-set.json
```

Signing checks metadata, host scope, explicit owner consent, field definitions,
and evidence quotes. It does not independently prove that the cited permission
exists; the owner and evidence reference are accountable human inputs. The
signature expires with the manifest.

## Run record and scoring

The run JSON contains `dataset_id`, optional run metadata (`label`, `engine`,
`engine_version`, `started_at`, `completed_at`), and `pages`. Each page record
contains the signed case `id` and exact `url`, `status` (`success`, `blocked`,
`error`, or `cancelled`), nested `fields`, optional `error_code`,
`latency_ms`, and optional `cost` `{ "usd": 0.01, "credits": 1 }`. Include one
record per attempted case. An omitted record is counted as `missing_result`;
duplicate IDs and URL changes are rejected.

```powershell
python tests/benchmark/goldset_evaluator.py evaluate `
  path/to/owned-gold-set.json path/to/apex-run.json `
  --output path/to/apex-score.json
```

The output separates page reach, field coverage (expected values returned),
and field accuracy (returned expected values that are correct). It reports
Wilson 95% confidence intervals for those binomial rates, category and
field-level scores, failure causes, median/p95 observed latency, and reported
cost per correct field when cost data is present. These metrics must be read
with the sample size, source permission, date, engine version, rendering mode,
and failure causes; a score on one owned fixture is not evidence of universal
site coverage or a production SLA.

To compare engines fairly, run the same signed URLs, expected fields, source
freshness window, and permission scope. Preserve each engine's raw output and
receipt separately. Report blocked/challenged pages as failures in reach, not
as inaccurate extracted fields. Refresh gold values when source pages change
and issue a newly signed manifest version.

## Run an API comparison

`compare_engines.py` sends the same signed cases through one API at a time and
stores normalized run records plus raw responses. It checks the manifest HMAC
and exact hostname scope before sending requests, uses structured JSON
extraction with a schema derived from expected field types, disables cached
results, runs sequentially, does not retry, and does not print API keys. It
makes no requests unless `--allow-billable-requests` is supplied. Responses are
capped at 20 MiB, and existing output paths are never overwritten. Each API may
consume its own credits; a request is billable even if the target blocks it.
The script does not estimate USD cost or reconcile differences in provider
credit units. API keys are sent only to the matching official API host or a
loopback endpoint by default; a custom HTTPS host also requires
`--allow-custom-api-host`.

```powershell
$env:APEX_GOLDSET_SIGNING_KEY = '<load from your secret manager>'
$env:APEX_SCRAPER_API_KEY = '<load from your secret manager>'
$env:FIRECRAWL_API_KEY = '<load from your secret manager>'
python tests/benchmark/compare_engines.py apex path/to/owned-gold-set.json `
  --only case-1,case-2 --output work/benchmark/apex.json --allow-billable-requests
python tests/benchmark/compare_engines.py firecrawl path/to/owned-gold-set.json `
  --only case-1,case-2 --output work/benchmark/firecrawl.json --allow-billable-requests
python tests/benchmark/goldset_evaluator.py evaluate path/to/owned-gold-set.json `
  work/benchmark/apex.json --output work/benchmark/apex-score.json
python tests/benchmark/goldset_evaluator.py evaluate path/to/owned-gold-set.json `
  work/benchmark/firecrawl.json --output work/benchmark/firecrawl-score.json
```

Run only against cases for which current written permission is recorded in the
signed manifest. Keep keys in the operator's secret manager and artifacts in a
private workspace; raw responses may contain source text. The runner does not
create permission, a Firecrawl account, or budget approval. Review each API's
current billing and terms before a run.
