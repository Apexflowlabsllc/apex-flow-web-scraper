import { NodeApiError, NodeConnectionTypes, NodeOperationError } from 'n8n-workflow';
import type {
	IExecuteFunctions,
	IDataObject,
	JsonObject,
	INode,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
} from 'n8n-workflow';

type Operation = 'scrape' | 'map' | 'webSearch' | 'startCrawl' | 'getCrawlStatus' | 'cancelCrawl' | 'batchScrape';

interface RequestPlan {
	method: 'GET' | 'POST' | 'DELETE';
	path: string;
	body?: Record<string, unknown>;
	query?: Record<string, string | number>;
}

export function buildRequestPlan(operation: Operation, params: Record<string, unknown>, node: INode): RequestPlan {
	const fail = (message: string): never => {
		throw new NodeOperationError(node, message);
	};
	const stringList = (value: unknown, label: string): string[] | undefined => {
		if (value === undefined || value === null || value === '') return undefined;
		if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
			fail(`${label} must be a list of text values.`);
		}
		return (value as string[]).map((item) => item.trim()).filter(Boolean);
	};
	const domainList = (value: unknown, label: string): string[] | undefined => {
		if (value === undefined || value === null || value === '') return undefined;
		const values = typeof value === 'string' ? value.split(/[\r\n,]+/) : value;
		if (!Array.isArray(values) || values.some((item) => typeof item !== 'string')) {
			fail(`${label} must be hostnames separated by commas or new lines.`);
		}
		const domains = [...new Set((values as string[]).map((item) => item.trim()).filter(Boolean))];
		if (domains.length < 1 || domains.length > 50) fail(`${label} must contain 1–50 hostnames.`);
		if (domains.some((domain) => domain.includes('/') || /[:?#@\s]/.test(domain))) fail(`${label} entries must be hostnames without protocol or path.`);
		return domains;
	};
	const scrapeOptions = (): Record<string, unknown> => {
		const body: Record<string, unknown> = {};
		const render = String(params.render ?? '').trim();
		if (render) body.render = render;
		const outputs = stringList(params.outputs, 'Outputs');
		if (outputs?.length) body.outputs = outputs;
		return body;
	};
	switch (operation) {
		case 'scrape': {
			const url = String(params.url ?? '').trim();
			if (!url) fail('URL is required.');
			const body: Record<string, unknown> = { url, ...scrapeOptions() };
			return { method: 'POST', path: '/scrape', body };
		}
		case 'map': {
			const url = String(params.url ?? '').trim();
			if (!url) fail('URL is required.');
			const body: Record<string, unknown> = { url, limit: Number(params.mapLimit ?? params.limit ?? 1000) };
			const search = String(params.search ?? '').trim();
			if (search) body.search = search;
			return { method: 'POST', path: '/map', body };
		}
		case 'webSearch': {
			const query = String(params.query ?? '').trim();
			if (!query) fail('Search query is required.');
			const body: Record<string, unknown> = { query, limit: Number(params.resultLimit ?? params.limit ?? 10) };
			const topic = String(params.searchTopic ?? 'general');
			if (topic !== 'general' && topic !== 'news' && topic !== 'images') fail('Search topic must be General, News, or Images.');
			if (topic !== 'general') body.topic = topic;
			const includeDomains = domainList(params.includeDomains, 'Include domains');
			const excludeDomains = domainList(params.excludeDomains, 'Exclude domains');
			if (includeDomains && excludeDomains) fail('Use include domains or exclude domains, not both.');
			if (includeDomains) body.include_domains = includeDomains;
			if (excludeDomains) body.exclude_domains = excludeDomains;
			return { method: 'POST', path: '/websearch', body };
		}
		case 'startCrawl': {
			const url = String(params.url ?? '').trim();
			if (!url) fail('URL is required.');
			return {
				method: 'POST',
				path: '/crawl',
				body: { url, limit: Number(params.pageLimit ?? params.limit ?? 25), ...scrapeOptions() },
			};
		}
		case 'getCrawlStatus':
		case 'cancelCrawl': {
			const id = String(params.id ?? '').trim();
			if (!id) fail('Crawl ID is required.');
			return {
				method: operation === 'getCrawlStatus' ? 'GET' : 'DELETE',
				path: `/crawl/${encodeURIComponent(id)}`,
				...(operation === 'getCrawlStatus' ? { query: { offset: Number(params.offset ?? 0) } } : {}),
			};
		}
		case 'batchScrape': {
			const urls = typeof params.urls === 'string'
				? params.urls.split(/[\r\n,]+/).map((url) => url.trim()).filter(Boolean)
				: params.urls;
			if (!Array.isArray(urls) || urls.length === 0 || urls.some((url) => typeof url !== 'string' || !url.trim())) {
				fail('Enter at least one URL, with one URL per line.');
			}
			const body: Record<string, unknown> = { urls };
			const formats = stringList(params.formats, 'Formats');
			if (formats?.length) body.formats = formats;
			return { method: 'POST', path: '/v2/batch/scrape', body };
		}
		default:
			return fail(`Unsupported Apex operation: ${String(operation)}`);
	}
}

export class ApexFlow implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Apex Flow Web Scraper',
		name: 'apexFlow',
		icon: { light: 'file:apex-flow.svg', dark: 'file:apex-flow.dark.svg' },
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description: 'Scrape pages, map sites, search the web, and run durable crawl or batch jobs. Requests use plan credits',
		defaults: { name: 'Apex Flow Web Scraper' },
		usableAsTool: true,
		inputs: [NodeConnectionTypes.Main],
		outputs: [NodeConnectionTypes.Main],
		credentials: [{ name: 'apexFlowApi', required: true }],
		properties: [
			{
				displayName: 'Resource',
				name: 'resource',
				type: 'options',
				noDataExpression: true,
				options: [{ name: 'Page and Site', value: 'pageSite' }, { name: 'Crawl', value: 'crawl' }, { name: 'Batch', value: 'batch' }],
				default: 'pageSite',
			},
			{ displayName: 'Operation', name: 'operation', type: 'options', noDataExpression: true, options: [{ name: 'Scrape', value: 'scrape', action: 'Scrape a web page' }, { name: 'Map', value: 'map', action: 'Map a site' }, { name: 'Search', value: 'webSearch', action: 'Search the public web' }], default: 'scrape', displayOptions: { show: { resource: ['pageSite'] } } },
			{ displayName: 'Operation', name: 'operation', type: 'options', noDataExpression: true, options: [{ name: 'Start', value: 'startCrawl', action: 'Start a site crawl' }, { name: 'Get Status', value: 'getCrawlStatus', action: 'Get crawl status and results' }, { name: 'Cancel', value: 'cancelCrawl', action: 'Cancel a crawl' }], default: 'startCrawl', displayOptions: { show: { resource: ['crawl'] } } },
			{ displayName: 'Operation', name: 'operation', type: 'options', noDataExpression: true, options: [{ name: 'Start', value: 'batchScrape', action: 'Start a batch scrape' }], default: 'batchScrape', displayOptions: { show: { resource: ['batch'] } } },
			{ displayName: 'URL', name: 'url', type: 'string', required: true, default: '', displayOptions: { show: { operation: ['scrape', 'map', 'startCrawl'] } } },
			{ displayName: 'Search Query', name: 'query', type: 'string', required: true, default: '', displayOptions: { show: { operation: ['webSearch'] } } },
			{ displayName: 'Crawl ID', name: 'id', type: 'string', required: true, default: '', displayOptions: { show: { operation: ['getCrawlStatus', 'cancelCrawl'] } } },
			{ displayName: 'Offset', name: 'offset', type: 'number', default: 0, displayOptions: { show: { operation: ['getCrawlStatus'] } } },
			{ displayName: 'Limit', name: 'mapLimit', type: 'number', default: 1000, displayOptions: { show: { operation: ['map'] } } },
			{ displayName: 'Search Filter', name: 'search', type: 'string', default: '', displayOptions: { show: { operation: ['map'] } } },
			{ displayName: 'Result Limit', name: 'resultLimit', type: 'number', default: 10, displayOptions: { show: { operation: ['webSearch'] } } },
			{ displayName: 'Search Topic', name: 'searchTopic', type: 'options', options: [{ name: 'General Web', value: 'general' }, { name: 'News', value: 'news' }, { name: 'Images', value: 'images' }], default: 'general', displayOptions: { show: { operation: ['webSearch'] } }, description: 'News requires Tavily or Exa; image search uses Brave’s dedicated image index' },
			{ displayName: 'Include Domains', name: 'includeDomains', type: 'string', typeOptions: { rows: 2 }, default: '', displayOptions: { show: { operation: ['webSearch'] } }, description: 'Optional hostnames, separated by commas or new lines. Cannot be used with Exclude Domains.' },
			{ displayName: 'Exclude Domains', name: 'excludeDomains', type: 'string', typeOptions: { rows: 2 }, default: '', displayOptions: { show: { operation: ['webSearch'] } }, description: 'Optional hostnames, separated by commas or new lines. Cannot be used with Include Domains.' },
			{ displayName: 'Page Limit', name: 'pageLimit', type: 'number', default: 25, displayOptions: { show: { operation: ['startCrawl'] } } },
			{ displayName: 'Render Mode', name: 'render', type: 'options', options: [{ name: 'Auto', value: 'auto' }, { name: 'Never', value: 'never' }, { name: 'Always', value: 'always' }], default: 'auto', displayOptions: { show: { operation: ['scrape', 'startCrawl'] } }, description: 'Rendered pages can use more credits' },
			{ displayName: 'Outputs', name: 'outputs', type: 'multiOptions', options: [{ name: 'Chunks', value: 'chunks' }, { name: 'Data', value: 'data' }, { name: 'Diff', value: 'diff' }, { name: 'Evidence', value: 'evidence' }, { name: 'HTML', value: 'html' }, { name: 'Links', value: 'links' }, { name: 'Markdown', value: 'markdown' }, { name: 'Raw HTML', value: 'raw_html' }, { name: 'Screenshot', value: 'screenshot' }, { name: 'Structured Data', value: 'structured_data' }, { name: 'Text', value: 'text' }], default: ['markdown'], displayOptions: { show: { operation: ['scrape', 'startCrawl'] } } },
			{ displayName: 'URLs', name: 'urls', type: 'string', typeOptions: { rows: 5 }, required: true, default: 'https://example.com', displayOptions: { show: { operation: ['batchScrape'] } }, description: 'Enter one URL per line. The maximum number of URLs follows your Apex plan.' },
			{ displayName: 'Formats', name: 'formats', type: 'multiOptions', options: [{ name: 'HTML', value: 'html' }, { name: 'Links', value: 'links' }, { name: 'Markdown', value: 'markdown' }, { name: 'Raw HTML', value: 'rawHtml' }, { name: 'Screenshot', value: 'screenshot' }], default: ['markdown'], displayOptions: { show: { operation: ['batchScrape'] } } },
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const returnData: INodeExecutionData[] = [];
		for (let i = 0; i < items.length; i++) {
			const operation = this.getNodeParameter('operation', i) as Operation;
			const params: Record<string, unknown> = {};
			for (const name of ['url', 'query', 'id', 'offset', 'limit', 'mapLimit', 'resultLimit', 'includeDomains', 'excludeDomains', 'pageLimit', 'search', 'urls', 'render', 'outputs', 'formats']) {
				try {
					params[name] = this.getNodeParameter(name, i);
				} catch {
					// Parameters are operation-specific and hidden in the editor.
				}
			}
			try {
				const request = buildRequestPlan(operation, params, this.getNode());
				const apiBase = request.path.startsWith('/v2/')
					? 'https://webscraper.apexflowlabs.com'
					: 'https://webscraper.apexflowlabs.com/v1';
				const apiUrl = apiBase + request.path;
				const result = await this.helpers.httpRequestWithAuthentication.call(this, 'apexFlowApi', {
					method: request.method,
					url: apiUrl,
					...(request.body ? { body: request.body } : {}),
					...(request.query ? { qs: request.query } : {}),
					json: true,
				});
				returnData.push({ json: result as IDataObject, pairedItem: { item: i } });
			} catch (error) {
				if (this.continueOnFail()) {
					returnData.push({ json: { error: error instanceof Error ? error.message : String(error) }, pairedItem: { item: i } });
					continue;
				}
				if (error instanceof NodeOperationError) {
					throw new NodeOperationError(this.getNode(), error.message);
				}
				const errorResponse = error instanceof Error ? { message: error.message } : error as JsonObject;
				throw new NodeApiError(this.getNode(), errorResponse as JsonObject);
			}
		}
		return [returnData];
	}
}
