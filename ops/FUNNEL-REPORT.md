# Private funnel report

The hosted scraper records daily aggregate events for `page_view`, `trial_view`, `trial_submit`, checkout creation and account activation. It does not store visitor identities in this report. Vercel Web Analytics is not enabled on the project, so this first-party report is the no-added-service-cost way to inspect the current funnel.

## Query

The owner-only endpoint is `GET /api/internal/funnel?days=30`. It accepts `days` from 1 to 365 and requires the existing `SCRAPER_GATEWAY_SECRET` in `X-Scraper-Gateway`. The handler returns 404 when the secret is unset or invalid. Responses are `no-store` and contain daily and per-plan event counts, plus same-window event-count ratios.

Run it only from a trusted process where `SCRAPER_GATEWAY_SECRET` is already available through the deployment's secret manager:

```powershell
$headers = @{ 'X-Scraper-Gateway' = $env:SCRAPER_GATEWAY_SECRET }
Invoke-RestMethod -Method Get `
  -Uri 'https://webscraper.apexflowlabs.com/api/internal/funnel?days=30' `
  -Headers $headers
```

Do not print, paste into chat, or commit the secret. These are event counts, not unique people or cohort conversions; repeat visits and automated traffic can inflate them. Treat the report as directional until visitor-level analytics are lawfully enabled and configured.
