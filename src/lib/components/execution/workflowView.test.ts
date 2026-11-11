import { describe, it, expect } from 'vitest';
import {
	roleViews,
	workflowGraph,
	callTiming,
	planGraph,
	definitionGraph,
	graphChanges,
	branchWorkflow
} from './workflowView';
import type { WorkflowRun, WorkflowGraph } from '$lib/apis/execution';

describe('template topology definition', () => {
	it('shows the shared input, parallel branches and result without runtime state', () => {
		const input = { source: 'input' as const, pointer: '/task' };
		const graph: WorkflowGraph = {
			nodes: [
				{ id: 'a', op: 'call', binding: 'first', inputs: { task: input } },
				{ id: 'b', op: 'call', binding: 'second', inputs: { task: input } },
				{ id: 'answers', op: 'join', policy: 'all_terminal', inputs: {} }
			],
			edges: [
				{ from: 'a', to: 'answers', on: 'terminal' },
				{ from: 'b', to: 'answers', on: 'terminal' }
			],
			result_node: 'answers'
		};
		const view = definitionGraph(graph, [{ id: 'first', name: 'First module' }]);
		expect(view.edges.map((edge) => [edge.source, edge.target])).toEqual([
			['node:a', 'node:answers'],
			['node:b', 'node:answers'],
			['input', 'node:a'],
			['input', 'node:b']
		]);
		expect(view.nodes.find((n) => n.id === 'node:a')?.data.role).toBe('First module');
		expect(view.nodes.find((n) => n.id === 'node:answers')?.data.result).toBe(true);
		expect(view.nodes.find((n) => n.id === 'node:answers')?.position.y).toBe(
			view.nodes[0].position.y
		);
		expect(view.nodes.every((n) => !('status' in n.data) && !('phase' in n.data))).toBe(true);
		expect(view.edges.every((edge) => !edge.animated)).toBe(true);
	});
	it('keeps a saved sequence and only draws external inputs actually referenced', () => {
		const graph: WorkflowGraph = {
			nodes: [
				{ id: 'prepare', op: 'call', binding: 'a', inputs: {} },
				{
					id: 'finish',
					op: 'call',
					binding: 'b',
					inputs: { report: { source: 'node', node: 'prepare', pointer: '/report' } }
				}
			],
			edges: [{ from: 'prepare', to: 'finish', on: 'success' }],
			result_node: 'finish'
		};
		const view = definitionGraph(graph, []);
		expect(view.nodes.map((n) => n.id)).toEqual(['node:prepare', 'node:finish']);
		expect(view.nodes[1].position.x).toBeGreaterThan(view.nodes[0].position.x);
		expect(view.edges).toHaveLength(1);
		const implicit = definitionGraph({ ...graph, edges: [] }, []);
		expect(implicit.edges.map((edge) => [edge.source, edge.target])).toEqual([
			['node:prepare', 'node:finish']
		]);
		expect(implicit.nodes[1].position.x).toBeGreaterThan(implicit.nodes[0].position.x);
		expect(graph.nodes[0]).toEqual({ id: 'prepare', op: 'call', binding: 'a', inputs: {} });
	});
});

it('shows a held future task distinctly from an executable planned task', () => {
	const graph: WorkflowGraph = {
		nodes: [{ id: 'collect', op: 'finish', enabled: false, inputs: {} }],
		edges: [],
		result_node: 'collect'
	};
	expect(planGraph(graph, undefined, true).nodes[0].data).toMatchObject({
		status: 'held',
		phase: 'On hold'
	});
	graph.nodes[0].enabled = true;
	expect(planGraph(graph, undefined, true).nodes[0].data.status).toBe('pending');
});

