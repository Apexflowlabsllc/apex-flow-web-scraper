import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { buildRequestPlan, ApexFlow } from '../dist/nodes/ApexFlow/ApexFlow.node.js';
import { ApexFlowApi } from '../dist/credentials/ApexFlowApi.credentials.js';

test('scrape forwards supported root-level options', () => {
	assert.deepEqual(buildRequestPlan('scrape', {
		url: 'https://example.com', options: '{"render":true,"outputs":["markdown"]}',
	}), {
		method: 'POST', path: '/scrape', body: { url: 'https://example.com', render: true, outputs: ['markdown'] },
	});
});

test('map and web search build the hosted API payloads', () => {
	assert.deepEqual(buildRequestPlan('map', { url: 'https://example.com', limit: 15, search: 'pricing' }), {
		method: 'POST', path: '/map', body: { url: 'https://example.com', limit: 15, search: 'pricing' },
	});
	assert.deepEqual(buildRequestPlan('webSearch', { query: 'web scraping API', limit: 5 }), {
		method: 'POST', path: '/websearch', body: { query: 'web scraping API', limit: 5 },
	});
});

test('crawl lifecycle encodes IDs and uses numeric offsets', () => {
	assert.deepEqual(buildRequestPlan('startCrawl', { url: 'https://example.com', pageLimit: 5, options: { render: 'never' } }), {
		method: 'POST', path: '/crawl', body: { url: 'https://example.com', limit: 5, render: 'never' },
	});
	assert.deepEqual(buildRequestPlan('getCrawlStatus', { id: 'a/b', offset: '20' }), {
		method: 'GET', path: '/crawl/a%2Fb', query: { offset: 20 },
	});
	assert.deepEqual(buildRequestPlan('cancelCrawl', { id: 'crawl-1' }), {
		method: 'DELETE', path: '/crawl/crawl-1',
	});
});

test('batch uses the v2 route and accepts a JSON URL array', () => {
	assert.deepEqual(buildRequestPlan('batchScrape', {
		urls: '["https://example.com","https://example.org"]',
		options: { formats: ['markdown'] },
	}), {
		method: 'POST', path: '/v2/batch/scrape',
		body: { urls: ['https://example.com', 'https://example.org'], formats: ['markdown'] },
	});
});

test('required inputs and malformed JSON fail before sending a request', () => {
	assert.throws(() => buildRequestPlan('scrape', {}), /URL is required/);
	assert.throws(() => buildRequestPlan('webSearch', { query: ' ' }), /Search query is required/);
	assert.throws(() => buildRequestPlan('batchScrape', { urls: '{' }), /valid JSON/);
	assert.throws(() => buildRequestPlan('batchScrape', { urls: '[]' }), /non-empty array/);
	assert.throws(() => buildRequestPlan('scrape', { url: 'https://example.com', options: '[]' }), /JSON object/);
});

test('node execution keeps item links and sends authenticated requests to the correct API root', async () => {
	const node = new ApexFlow();
	const calls = [];
	const context = {
		getInputData: () => [{ json: {} }, { json: {} }],
		getNodeParameter: (name, index) => ({
			operation: 'batchScrape',
			urls: index === 0 ? '["https://a.example"]' : '["https://b.example"]',
		}[name]),
		helpers: {
			httpRequestWithAuthentication: async (credential, options) => {
				calls.push({ credential, options });
				return { success: true, id: `job-${calls.length}` };
			},
		},
		continueOnFail: () => false,
	};
	const [items] = await node.execute.call(context);
	assert.equal(items.length, 2);
	assert.deepEqual(items.map((item) => item.pairedItem.item), [0, 1]);
	assert.equal(calls[0].credential, 'apexFlowApi');
	assert.equal(calls[0].options.url, 'https://webscraper.apexflowlabs.com/v2/batch/scrape');
	assert.equal(calls[1].options.url, 'https://webscraper.apexflowlabs.com/v2/batch/scrape');
	assert.deepEqual(calls[0].options.body, { urls: ['https://a.example'] });
});

test('package builds the node catalog metadata at the path declared in package.json', async () => {
	const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
	const nodePath = pkg.n8n.nodes[0];
	const codexPath = nodePath.replace(/\.node\.js$/, '.node.json');
	const metadata = JSON.parse(await readFile(new URL(`../${codexPath}`, import.meta.url), 'utf8'));
	assert.equal(metadata.node, 'n8n-nodes-apex-flow-web-scraper.apexFlow');
});

test('credential stores a masked key and tests authentication without a scrape', () => {
	const credential = new ApexFlowApi();
	assert.deepEqual(credential.properties[0], {
		displayName: 'API Key', name: 'apiKey', type: 'string',
		typeOptions: { password: true }, default: '', required: true,
	});
	assert.equal(credential.authenticate.properties.headers.Authorization, '=Bearer {{$credentials.apiKey}}');
	assert.equal(credential.test.request.url, '/usage');
	assert.equal(credential.test.request.method, 'GET');
});
