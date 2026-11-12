// Fixed upstream: customer URLs are never used as proxy destinations.
module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  const route = req.query.route;
  const allowed = new Set(["api/status", "api/checkout", "api/claim", "api/account", "api/portal", "api/event", "api/trial"]);
  if (!allowed.has(route)) return res.status(404).json({error: "Not found"});
  if (!process.env.SCRAPER_GATEWAY_SECRET) return res.status(503).json({error: "Service is being configured. Please retry shortly."});
  try {
    const headers = {
      "Content-Type": "application/json",
      "X-Scraper-Gateway": process.env.SCRAPER_GATEWAY_SECRET,
      "X-Real-IP": String(req.headers["x-vercel-forwarded-for"] || req.headers["x-forwarded-for"] || "unknown").split(",")[0].trim(),
    };
    for (const name of ["authorization", "cookie", "origin"]) if (req.headers[name]) headers[name] = req.headers[name];
    const upstream = await fetch("https://webscraper-api.159.223.109.53.nip.io/" + route, {
      method: req.method, headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : typeof req.body === "string" ? req.body : JSON.stringify(req.body),
      signal: AbortSignal.timeout(95000), redirect: "error",
    });
    for (const name of ["content-type", "set-cookie", "retry-after", "referrer-policy"]) {
      const value = upstream.headers.get(name);
      if (value) res.setHeader(name, value);
    }
    res.status(upstream.status).send(await upstream.text());
  } catch {
    res.status(502).json({error: "The service is temporarily unavailable. Please retry."});
  }
};
