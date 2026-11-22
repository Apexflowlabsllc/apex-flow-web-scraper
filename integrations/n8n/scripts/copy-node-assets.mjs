import { copyFile, mkdir } from 'node:fs/promises';

const output = new URL('../dist/nodes/ApexFlow/', import.meta.url);
await mkdir(output, { recursive: true });
await copyFile(
	new URL('../nodes/ApexFlow/ApexFlow.node.json', import.meta.url),
	new URL('../dist/nodes/ApexFlow/ApexFlow.node.json', import.meta.url),
);
