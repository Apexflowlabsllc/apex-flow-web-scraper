// Dormant until two env vars exist: STRIPE_SECRET_KEY (the team's existing Stripe account) and CHECKOUT_OPEN=1.
// Creates a Stripe Checkout subscription session with inline price data; no product setup needed in Stripe.
const PLANS = {
  starter: { name: "Starter", cents: 1900, pages: "25,000 pages a month" },
  growth: { name: "Growth", cents: 7900, pages: "150,000 pages a month" },
  scale: { name: "Scale", cents: 29900, pages: "750,000 pages a month" },
};
const SITE = "https://apex-flow-web-scraper.vercel.app";

module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "POST only" });
  const origin = req.headers.origin;
  if (origin && origin !== SITE) return res.status(403).json({ error: "Open checkout from the pricing page." });
  if (process.env.CHECKOUT_OPEN !== "1" || !process.env.STRIPE_SECRET_KEY) {
    return res.status(503).json({ error: "Checkout is not open yet. No payment was taken." });
  }
  const plan = PLANS[(req.body && req.body.plan) || ""];
  if (!plan) return res.status(400).json({ error: "Unknown plan." });
  const form = new URLSearchParams({
    mode: "subscription",
    "line_items[0][quantity]": "1",
    "line_items[0][price_data][currency]": "usd",
    "line_items[0][price_data][unit_amount]": String(plan.cents),
    "line_items[0][price_data][recurring][interval]": "month",
    "line_items[0][price_data][product_data][name]": `Apex Flow Web Scraper ${plan.name}`,
    "line_items[0][price_data][product_data][description]": plan.pages,
    success_url: SITE + "/thanks.html",
    cancel_url: SITE + "/#pricing",
    "metadata[product]": "apex-flow-web-scraper",
    "metadata[plan]": (req.body.plan),
  });
  try {
    const r = await fetch("https://api.stripe.com/v1/checkout/sessions", {
      method: "POST",
      headers: { Authorization: "Bearer " + process.env.STRIPE_SECRET_KEY, "Content-Type": "application/x-www-form-urlencoded" },
      body: form,
    });
    const data = await r.json();
    if (!r.ok || !data.url) return res.status(502).json({ error: "Checkout could not start. No payment was taken." });
    return res.status(200).json({ url: data.url });
  } catch (e) {
    return res.status(502).json({ error: "Checkout could not start. No payment was taken." });
  }
};
