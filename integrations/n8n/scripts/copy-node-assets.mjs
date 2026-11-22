import { copyFile, mkdir } from 'node:fs/promises';

const output = new URL('../dist/nodes/ApexFlow/', import.meta.url);
const credentialsOutput = new URL('../dist/credentials/', import.meta.url);
await mkdir(output, { recursive: true });
await mkdir(credentialsOutput, { recursive: true });
await copyFile(
	new URL('../nodes/ApexFlow/ApexFlow.node.json', import.meta.url),
	new URL('../dist/nodes/ApexFlow/ApexFlow.node.json', import.meta.url),
);
for (const icon of ['apex-flow.svg', 'apex-flow.dark.svg']) {
	await copyFile(new URL(`../nodes/ApexFlow/${icon}`, import.meta.url), new URL(`../dist/nodes/ApexFlow/${icon}`, import.meta.url));
}
await copyFile(new URL('../credentials/apex-flow.svg', import.meta.url), new URL('../dist/credentials/apex-flow.svg', import.meta.url));
