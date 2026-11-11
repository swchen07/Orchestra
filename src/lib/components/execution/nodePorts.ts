import type { GraphNode, GraphRef, WorkflowGraph } from '$lib/apis/execution';
import { capabilitySchema } from './nodeProgram';

export type OutputField = { pointer: string; name: string; type: string; description: string };
export const fieldTypes: Record<string, string> = {
	string: 'Text',
	integer: 'Whole number',
	number: 'Number',
	boolean: 'Yes / no',
	object: 'Object',
	array: 'List',
	any: 'Any value'
};
const escapeKey = (key: string) => key.replaceAll('~', '~0').replaceAll('/', '~1');

export function outputFields(node: GraphNode): OutputField[] {
	const schema = (node.output_schema ?? capabilitySchema(node.op)) as
		| Record<string, any>
		| undefined;
	const result: OutputField[] = [];
	function visit(properties: Record<string, any>, path = '', label = '', depth = 0) {
		if (depth > 6) return;
		for (const [key, property] of Object.entries(properties)) {
			const pointer = path + '/' + escapeKey(key),
				name = label ? label + '.' + key : key;
			result.push({
				pointer,
				name,
				type: property.type ?? 'any',
				description: property.description ?? ''
			});
			if (property.properties) visit(property.properties, pointer, name, depth + 1);
		}
	}
	visit(schema?.properties ?? {});
	if (!['input', 'output'].includes(node.op) && !result.some((f) => f.pointer === '/report'))
		result.push({
			pointer: '/report',
			name: 'report',
			type: 'string',
			description: node.report_description ?? 'The complete reply returned by this module.'
		});
	if (['call', 'map', 'loop', 'workflow'].includes(node.op)) {
		result.push({
			pointer: '/__workspace',
			name: 'Workspace revision',
			type: 'object',
			description: 'Host-owned lease and actual isolation guarantees when coding is enabled.'
		});
		result.push({
			pointer: '/__artifacts',
			name: 'Published artifacts',
			type: 'array',
			description:
				'Immutable host-verified files and Git revisions; select these for evaluation, integration or downstream modules.'
		});
	}
	return result;
}

export function referenceFields(
	ref: GraphRef,
	graph: WorkflowGraph,
	seen: string[] = []
): OutputField[] {
	if (ref.source === 'input')
		return outputFields({
			id: '_public',
			op: 'input',
			inputs: {},
			output_schema: graph.input_schema ?? {
				type: 'object',
				properties: {
					task: { type: 'string', description: 'The current request and visible chat context.' }
				}
			}
		});
	if (ref.source === 'invocation')
		return [
			['/action', 'Caller Action', 'string', 'Registered caller Action, as data.'],
			[
				'/instruction',
				'Assigned responsibilities',
				'string',
				'Instructions selected by the caller node.'
			],
			['/inputs', 'Selected inputs', 'object', 'Only the caller’s declared input values.'],
			[
				'/output_schema',
				'Expected output contract',
				'any',
				'The caller host validates the returned object.'
			],
			[
				'/context',
				'Authorized Action context',
				'object',
				'Contains only context authorized for this caller Action.'
			],
			[
				'/context/prompt',
				'Planning or review packet',
				'string',
				'Available only for graph.plan or result.review; ordinary calls receive no control privilege.'
			],
			[
				'/context/feedback',
				'Correction feedback',
				'object',
				'Available when the caller host has issued corrections.'
			],
			[
				'/context/dispatch',
				'Allowed dispatch branches',
				'object',
				'Available only for tasks.dispatch.'
			]
		].map(([pointer, name, type, description]) => ({ pointer, name, type, description }));
	if (ref.source === 'report')
		return [
			{
				pointer: '/report',
				name: 'report',
				type: 'string',
				description: 'The saved reply from a previous module invocation.'
			}
		];
	if (ref.source === 'node') {
		const node = graph.nodes.find((n) => n.id === ref.node);
		return node ? outputFields(node) : [];
	}
	if (ref.source === 'output' && ref.port && !seen.includes(ref.port)) {
		const target = graph.outputs?.[ref.port];
		if (!target)
			return [
				{
					pointer: '',
					name: 'result',
					type: 'any',
					description: 'The result published by the Controller at runtime.'
				}
			];
		const fields = referenceFields(target, graph, [...seen, ref.port]);
		const root = target.pointer ?? '';
		if (!root) return fields;
		const field = fields.find((f) => f.pointer === root);
		return [
			{
				pointer: '',
				name: 'result',
				type: field?.type ?? 'any',
				description: field?.description ?? 'The selected graph result.'
			},
			...fields
				.filter((f) => f.pointer.startsWith(root + '/'))
				.map((f) => ({ ...f, pointer: f.pointer.slice(root.length) }))
		];
	}
	return [];
}

export function refForNode(node: GraphNode): GraphRef {
	return { source: 'node', node: node.id, pointer: outputFields(node)[0]?.pointer ?? '' };
}

export function inputSourceLabel(ref: GraphRef, graph: WorkflowGraph) {
	if (ref.source === 'node')
		return graph.nodes.find((n) => n.id === ref.node)?.label ?? ref.node ?? 'Choose source';
	return {
		input: 'Current chat request',
		output: 'Generated result: ' + (ref.port ?? ''),
		report: 'Saved report',
		literal: 'Fixed value',
		invocation: 'Caller invocation'
	}[ref.source];
}
