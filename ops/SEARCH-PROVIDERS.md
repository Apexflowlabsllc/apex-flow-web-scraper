# Whole-web search provider setup

The hosted search endpoint uses one server-side provider for each request. Supported adapters are Brave Search, Tavily Search and Exa Search. Customer requests continue to use the same `/v1/websearch` contract and the same Apex credit accounting.

## Select a provider

Configure the provider key in the hosting dashboard's encrypted environment variable settings. Never put a provider key in source control, a customer-facing page, an MCP client config, a log, or a support ticket.

| Provider | Environment variable | Selector value |
| --- | --- | --- |
| Brave Search | `BRAVE_SEARCH_API_KEY` | `brave` |
| Tavily Search | `TAVILY_API_KEY` | `tavily` |
| Exa Search | `EXA_API_KEY` | `exa` |

If exactly one supported key is configured, it is selected automatically. If more than one key is present, set `APEX_SEARCH_PROVIDER` to exactly one listed selector. If the selector is invalid, its key is missing, or multiple keys exist without a selector, the service keeps web search disabled and MCP omits `web_search`. This avoids silently choosing a provider and incurring its charges.

## Verify the service after configuration

1. Use the hosting dashboard to add the selected provider key and, when required, `APEX_SEARCH_PROVIDER` to the correct environment. Do not paste values into repository files or the terminal.
2. Redeploy through the normal reviewed release process; environment changes alone do not prove the running service has loaded them.
3. Confirm `web_search` appears in authenticated `/v1/mcp` `tools/list` output.
4. Send one low-result-count request to `/v1/websearch` and verify valid HTTPS result URLs, normalized `title`/`snippet` fields, and the existing 70-credit charge. Confirm an upstream failure refunds the reservation.
5. Review the selected provider's current commercial terms, rate limits, and account-level spending controls before enabling paid traffic. Provider charges are separate from Apex credits and are incurred by the operator account.

Do not enable a second provider just for automatic failover. The current implementation deliberately makes a single deterministic provider call per search; automatic failover can incur duplicate upstream charges after ambiguous timeout failures.
