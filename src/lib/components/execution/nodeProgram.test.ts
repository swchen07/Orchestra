import { expect, it } from 'vitest';
import type { WorkflowGraph, WorkflowRun } from '$lib/apis/execution';
import {
	dataEdges,
	synchronizeGraph,
	outputNode,
	hasPlanning,
	capabilitySchema,
	setDependency,
	dependencyChoices,
	consumers,
	synchronizeBoundaryPorts,
	feedbackTargetChoices
} from './nodeProgram';
import { definitionGraph, graphChanges, planGraph } from './workflowView';

function graph(): WorkflowGraph {
	return {
		schema_version: '2.0',
		nodes: [
			{
				id: 'plan',
				op: 'plan',
				binding: 'planner',
				inputs: { goal: { source: 'input', pointer: '/task' } },
				planning: { output_port: 'candidate' }
			},
			{ id: 'a', op: 'call', binding: 'worker', inputs: {} },
			{ id: 'b', op: 'call', binding: 'worker', inputs: {} },
			{
				id: 'review',
				op: 'review',
				binding: 'critic',
				inputs: { candidate: { source: 'output', port: 'candidate', pointer: '' } }
			}
		],
		edges: [],
		outputs: {
			candidate: { source: 'node', node: 'a', pointer: '/report' },
			answer: { source: 'node', node: 'review', pointer: '/answer' }
		},
		result_node: 'review'
	};
}

it('renders bounded feedback separately while preserving forward layout and source choices', () => {
	const value: WorkflowGraph = {
		schema_version: '2.0',
		result_node: 'review',
		nodes: [
			{ id: 'dispatch', op: 'dispatch', inputs: {}, dispatch: { targets: ['work'] } },
			{
				id: 'work',
				op: 'call',
				inputs: { assignment: { source: 'node', node: 'dispatch', pointer: '/assignment' } }
			},
			{
				id: 'review',
				op: 'review',
				inputs: { report: { source: 'node', node: 'work', pointer: '/report' } },
				review: { repair_node: 'dispatch', max_repairs: 3 }
			}
		],
		edges: []
	};
	const compiled = synchronizeGraph(value);
	expect(feedbackTargetChoices(compiled, 'review').map((n) => n.id)).toEqual(['dispatch', 'work']);
	expect(compiled.edges.filter((e) => e.on === 'revise')).toEqual([
		{ from: 'review', to: 'dispatch', on: 'revise', generated: true }
	]);
	expect(synchronizeGraph(compiled).edges).toEqual(compiled.edges);
	expect(dependencyChoices(compiled, 'work').map((n) => n.id)).toEqual(['dispatch']);
	for (const view of [definitionGraph(compiled, []), planGraph(compiled)]) {
		const edge = view.edges.find((e) => e.type === 'feedback');
		expect(edge?.data?.limit).toBe(3);
		expect(edge?.source).toBe('node:review');
		expect(edge?.target).toBe('node:dispatch');
		expect(view.nodes.find((n) => n.id === 'node:work')!.position.x).toBeGreaterThan(
			view.nodes.find((n) => n.id === 'node:dispatch')!.position.x
		);
		expect(view.nodes.find((n) => n.id === 'node:review')!.position.x).toBeGreaterThan(
			view.nodes.find((n) => n.id === 'node:work')!.position.x
		);
	}
	const exhausted = planGraph(compiled, {
		id: 'exhausted',
		created_at: 1,
		calls: [],
		status: 'failed',
		review_attempts: Array.from({ length: 4 }, () => ({
			node_id: 'review',
			status: 'changes_requested',
			revision: 1
		}))
	} as WorkflowRun);
	expect(exhausted.edges.find((e) => e.type === 'feedback')?.data?.used).toBe(3);
});

