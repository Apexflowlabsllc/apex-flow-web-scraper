# Operator configuration

The engine can route public HTTP(S) fetches and isolated Brave subresources through one operator-controlled proxy. Configure the worker and local dashboard process with `RAINBO_SCRAPE_PROXY_URL` (for example, `http://proxy.example:3128`). Keep proxy credentials in the process environment; never place them in job JSON, recipes, MCP arguments, screenshots, support logs, or source control. Restart the engine after changing the setting. Health reports only whether routing is configured.

The engine checks each requested target host against its public-address policy before forwarding it. This is a routing option, not a supplied proxy fleet, residential IP product, CAPTCHA solver, or promise of access to protected sites. Respect target-site rules and use only accounts and proxy infrastructure you control.

Access challenges are surfaced as `access_challenge` errors and stop extraction. The current release does not provide an isolated signed-in profile or resume-after-human flow.

