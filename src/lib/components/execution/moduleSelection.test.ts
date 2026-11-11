import { describe, expect, it } from 'vitest';
import { bindModuleContract, bindingIssues } from './moduleSelection';
import { synchronizeGraph } from './nodeProgram';
import { referenceFields } from './nodePorts';
import type { GraphNode, WorkflowGraph } from '$lib/apis/execution';

const node: GraphNode = {
	id: 'stable',
	op: 'call',
	action: 'module.call',
	binding: 'worker',
	context_key: 'own',
	inputs: { question: { source: 'literal', value: false } }
};
const graph: WorkflowGraph = {
	schema_version: '2.0',
	nodes: [node],
	edges: [],
	result_node: 'stable',
	outputs: { answer: { source: 'node', node: 'stable', pointer: '/report' } }
};
const module = {
	id: 'workflow:test',
	name: 'Reusable',
	module_type: 'workflow',
	input_schema: {
		type: 'object',
		properties: { question: { type: 'boolean' } },
		required: ['question']
	},
	output_schema: { type: 'object', properties: { flag: { type: 'boolean' } }, required: ['flag'] }
};

describe('module selection contract', () => {
	it('preserves Action, instance key and existing falsy input mappings', () => {
		const selected = bindModuleContract({ ...node, binding: module.id }, module, graph);
		expect(selected.action).toBe('module.call');
		expect(selected.context_key).toBe('own');
		expect(selected.inputs.question).toEqual({ source: 'literal', value: false });
		expect(selected.output_schema).toEqual(module.output_schema);
		expect(node.output_schema).toBeUndefined();
	});
	it('keeps registered review output requirements and exposes incompatible bindings', () => {
		const review = {
			...node,
			op: 'review' as const,
			output_schema: {
				type: 'object',
				properties: { verdict: { type: 'string' } },
				required: ['verdict']
			}
		};
		const selected = bindModuleContract(review, module, graph);
		expect(selected.output_schema).toEqual(review.output_schema);
		expect(bindingIssues(selected, module).join(' ')).toContain('verdict');
	});
	it('keeps map item injection out of outer inputs', () => {
		const selected = bindModuleContract(
			{ ...node, op: 'map' },
			{
				...module,
				input_schema: {
					type: 'object',
					properties: { question: {}, item: {} },
					required: ['question', 'item']
				}
			},
			graph
		);
		expect(Object.keys(selected.inputs)).toEqual(['question']);
		expect((selected.input_schema as any).required).toEqual(['question']);
	});
	it('lists public input and host invocation fields with named meanings', () => {
		expect(
			referenceFields(
				{ source: 'input' },
				{ ...graph, input_schema: { type: 'object', properties: { count: { type: 'integer' } } } }
			)[0].pointer
		).toBe('/count');
		expect(
			referenceFields({ source: 'invocation' }, graph).find(
				(field) => field.pointer === '/context/prompt'
			)?.description
		).toContain('ordinary calls');
	});
	it('saves custom public output names and an explicit chat delivery port', () => {
		const exit: GraphNode = {
			id: 'exit',
			op: 'output',
			inputs: { count: { source: 'literal', value: 0 } },
			output_schema: {
				type: 'object',
				properties: { count: { type: 'integer' } },
				required: ['count']
			}
		};
		const saved = synchronizeGraph({
			...graph,
			nodes: [exit],
			delivery_port: 'count',
			outputs: { old: { source: 'node', node: 'exit', pointer: '/old' } }
		});
		expect(saved.outputs).toEqual({ count: { source: 'node', node: 'exit', pointer: '/count' } });
		expect(saved.delivery_port).toBe('count');
	});
});