it('named ports compile the same input dependencies for saved and generated graphs', () => {
	const value = graph();
	expect(hasPlanning(value)).toBe(true);
	expect(dataEdges(value)).toEqual([{ from: 'a', to: 'review', on: 'success', generated: true }]);
	value.nodes = value.nodes.filter((n) => n.op !== 'plan');
	expect(hasPlanning(value)).toBe(false);
	expect(dataEdges(value)).toEqual([{ from: 'a', to: 'review', on: 'success', generated: true }]);
});
it('retargeting a graph port replaces generated edges without retaining stale dependencies', () => {
	let value = synchronizeGraph(graph());
	value.outputs!.candidate = { source: 'node', node: 'b', pointer: '/report' };
	value = synchronizeGraph(value);
	expect(value.edges).toEqual([{ from: 'b', to: 'review', on: 'success', generated: true }]);
	expect(outputNode(value)).toBe('review');
});
it('graph edits send output changes instead of a workflow-specific action', () => {
	const before = graph(),
		after = graph();
	after.outputs!.candidate = { source: 'node', node: 'b', pointer: '/report' };
	expect(graphChanges(before, after)).toEqual([
		{ op: 'set_output', port: 'candidate', ref: after.outputs!.candidate }
	]);
});
it('unbound planned ports show their producer without inventing an executable placeholder', () => {
	const value = graph();
	delete value.outputs!.candidate;
	value.nodes = value.nodes.filter((n) => ['plan', 'review'].includes(n.op));
	const view = definitionGraph(value, []);
	expect(view.nodes.filter((n) => n.data.node_id).map((n) => n.data.node_id)).toEqual([
		'plan',
		'review'
	]);
	expect(
		view.edges.some(
			(e) =>
				e.source === 'node:plan' &&
				e.target === 'node:review' &&
				e.label === 'candidate · generated work'
		)
	).toBe(true);
	expect(view.nodes.every((n) => !('status' in n.data))).toBe(true);
});
it('planning and review output contracts are capability contracts, independent of role labels', () => {
	expect(capabilitySchema('plan')?.required).toEqual(['action']);
	expect(capabilitySchema('review')?.required).toContain('answer');
	expect(capabilitySchema('call')).toBeUndefined();
});

it('ordering dependencies do not silently copy or delete data bindings', () => {
	const value = graph();
	const before = structuredClone(value.nodes);
	const ordered = setDependency(value, 'b', 'a', true);
	expect(ordered.nodes).toEqual(before);
	expect(ordered.edges.some((e) => e.from === 'a' && e.to === 'b' && !e.generated)).toBe(true);
	expect(setDependency(ordered, 'b', 'a', false).nodes).toEqual(before);
	const dataBound = synchronizeGraph(value);
	expect(
		setDependency(dataBound, 'review', 'a', false).edges.some(
			(e) => e.from === 'a' && e.to === 'review'
		)
	).toBe(true);
});
it('predecessor choices exclude descendants and output consumers identify the local input', () => {
	const value = synchronizeGraph(graph());
	expect(dependencyChoices(value, 'a').map((n) => n.id)).not.toContain('review');
	expect(consumers(value, 'a')).toEqual([
		{ node: 'review', label: 'review', input: 'candidate', pointer: '' }
	]);
});
it('input and output boundaries use real selectable nodes and the same field dependencies', () => {
	const entry = synchronizeBoundaryPorts({
		id: 'entry',
		op: 'input',
		inputs: { task: { source: 'input', pointer: '/task' } }
	});
	const value: WorkflowGraph = {
		schema_version: '2.0',
		nodes: [
			entry,
			{
				id: 'work',
				op: 'call',
				binding: 'worker',
				inputs: { request: { source: 'node', node: 'entry', pointer: '/task' } }
			},
			synchronizeBoundaryPorts({
				id: 'exit',
				op: 'output',
				inputs: { answer: { source: 'node', node: 'work', pointer: '/report' } }
			})
		],
		edges: [],
		outputs: { answer: { source: 'node', node: 'exit', pointer: '/answer' } },
		result_node: 'exit'
	};
	const view = definitionGraph(synchronizeGraph(value), []);
	expect(view.nodes.map((n) => n.id)).toEqual(['node:entry', 'node:work', 'node:exit']);
	expect(view.edges.map((e) => e.label)).toEqual(['task', 'report']);
	expect((entry.output_schema as any).properties.task.type).toBe('string');
});