const run = (calls: WorkflowRun['calls'], status = 'running'): WorkflowRun => ({
	id: 'run',
	status,
	created_at: 1,
	participants: [
		{ id: 'lead', name: 'Editor', framework_id: 'claude-code' },
		{ id: 'a', name: 'Researcher', framework_id: 'codex' },
		{ id: 'b', name: 'Reviewer', framework_id: 'dsh' }
	],
	calls
});
const call = (
	id: string,
	participant_id: string,
	depends_on: string[] = [],
	status = 'completed'
) => ({
	id,
	participant_id,
	name: participant_id,
	status,
	input: 'task',
	created_at: 1,
	depends_on
});
describe('one event-driven workflow presentation', () => {
	it('includes descendant calls in module statistics without duplicating local child slots', () => {
		const source = run([call('child-call', 'team/worker')]);
		expect(roleViews(source).find((r) => r.id === 'team/worker')?.calls).toHaveLength(1);
		const child = {
			...source,
			workflow_path: 'team',
			participants: [{ id: 'worker', name: 'Worker' }]
		};
		expect(roleViews(child).map((r) => r.id)).toEqual(['team/worker']);
		expect(roleViews(child)[0].calls).toHaveLength(1);
	});
	it('places overlapping calls on one recorded time axis without workflow-specific assumptions', () => {
		const record = { ...run([], 'completed'), finished_at: 11, last_event_at: 11000 };
		const first = { ...call('1', 'a'), started_at: 1000, last_event_at: 6000 };
		const second = { ...call('2', 'b'), started_at: 3500, last_event_at: 8500 };
		expect(callTiming(first, record, 50000)).toEqual({ left: 0, width: 50, elapsed: 5000 });
		expect(callTiming(second, record, 50000)).toEqual({ left: 25, width: 50, elapsed: 5000 });
	});
	it('distinguishes waiting roles, queued calls and actively running calls', () => {
		const roles = roleViews(run([call('1', 'a', [], 'queued'), call('2', 'b', [], 'running')]));
		expect(roles.map((r) => r.status)).toEqual(['idle', 'queued', 'running']);
		expect(roleViews(run([], 'completed')).every((r) => r.status === 'unused')).toBe(true);
		const waiting = run([call('1', 'lead')]);
		waiting.role_activity = { lead: { status: 'waiting', phase: 'Waiting for reports' } };
		expect(roleViews(waiting)[0].status).toBe('waiting');
		expect(roleViews({ ...waiting, status: 'completed' })[0].status).toBe('completed');
		const pending = run([call('1', 'a', [], 'running'), call('2', 'b', [], 'running')]);
		pending.role_activity = waiting.role_activity;
		const graph = workflowGraph(pending);
		expect(graph.nodes.find((n) => n.id === 'role:lead')!.position.x).toBeGreaterThan(
			graph.nodes.find((n) => n.id === 'role:a')!.position.x
		);
	});
	it('derives summary edges from actual report dependencies, independent of template naming', () => {
		const record = run(
			[call('1', 'a'), call('2', 'b'), call('3', 'lead', ['1', '2'])],
			'completed'
		);
		const graph = workflowGraph(record);
		expect(graph.edges.map((e) => [e.source, e.target])).toContainEqual(['role:a', 'role:lead']);
		expect(graph.edges.map((e) => [e.source, e.target])).toContainEqual(['role:b', 'role:lead']);
		expect(graph.edges.map((e) => [e.source, e.target])).toContainEqual(['role:lead', 'result']);
		expect(
			workflowGraph({ ...record, template: { name: 'Any composition', id: 'other' } })
		).toEqual(graph);
	});
	it('shows repeated lead/worker handoffs and marks unused participants without inventing work', () => {
		const graph = workflowGraph(
			run([call('1', 'lead'), call('2', 'b', ['1']), call('3', 'lead', ['2'], 'running')])
		);
		expect(
			graph.edges.find((e) => e.source === 'role:b' && e.target === 'role:lead')?.animated
		).toBe(true);
		expect(graph.edges.some((e) => e.target === 'role:a')).toBe(false);
		expect(graph.nodes.some((n) => n.id === 'result')).toBe(false);
	});
	it('an independent composition uses the same roles and graph renderer with task-root edges', () => {
		const graph = workflowGraph(run([call('1', 'a'), call('2', 'b')], 'completed'));
		expect(graph.edges.filter((e) => e.source === 'task').map((e) => e.target)).toEqual([
			'role:a',
			'role:b'
		]);
		expect(graph.edges.filter((e) => e.target === 'result')).toHaveLength(2);
	});
});

describe('generic graph plans and chat branches', () => {
	const plan: WorkflowGraph = {
		schema_version: '1.0',
		revision: 2,
		nodes: [
			{ id: 'fast', op: 'call', binding: 'a', inputs: {} },
			{ id: 'slow', op: 'call', binding: 'b', inputs: {} },
			{
				id: 'next',
				op: 'call',
				binding: 'a',
				inputs: { evidence: { source: 'node', node: 'fast', pointer: '/report' } }
			}
		],
		edges: [{ from: 'fast', to: 'next', on: 'success' }],
		result_node: 'next'
	};
	it('renders declared task dependencies and concurrent state for any template', () => {
		const record = {
			...run([]),
			plan,
			node_states: {
				fast: { status: 'completed' },
				slow: { status: 'running' },
				next: { status: 'running' }
			}
		};
		const rendered = planGraph(plan, record);
		expect(rendered.edges.map((e) => [e.source, e.target])).toEqual([['node:fast', 'node:next']]);
		expect(rendered.nodes.find((n) => n.id === 'node:slow')?.data.status).toBe('running');
		expect(rendered.nodes.find((n) => n.id === 'node:next')?.data.status).toBe('running');
		expect(planGraph(plan, record, true).nodes.every((n) => n.data.status === 'pending')).toBe(
			true
		);
		expect(workflowGraph({ ...record, template: { id: 'different', name: 'Any name' } })).toEqual(
			rendered
		);
	});
	it('builds a scoped atomic patch without changing the source plan', () => {
		const after = structuredClone(plan);
		after.nodes[0].binding = 'hermes';
		after.nodes.push({
			id: 'final',
			op: 'finish',
			inputs: { answer: { source: 'node', node: 'next' } }
		});
		after.edges.push({ from: 'next', to: 'final', on: 'success' });
		after.result_node = 'final';
		const patch = graphChanges(plan, after);
		expect(patch.map((p) => p.op)).toEqual(['update_node', 'add_node', 'add_edge', 'set_result']);
		expect(plan.nodes[0].binding).toBe('a');
		expect(graphChanges(plan, structuredClone(plan))).toEqual([]);
	});
	it('selects only the current chat branch and supports streaming status IDs', () => {
		const history = {
			currentId: 'current',
			messages: {
				current: {
					parentId: 'parent',
					done: false,
					statusHistory: [{ workflow: { run_id: 'live' } }]
				},
				parent: { meta: { workflow_run_id: 'old' } },
				sibling: { meta: { workflow_run_id: 'unrelated' } }
			}
		};
		expect(branchWorkflow(history)).toEqual({ runId: 'live', running: true });
		expect(branchWorkflow({ ...history, currentId: 'parent' })).toEqual({
			runId: 'old',
			running: false
		});
		expect(
			branchWorkflow({ currentId: 'a', messages: { a: { parentId: 'b' }, b: { parentId: 'a' } } })
		).toBeNull();
	});
});

