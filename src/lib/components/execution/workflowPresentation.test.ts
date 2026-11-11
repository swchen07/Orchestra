import { describe, it, expect } from 'vitest';
import { roleViews } from './workflowView';
import type { WorkflowCall, WorkflowRun } from '$lib/apis/execution';
import { executionCycles, executionMap, statusAt, currentCallStatus } from './workflowPresentation';
import { summarizeUsage, usageLabel } from './workflowUsage';
const t = 1793260000000;
const call = (
	id: string,
	start: number,
	end: number,
	extra: Partial<WorkflowCall> = {}
): WorkflowCall => ({
	id,
	name: id,
	participant_id: id,
	input: 'task',
	status: 'completed',
	created_at: (t + start) / 1000,
	started_at: t + start,
	finished_at: (t + end) / 1000,
	...extra
});
const run = (): WorkflowRun => ({
	id: 'run',
	created_at: t / 1000,
	status: 'completed',
	finished_at: (t + 12000) / 1000,
	plan: { nodes: [], edges: [], result_node: 'done', controller: { binding: 'lead' } },
	calls: [
		call('d1', 0, 1000, {
			kind: 'decision',
			participant_id: 'lead',
			output: '{"action":"propose_plan"}'
		}),
		call('B', 1100, 2000, { depends_on: ['d1'] }),
		call('C', 1100, 10000, { depends_on: ['d1'] }),
		call('d2', 2100, 3000, {
			kind: 'decision',
			participant_id: 'lead',
			output: '{"action":"patch_plan"}'
		}),
		call('D', 3100, 4000, { depends_on: ['B', 'd2'] }),
		call('d3', 4100, 5000, {
			kind: 'decision',
			participant_id: 'lead',
			output: '{"action":"wait"}'
		}),
		call('d4', 10100, 11000, {
			kind: 'decision',
			participant_id: 'lead',
			output: '{"action":"finish"}'
		})
	]
});
describe('actual execution cycles', () => {
	it.each([
		['DSH', [6937, 6626, 6489, 8042, 7325, 7215, 9339]],
		['Claude Code', [2932, 2350, 2563, 3742, 2998, 3072, 4764]],
		['Hermes', [1079, 998, 1042, 1903, 1549, 1590, 2983]]
	] as const)('keeps %s coordinator usage separate from its workers', (_, totals) => {
		const record = run();
		record.template = { id: 'abc', name: 'ABC', lead_id: 'A' };
		record.plan = {
			nodes: ['assign', 'adjust', 'finish'].map((id) => ({
				id,
				op: 'call',
				label: `A · ${id}`,
				binding: 'A',
				inputs: {}
			})),
			edges: [],
			result_node: 'finish'
		};
		record.calls = [
			call('a1', 0, 1000, { participant_id: 'A', node_id: 'assign' }),
			call('b1', 1100, 2000, { participant_id: 'B', depends_on: ['a1'] }),
			call('c1', 1100, 3000, { participant_id: 'C', depends_on: ['a1'] }),
			call('a2', 3100, 4000, { participant_id: 'A', node_id: 'adjust', depends_on: ['b1', 'c1'] }),
			call('b2', 4100, 5000, { participant_id: 'B', depends_on: ['a2'] }),
			call('c2', 4100, 5500, { participant_id: 'C', depends_on: ['a2'] }),
			call('a3', 5600, 6500, { participant_id: 'A', node_id: 'finish', depends_on: ['b2', 'c2'] })
		];
		record.calls.forEach((call, i) => {
			call.usage = { prompt_tokens: totals[i] - 100, completion_tokens: 100 };
		});
		const graph = executionMap(record);
		const coordinator = graph.nodes.filter((node) => node.id.startsWith('step:'));
		expect(coordinator.map((node) => node.data.tokenTotal)).toEqual([
			totals[0],
			totals[3],
			totals[6]
		]);
		expect(coordinator.map((node) => node.data.tokenScope)).toEqual(['own', 'own', 'own']);
		expect(coordinator[0].data.tokens).toBe(usageLabel(summarizeUsage(record, [record.calls[0]])));
		expect(graph.nodes.find((node) => node.id === 'call:b1')?.data.tokenTotal).toBe(totals[1]);
		expect(graph.nodes.reduce((sum, node) => sum + Number(node.data.tokenTotal), 0)).toBe(
			summarizeUsage(record).total
		);
	});
	it('labels a structural stage total and keeps unreported coordinator usage unknown', () => {
		const record = run();
		record.calls = [call('B', 0, 1000, { usage: { prompt_tokens: 100, completion_tokens: 20 } })];
		const stage = executionMap(record).nodes.find((node) => node.id === 'step:defined')!;
		expect(stage.data).toMatchObject({ tokenScope: 'stage', tokenTotal: 120, tokens: '120' });
		const decision = call('A', 0, 1000, { kind: 'decision' });
		record.calls.unshift(decision);
		const coordinator = executionMap(record).nodes.find((node) => node.id === 'step:A')!;
		expect(coordinator.data).toMatchObject({ tokenScope: 'own', tokens: '—' });
		expect(coordinator.data.tokenTotal).toBeUndefined();
	});
	it('uses repeated coordinator calls as anchors in a defined graph without inventing AI decisions', () => {
		const record = run();
		record.template = { id: 'abc', name: 'ABC', lead_id: 'A' };
		record.plan = {
			nodes: [
				{ id: 'assign', op: 'call', label: 'A · Assign', binding: 'A', inputs: {} },
				{ id: 'adjust', op: 'call', label: 'A · Adjust', binding: 'A', inputs: {} }
			],
			edges: [],
			result_node: 'adjust'
		};
		record.calls = [
			call('a1', 0, 1000, { participant_id: 'A', node_id: 'assign' }),
			call('b1', 1100, 2000, { participant_id: 'B', depends_on: ['a1'] }),
			call('c1', 1100, 3000, { participant_id: 'C', depends_on: ['a1'] }),
			call('a2', 3100, 4000, { participant_id: 'A', node_id: 'adjust', depends_on: ['b1', 'c1'] }),
			call('b2', 4100, 5000, { participant_id: 'B', depends_on: ['a2'] })
		];
		const cycles = executionCycles(record);
		expect(cycles.map((c) => c.label)).toEqual(['A · Assign', 'A · Adjust']);
		expect(cycles.map((c) => c.calls.map((c) => c.id))).toEqual([['b1', 'c1'], ['b2']]);
		expect(record.calls.every((c) => c.kind !== 'decision')).toBe(true);
		expect(executionMap(record).nodes.filter((n) => n.id.startsWith('call:'))).toHaveLength(3);
		record.calls = record.calls.slice(0, 3);
		expect(executionCycles(record)[0].label).toBe('A · Assign');
		expect(executionCycles(record)[0].calls.map((c) => c.id)).toEqual(['b1', 'c1']);
	});
	it('groups B/C in cycle one and D in cycle two using dispatch dependencies', () => {
		const cycles = executionCycles(run());
		expect(cycles.map((c) => c.calls.map((call) => call.id))).toEqual([['B', 'C'], ['D'], [], []]);
		expect(cycles.map((c) => c.label)).toEqual(['Plan', 'Adjust', 'Observe', 'Finish']);
	});
	it('does not confuse plan revisions with controller decisions or invent rounds in static flows', () => {
		const record = run();
		record.calls = record.calls.filter((c) => c.kind !== 'decision');
		delete record.plan?.controller;
		record.plan_revisions = [
			{ revision: 8, at: t, reason: 'user edit', source: 'user', graph: record.plan! }
		];
		expect(executionCycles(record)).toHaveLength(1);
		expect(executionCycles(record)[0].label).toBe('Defined flow');
	});
	it('keeps final states when selecting a controller step after completion', () => {
		const record = run();
		const graph = executionMap(record, 'd2', t + 20000);
		expect(graph.nodes.find((n) => n.id === 'call:C')?.data).toMatchObject({
			status: 'completed'
		});
		expect(graph.nodes.find((n) => n.id === 'call:D')?.data.status).toBe('completed');
		expect(graph.nodes.find((n) => n.id === 'call:B')?.data.status).toBe('completed');
		expect(graph.nodes.some((n) => n.id === 'step:d3')).toBe(true);
	});
	it('renders each work call once, and only recorded work dependencies', () => {
		const record = run();
		const graph = executionMap(record);
		expect(graph.nodes.filter((n) => n.id.startsWith('call:'))).toHaveLength(3);
		expect(
			graph.edges.filter((e) => e.id.startsWith('dependency:')).map((e) => [e.source, e.target])
		).toEqual([['call:B', 'call:D']]);
		expect(graph.nodes.find((n) => n.id === 'call:D')!.position.x).toBeGreaterThan(
			graph.nodes.find((n) => n.id === 'call:B')!.position.x
		);
	});
	it('prefers the exact completed event time over coarse call timestamps', () => {
		const record = run();
		record.events = [
			{ seq: 1, at: t + 10050, type: 'call.completed', title: 'Completed', call_id: 'C' }
		];
		expect(statusAt(record, record.calls[2], t + 10025)).toBe('running');
		expect(statusAt(record, record.calls[2], t + 10051)).toBe('completed');
	});
	it('keeps repeated calls by one role separate and uses each frozen binding', () => {
		const record = run();
		record.calls[1].participant_id = 'same';
		record.calls[4].participant_id = 'same';
		record.calls[1].binding = { framework_id: 'codex', model_name: 'first' };
		record.calls[4].binding = { framework_id: 'hermes', model_name: 'second' };
		const graph = executionMap(record);
		expect(graph.nodes.find((n) => n.id === 'call:B')?.data.model).toBe('first');
		expect(graph.nodes.find((n) => n.id === 'call:D')?.data.model).toBe('second');
	});
});

