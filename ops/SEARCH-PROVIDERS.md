# Whole-web search provider setup

The hosted search endpoint uses one server-side provider for each request. Supported adapters are Brave Search, Tavily Search and Exa Search. Customer requests continue to use the same `/v1/websearch` contract and Apex credits.

## Select a provider

Configure the provider key in the hosting dashboard's encrypted environment variable settings. Never put a provider key in source control, a customer-facing page, an MCP client config, a log, or a support ticket.

| Provider | Environment variable | Selector value |
| --- | --- | --- |
| Brave Search | `BRAVE_SEARCH_API_KEY` | `brave` |
| Tavily Search | `TAVILY_API_KEY` | `tavily` |
| Exa Search | `EXA_API_KEY` | `exa` |

## Credit pricing and provider costs

Current default successful-request charges:

| Provider | Apex credit charge |
| --- | ---: |
| Brave Search | 130 credits per successful request |
| Tavily Search | 210 credits for basic search |
| Exa Search | 180 credits for up to 10 requested results, plus 25 credits per requested result above 10 |

Successful searches with zero results are charged because the upstream service still bills the search request. Failed provider calls refund the reservation. Exa pricing is based on the requested `numResults`, not the number of results actually returned, so a successful request is charged at its full requested-result cap.

Defaults are sized at roughly 2x current public direct API pricing at Apex's lowest per-credit Scale plan: Brave lists $5/1,000 search requests, Tavily lists $0.008 per API credit and basic search uses one credit, and Exa's `auto` search lists $7/1,000 requests for up to 10 results plus $1/1,000 for each requested result above 10. This leaves roughly half of the allocated credit revenue for direct provider cost, before hosting, payment fees, support and taxes; it is not a net-margin guarantee. Exa's pricing page says its extra-result charge is based on requested result count. Pricing sources checked 2026-11-22: [Brave pricing](https://api-dashboard.search.brave.com/documentation/pricing), [Tavily pricing](https://www.tavily.com/pricing) and [Exa pricing](https://exa.ai/pricing).

Credit values can be adjusted using `APEX_SEARCH_BRAVE_CREDITS`, `APEX_SEARCH_TAVILY_CREDITS`, `APEX_SEARCH_EXA_BASE_CREDITS` and `APEX_SEARCH_EXA_EXTRA_RESULT_CREDITS`. These are integer credit amounts, not secrets. Before lowering a default, check the actual provider account's current rate and recalculate contribution at the lowest revenue-per-credit plan. Invalid/non-positive values keep that provider disabled. Re-check public pricing and contracted rates before production enablement.

If exactly one supported key is configured, it is selected automatically. If more than one key is present, set `APEX_SEARCH_PROVIDER` to exactly one listed selector. If the selector is invalid, its key is missing, or multiple keys exist without a selector, the service keeps web search disabled and MCP omits `web_search`. This avoids silently choosing a provider and incurring its charges.

## Verify the service after configuration

1. Use the hosting dashboard to add the selected provider key and, when required, `APEX_SEARCH_PROVIDER` to the correct environment. Do not paste values into repository files or the terminal.
2. Redeploy through the normal reviewed release process; environment changes alone do not prove the running service has loaded them.
3. Confirm `web_search` appears in authenticated `/v1/mcp` `tools/list` output.
4. Send one low-result-count request to `/v1/websearch` and verify valid HTTP(S) result URLs, normalized `title`/`snippet` fields, the provider-specific credit charge, and an upstream failure refund. Confirm an empty successful search is charged.
5. Review the selected provider's current commercial terms, rate limits, and account-level spending controls before enabling paid traffic. Provider charges are separate from Apex credits and are incurred by the operator account.

Do not enable a second provider just for automatic failover. The current implementation deliberately makes a single deterministic provider call per search; automatic failover can incur duplicate upstream charges after ambiguous timeout failures.
