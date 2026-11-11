import { expect, it } from 'vitest';
import type { GraphNode, WorkflowGraph } from '$lib/apis/execution';
import { outputFields, referenceFields, refForNode } from './nodePorts';
import { nodeActions, nodeOperations } from './nodeProgram';

const node: GraphNode = {
	id: 'source',
	op: 'call',
	inputs: {},
	output_schema: {
		type: 'object',
		properties: {
			amount: { type: 'integer', description: 'Amount in cents' },
			nested: {
				type: 'object',
				properties: { 'a/b': { type: 'string', description: 'Escaped field' } }
			},
			documents: { type: 'array', items: { type: 'string' } }
		}
	}
};
const graph: WorkflowGraph = {
	nodes: [node],
	edges: [],
	result_node: 'source',
	outputs: { candidate: { source: 'node', node: 'source', pointer: '/amount' } }
};
it('selectable fields retain names, types, meanings and escaped nested paths', () => {
	const fields = outputFields(node);
	expect(fields.find((f) => f.pointer === '/amount')).toMatchObject({
		name: 'amount',
		type: 'integer',
		description: 'Amount in cents'
	});
	expect(fields.find((f) => f.pointer === '/nested/a~1b')).toMatchObject({
		name: 'nested.a/b',
		description: 'Escaped field'
	});
	expect(fields.find((f) => f.pointer === '/documents')?.type).toBe('array');
});
it('switching source uses a real declared field rather than retaining the old path', () => {
	expect(refForNode(node)).toEqual({ source: 'node', node: 'source', pointer: '/amount' });
	const plain: GraphNode = {
		id: 'plain',
		op: 'call',
		inputs: {},
		report_description: 'Independent answer with arithmetic.'
	};
	expect(outputFields(plain)[0]).toMatchObject({
		pointer: '/report',
		description: plain.report_description
	});
	expect(refForNode(plain).pointer).toBe('/report');
});
it('generated result aliases preserve scalar meaning and unknown sources invent no fields', () => {
	expect(referenceFields({ source: 'output', port: 'candidate' }, graph)).toEqual([
		{ pointer: '', name: 'result', type: 'integer', description: 'Amount in cents' }
	]);
	expect(referenceFields({ source: 'node', node: '' }, graph)).toEqual([]);
});
it('the visual action labels use the same registered catalog as the backend', () => {
	for (const action of nodeActions) {
		expect(nodeOperations[action.operation as GraphNode['op']]).toBe(action.label);
		expect(action.context_rule).toBeTruthy();
	}
	expect(nodeActions.some((a) => a.id === 'native.handoff')).toBe(false);
});
