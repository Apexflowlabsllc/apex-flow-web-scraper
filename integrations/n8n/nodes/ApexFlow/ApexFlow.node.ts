import type {
	IExecuteFunctions,
	IDataObject,
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

export function buildRequestPlan(operation: Operation, params: Record<string, unknown>): RequestPlan {
	const jsonObject = (value: unknown, label: string): Record<string, unknown> | undefined => {
		if (value === undefined || value === null || value === '') return undefined;
		let parsed = value;
		if (typeof parsed === 'string') {
			try {
				parsed = JSON.parse(parsed);
			} catch {
				throw new Error(`${label} must be valid JSON.`);
			}
		}
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(`${label} must be a JSON object.`);
		return parsed as Record<string, unknown>;
	};
	switch (operation) {
		case 'scrape': {
			const url = String(params.url ?? '').trim();
			if (!url) throw new Error('URL is required.');
			const body: Record<string, unknown> = { url, ...jsonObject(params.options, 'Options') };
			return { method: 'POST', path: '/scrape', body };
		}
		case 'map': {
			const url = String(params.url ?? '').trim();
			if (!url) throw new Error('URL is required.');
			const body: Record<string, unknown> = { url, limit: Number(params.mapLimit ?? params.limit ?? 1000) };
			const search = String(params.search ?? '').trim();
			if (search) body.search = search;
			return { method: 'POST', path: '/map', body };
		}
		case 'webSearch': {
			const query = String(params.query ?? '').trim();
			if (!query) throw new Error('Search query is required.');
			return { method: 'POST', path: '/websearch', body: { query, limit: Number(params.resultLimit ?? params.limit ?? 10) } };
		}
		case 'startCrawl': {
			const url = String(params.url ?? '').trim();
			if (!url) throw new Error('URL is required.');
			return {
				method: 'POST',
				path: '/crawl',
				body: { url, limit: Number(params.pageLimit ?? params.limit ?? 25), ...jsonObject(params.options, 'Options') },
			};
		}
		case 'getCrawlStatus':
		case 'cancelCrawl': {
			const id = String(params.id ?? '').trim();
			if (!id) throw new Error('Crawl ID is required.');
			return {
				method: operation === 'getCrawlStatus' ? 'GET' : 'DELETE',
				path: `/crawl/${encodeURIComponent(id)}`,
				...(operation === 'getCrawlStatus' ? { query: { offset: Number(params.offset ?? 0) } } : {}),
			};
		}
		case 'batchScrape': {
			let urls: unknown;
			try {
				urls = typeof params.urls === 'string' ? JSON.parse(params.urls) : params.urls;
			} catch {
				throw new Error('URLs must be valid JSON: an array of URL strings.');
			}
			if (!Array.isArray(urls) || urls.length === 0 || urls.some((url) => typeof url !== 'string' || !url.trim())) {
				throw new Error('URLs must be a non-empty array of URL strings.');
			}
			const body: Record<string, unknown> = { urls, ...((jsonObject(params.options, 'Options') ?? {}) as Record<string, unknown>) };
			return { method: 'POST', path: '/v2/batch/scrape', body };
		}
		default:
			throw new Error(`Unsupported Apex operation: ${String(operation)}`);
	}
}

export class ApexFlow implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Apex Flow Web Scraper',
		name: 'apexFlow',
		icon: 'fa:globe',
		group: ['transform'],
		version: 1,
		description: 'Scrape pages, map sites, search the web, and run durable crawl or batch jobs.',
		defaults: { name: 'Apex Flow Web Scraper' },
		inputs: ['main'],
		outputs: ['main'],
		credentials: [{ name: 'apexFlowApi', required: true }],
		properties: [
			{
				displayName: 'Resource',
				name: 'resource',
				type: 'options',
				options: [{ name: 'Page and Site', value: 'pageSite' }, { name: 'Crawl', value: 'crawl' }, { name: 'Batch', value: 'batch' }],
				default: 'pageSite',
			},
			{ displayName: 'Operation', name: 'operation', type: 'options', options: [{ name: 'Scrape Page', value: 'scrape', action: 'Scrape a web page' }, { name: 'Map Site', value: 'map', action: 'Find URLs on a website' }, { name: 'Search the Web', value: 'webSearch', action: 'Search the public web' }], default: 'scrape', displayOptions: { show: { resource: ['pageSite'] } } },
			{ displayName: 'Operation', name: 'operation', type: 'options', options: [{ name: 'Start Crawl', value: 'startCrawl', action: 'Start an asynchronous site crawl' }, { name: 'Get Crawl Status', value: 'getCrawlStatus', action: 'Get crawl status and results' }, { name: 'Cancel Crawl', value: 'cancelCrawl', action: 'Cancel an active crawl' }], default: 'startCrawl', displayOptions: { show: { resource: ['crawl'] } } },
			{ displayName: 'Operation', name: 'operation', type: 'options', options: [{ name: 'Start Batch Scrape', value: 'batchScrape', action: 'Start an asynchronous batch scrape' }], default: 'batchScrape', displayOptions: { show: { resource: ['batch'] } } },
			{ displayName: 'URL', name: 'url', type: 'string', required: true, default: '', displayOptions: { show: { operation: ['scrape', 'map', 'startCrawl'] } } },
			{ displayName: 'Search Query', name: 'query', type: 'string', required: true, default: '', displayOptions: { show: { operation: ['webSearch'] } } },
			{ displayName: 'Crawl ID', name: 'id', type: 'string', required: true, default: '', displayOptions: { show: { operation: ['getCrawlStatus', 'cancelCrawl'] } } },
			{ displayName: 'Offset', name: 'offset', type: 'number', default: 0, displayOptions: { show: { operation: ['getCrawlStatus'] } } },
			{ displayName: 'Limit', name: 'mapLimit', type: 'number', default: 1000, displayOptions: { show: { operation: ['map'] } } },
			{ displayName: 'Search Filter', name: 'search', type: 'string', default: '', displayOptions: { show: { operation: ['map'] } } },
			{ displayName: 'Result Limit', name: 'resultLimit', type: 'number', default: 10, displayOptions: { show: { operation: ['webSearch'] } } },
			{ displayName: 'Page Limit', name: 'pageLimit', type: 'number', default: 25, displayOptions: { show: { operation: ['startCrawl'] } } },
			{ displayName: 'URLs (JSON)', name: 'urls', type: 'json', required: true, default: '["https://example.com"]', displayOptions: { show: { operation: ['batchScrape'] } }, description: 'An array of URLs. Maximum count follows your Apex plan.' },
			{ displayName: 'Options (JSON)', name: 'options', type: 'json', default: '{}', displayOptions: { show: { operation: ['scrape', 'startCrawl', 'batchScrape'] } }, description: 'Scrape options supported by the selected endpoint.' },
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const returnData: INodeExecutionData[] = [];
		for (let i = 0; i < items.length; i++) {
			const operation = this.getNodeParameter('operation', i) as Operation;
			const params: Record<string, unknown> = {};
			for (const name of ['url', 'query', 'id', 'offset', 'limit', 'mapLimit', 'resultLimit', 'pageLimit', 'search', 'urls', 'options']) {
				try {
					params[name] = this.getNodeParameter(name, i);
				} catch {
					// Parameters are operation-specific and hidden in the editor.
				}
			}
			try {
				const request = buildRequestPlan(operation, params);
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
				throw error;
			}
		}
		return [returnData];
	}
}