describe('composite bindings keep their own presentation identity', () => {
	const plan: WorkflowGraph = {
		nodes: [
			{
				id: 'stream',
				action: 'module.call',
				op: 'call',
				binding: 'worker',
				context_key: 'stream',
				inputs: {}
			}
		],
		edges: [],
		result_node: 'stream'
	};
	it('shows a saved Workflow name for ordinary Actions without borrowing descendant native metadata', () => {
		for (const op of ['call', 'review', 'dispatch', 'route'] as const) {
			const graph = { ...plan, nodes: [{ ...plan.nodes[0], op }] };
			const source: WorkflowRun = {
				...run([
					{
						...call('leaf', 'stream/worker'),
						node_id: 'native',
						workflow_path: 'stream',
						observed: { actual_model: 'deepseek-flash' },
						phase: 'Calling native model'
					}
				]),
				modules: [
					{ id: 'worker', name: 'Executor 01', module_type: 'workflow', framework_id: 'workflow' }
				],
				node_states: {
					stream: {
						status: 'running',
						call_ids: ['leaf'],
						workflow: { path: 'stream', workflow_id: 'saved' }
					}
				},
				child_workflows: {
					stream: {
						...run([]),
						workflow_path: 'stream',
						template: { id: 'saved', name: 'QA · DSH Middle' }
					}
				}
			};
			expect(planGraph(graph, source).nodes[0].data).toMatchObject({
				framework: 'Workflow',
				model: 'QA · DSH Middle',
				role_id: 'stream',
				phase: 'Running'
			});
		}
	});
	it('resolves a pending composite role through its saved binding rather than the slot name', () => {
		const source: WorkflowRun = {
			...run([]),
			participants: [
				{ id: 'worker', name: 'Executor 01', binding_id: 'workflow:saved', module_type: 'workflow' }
			],
			modules: [
				{ id: 'worker', name: 'Executor 01', framework_id: 'workflow' },
				{ id: 'workflow:saved', name: 'QA · Hermes Leaf', module_type: 'workflow' }
			]
		};
		expect(planGraph(plan, source).nodes[0].data).toMatchObject({
			framework: 'Workflow',
			model: 'QA · Hermes Leaf'
		});
	});
	it('uses recorded composite state when archived module metadata is missing', () => {
		const source: WorkflowRun = {
			...run([
				{
					...call('leaf', 'outer/stream/executor_01_b6'),
					node_id: 'stream',
					workflow_path: 'outer/stream',
					observed: { actual_model: 'executor_01_b6' }
				}
			]),
			workflow_path: 'outer',
			node_states: {
				stream: {
					status: 'completed',
					call_ids: ['leaf'],
					workflow: { path: 'outer/stream', workflow_id: 'saved' }
				}
			},
			child_workflows: {
				'outer/stream': {
					...run([], 'completed'),
					template: { id: 'saved', name: 'QA · Hermes Leaf' }
				}
			}
		};
		expect(planGraph(plan, source).nodes[0].data).toMatchObject({
			framework: 'Workflow',
			model: 'QA · Hermes Leaf',
			role_id: 'outer/stream'
		});
	});
	it('selects only same-scope native calls when descendant IDs overlap a node state', () => {
		const source: WorkflowRun = {
			...run([
				{ ...call('own', 'stream'), node_id: 'stream', observed: { actual_model: 'own-native' } },
				{
					...call('child', 'nested/worker'),
					node_id: 'stream',
					workflow_path: 'nested',
					observed: { actual_model: 'wrong-descendant' }
				}
			]),
			modules: [{ id: 'worker', name: 'Native', framework_id: 'dsh' }],
			node_states: { stream: { status: 'completed', call_ids: ['own', 'child'] } }
		};
		expect(planGraph(plan, source).nodes[0].data).toMatchObject({
			framework: 'dsh',
			model: 'own-native',
			role_id: 'stream'
		});
	});
});
