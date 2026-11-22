import type { ICredentialType, INodeProperties } from 'n8n-workflow';

export class ApexFlowApi implements ICredentialType {
	name = 'apexFlowApi';
	displayName = 'Apex Flow Web Scraper API';
	documentationUrl = 'https://webscraper.apexflowlabs.com/docs.html#api';
	properties: INodeProperties[] = [
		{
			displayName: 'API Key',
			name: 'apiKey',
			type: 'string',
			typeOptions: { password: true },
			default: '',
			required: true,
		},
	];
	authenticate = {
		type: 'generic' as const,
		properties: {
			headers: {
				Authorization: '=Bearer {{$credentials.apiKey}}',
			},
		},
	};
	test = {
		request: {
			baseURL: 'https://webscraper.apexflowlabs.com/v1',
			url: '/usage',
			method: 'GET' as const,
		},
	};
}
