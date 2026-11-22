# Apex Flow Web Scraper node for n8n

This community node connects n8n to the hosted Apex Flow Web Scraper API. It
stores the Apex API key in n8n credentials and provides page scraping, site
mapping, whole-web search, crawl lifecycle, and asynchronous batch creation.

## Install from a local checkout

This package has not been published to npm. Build it from this repository and
install the package into a self-hosted n8n instance using n8n's community node
installation instructions. Do not add a release workflow or publish without
the repository owner's release approval.

```bash
cd integrations/n8n
npm install
npm run build
npm test
npm pack
```

The resulting tarball can be installed as a private/custom node in a
self-hosted n8n instance by following n8n's current
[private node instructions](https://docs.n8n.io/connect/create-nodes/deploy-your-node/install-private-nodes/).
The package is not in npm or n8n's community-node catalog yet.
The package is marked private in `package.json` to prevent accidental npm publication.

## Configure credentials

Create an **Apex Flow Web Scraper API** credential and paste the API key from
your Apex account. n8n stores and sends it as a bearer token. Credentials are
tested with the low-cost `/v1/usage` endpoint. Scrape, search, and crawl actions
use Apex plan credits; check your plan before running large workflows.

## Operations

- Page and site: Scrape Page, Map Site, Search the Web.
- Crawl: Start Crawl, Get Crawl Status, Cancel Crawl.
- Batch: Start Batch Scrape with a JSON array of URLs.

Search requires an operator-configured hosted search provider. If it is not
configured, the API returns its normal actionable error. This package does not
silently switch to a different provider or expose local bring-your-own-key
search, which belongs in a local client process.

This initial community-node implementation has contract tests and TypeScript
checks, but the n8n UI and multi-version runtime behavior still need a local
n8n integration run before release or a verified-node submission. n8n's
verified-node path also requires its current CLI scaffold, lint and review;
those release checks have not been completed.