describe('execution graph readability', () => {
	it('keeps a within-cycle dependency on a later row', () => {
		const record = run();
		record.calls = [call('A', 0, 1000), call('B', 1100, 2000, { depends_on: ['A'] })];
		const graph = executionMap(record);
		expect(graph.nodes.find((n) => n.id === 'call:B')!.position.y).toBeGreaterThan(
			graph.nodes.find((n) => n.id === 'call:A')!.position.y + 100
		);
	});
	it('keeps recorded data dependencies separate from controller dispatch and return edges', () => {
		const record = run();
		record.calls = [
			call('A', 0, 1000),
			call('B', 1100, 2000, { depends_on: ['A'] }),
			call('C', 2100, 3000, { depends_on: ['A', 'B'] })
		];
		const graph = executionMap(record);
		expect(
			graph.edges.filter((e) => e.id.startsWith('dependency:')).map((e) => [e.source, e.target])
		).toEqual([
			['call:A', 'call:B'],
			['call:A', 'call:C'],
			['call:B', 'call:C']
		]);
		expect(record.calls[2].depends_on).toEqual(['A', 'B']);
	});
});

it('returns each result to its dispatch owner even when later decisions already happened', () => {
	const record = run();
	const graph = executionMap(record, 'd3');
	expect(graph.edges.find((e) => e.id === 'return:C')).toMatchObject({
		source: 'call:C',
		target: 'step:d1'
	});
	expect(graph.edges.find((e) => e.id === 'dispatch:D')).toMatchObject({
		source: 'step:d2',
		target: 'call:D'
	});
	expect(graph.edges.filter((e) => e.id.startsWith('spine:'))).toHaveLength(3);
});
it('never draws a completed return for an active call', () => {
	const record = run();
	record.status = 'running';
	record.calls[2].status = 'running';
	record.calls[2].finished_at = undefined;
	const graph = executionMap(record);
	expect(graph.nodes.find((n) => n.id === 'call:C')?.data.status).toBe('running');
	expect(graph.edges.some((e) => e.id === 'return:C')).toBe(false);
});

it('uses the terminal event when a cached call still says running', () => {
	const record = run();
	const item = record.calls[1];
	item.status = 'running';
	record.events = [
		{ seq: 1, at: t + 2000, type: 'call.completed', title: 'Complete', call_id: item.id }
	];
	expect(currentCallStatus(record, item)).toBe('completed');
	expect(roleViews(record).find((r) => r.id === item.participant_id)?.status).toBe('completed');
});
it('marks an orphaned active call as interrupted, without inventing success', () => {
	const record = run();
	const item = record.calls[1];
	item.status = 'running';
	expect(currentCallStatus(record, item)).toBe('interrupted');
});
