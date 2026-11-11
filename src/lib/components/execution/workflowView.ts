import type { WorkflowCall, WorkflowRun, WorkflowGraph } from '$lib/apis/execution';
import type { Node, Edge } from '@xyflow/svelte';
import { MarkerType } from '@xyflow/system';
import { moduleName } from './moduleNames';
import { dataEdges, outputNode, referenceNodes } from './nodeProgram';

export const isLive = (status: string) => ['queued', 'running'].includes(status);
/** Current status is never inferred from the selected controller step. */
export function currentCallStatus(run: WorkflowRun, call: WorkflowCall) {
	const terminal = run.events?.findLast(
		(e) =>
			e.call_id === call.id &&
			['call.completed', 'call.failed', 'call.cancelled', 'call.interrupted'].includes(e.type)
	);
	if (terminal) return terminal.type.slice(5);
	if (['running', 'queued'].includes(call.status) && !['running', 'queued'].includes(run.status))
		return 'interrupted';
	return call.status;
}
export const statusLabel = (status: string) =>
	({
		pending: 'Planned',
		held: 'On hold',
		ready: 'Ready',
		skipped: 'Skipped',
		queued: 'Queued',
		waiting: 'Waiting',
		running: 'Running',
		completed: 'Completed',
		changes_requested: 'Revise',
		completed_with_errors: 'Completed with errors',
		failed: 'Failed',
		cancelled: 'Stopped',
		interrupted: 'Interrupted',
		idle: 'Not assigned',
		unused: 'Not used'
	})[status] ?? status.replaceAll('_', ' ');
export function duration(milliseconds: number) {
	const seconds = Math.max(0, Math.floor(milliseconds / 1000));
	return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
export function taskPreview(text: string) {
	return (text.split('Current task:\n').at(-1) ?? text).split('\n\nReference ID')[0].slice(0, 180);
}

export function callTiming(call: WorkflowCall, run: WorkflowRun, now: number) {
	const start = run.created_at * 1000;
	const end = Math.max(
		start + 1,
		run.finished_at ? (run.last_event_at ?? run.finished_at * 1000) : now
	);
	const callStart = Math.max(start, call.started_at ?? call.created_at * 1000);
	const callEnd = Math.max(
		callStart,
		isLive(currentCallStatus(run, call))
			? end
			: (call.last_event_at ?? (call.finished_at ?? call.created_at) * 1000)
	);
	return {
		left: Math.min(100, ((callStart - start) / (end - start)) * 100),
		width: Math.max(1, Math.min(100, ((callEnd - callStart) / (end - start)) * 100)),
		elapsed: callEnd - callStart
	};
}
export function roleViews(run: WorkflowRun) {
	const participants: NonNullable<WorkflowRun['participants']> = (run.participants ?? []).map(
		(p) => ({
			...p,
			id:
				run.workflow_path && !p.id.startsWith(run.workflow_path + '/')
					? run.workflow_path + '/' + p.id
					: p.id
		})
	);
	const known = new Set(participants.map((p) => p.id));
	for (const call of run.calls)
		if (!known.has(call.participant_id)) {
			participants.push({ ...call.binding, id: call.participant_id, name: call.name });
			known.add(call.participant_id);
		}
	return participants.map((p) => {
		const calls = run.calls.filter((c) => c.participant_id === p.id);
		const current =
			calls.findLast((c) => currentCallStatus(run, c) === 'running') ??
			calls.findLast((c) => currentCallStatus(run, c) === 'queued') ??
			calls.at(-1);
		const activity = isLive(run.status) ? run.role_activity?.[p.id] : null;
		const status =
			activity?.status ??
			(current ? currentCallStatus(run, current) : isLive(run.status) ? 'idle' : 'unused');
		return {
			id: p.id,
			name: moduleName(run, p.id),
			framework:
				current?.observed?.framework_name ??
				{
					direct: 'Direct',
					hermes: 'Hermes',
					codex: 'Codex',
					dsh: 'DSH',
					'claude-code': 'Claude Code'
				}[current?.binding?.framework_id ?? p.framework_id ?? ''] ??
				current?.binding?.framework_id ??
				p.framework_id ??
				'',
			model:
				current?.observed?.actual_model ??
				current?.execution?.actual_model ??
				current?.binding?.model_name ??
				p.model_name ??
				'',
			provider: current?.binding?.provider_name ?? p.provider_name ?? '',
			status,
			phase:
				activity?.phase ??
				(current?.status === status ? current?.phase : null) ??
				statusLabel(status),
			calls,
			current
		};
	});
}

/** Render recorded dependencies; workflow policy names never select a different view or layout. */
export function workflowGraph(run: WorkflowRun) {
	if (run.plan) return planGraph(run.plan, run);
	const roles = roleViews(run);
	const calls = new Map(run.calls.map((c) => [c.id, c]));
	const levels = new Map<string, number>();
	for (const call of run.calls)
		levels.set(
			call.id,
			Math.max(-1, ...(call.depends_on ?? []).map((id) => levels.get(id) ?? -1)) + 1
		);
	const roleLevel = (id: string) => {
		const values = run.calls
			.filter((c) => c.participant_id === id)
			.map((c) => levels.get(c.id) ?? 0);
		return values.length
			? Math.min(...values)
			: run.role_activity?.[id]?.status === 'waiting'
				? Math.max(0, ...levels.values()) + 1
				: 0;
	};
	const maxLevel = Math.max(0, ...roles.map((r) => roleLevel(r.id)));
	const rows: Record<number, number> = {};
	const nodes: Node[] = [
		{
			id: 'task',
			type: 'agent',
			position: { x: 0, y: 55 },
			data: { kind: 'boundary', name: 'User task', status: 'completed', phase: 'Current turn' }
		}
	];
	for (const role of roles) {
		const level = roleLevel(role.id);
		const row = rows[level] ?? 0;
		rows[level] = row + 1;
		nodes.push({
			id: 'role:' + role.id,
			type: 'agent',
			position: { x: (level + 1) * 240, y: row * 142 },
			ariaLabel: `${role.name} · ${role.phase}`,
			data: {
				...role,
				role_id: role.id,
				kind: 'role',
				task: taskPreview(role.current?.input ?? '')
			}
		});
	}
	const edges = new Map<string, Edge>();
	const connect = (source: string, target: string, call: WorkflowCall) => {
		const id = `${source}->${target}`;
		const existing = edges.get(id);
		edges.set(id, {
			id,
			source,
			target,
			type: 'smoothstep',
			markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8' },
			animated: call.status === 'running' || !!existing?.animated,
			label: String(Number(existing?.label ?? 0) + 1),
			style: 'stroke: #94a3b8; stroke-width: 1.5;'
		});
	};
	for (const call of run.calls) {
		if (run.legacy_trace) continue;
		const parents = (call.depends_on ?? [])
			.map((id) => calls.get(id))
			.filter((c): c is WorkflowCall => !!c);
		if (!parents.length) connect('task', 'role:' + call.participant_id, call);
		for (const parent of parents)
			connect('role:' + parent.participant_id, 'role:' + call.participant_id, call);
	}
	if (!isLive(run.status)) {
		nodes.push({
			id: 'result',
			type: 'agent',
			position: { x: (maxLevel + 2) * 240, y: 55 },
			data: { kind: 'boundary', name: 'Result', phase: statusLabel(run.status), status: run.status }
		});
		const parents = new Set(run.calls.flatMap((c) => c.depends_on ?? []));
		for (const call of run.calls.filter((c) => !run.legacy_trace && !parents.has(c.id)))
			connect('role:' + call.participant_id, 'result', call);
	}
	return {
		nodes,
		edges: [...edges.values()],
		layoutKey: nodes.map((n) => `${n.id}:${n.position.x}:${n.position.y}`).join('|')
	};
}

export function planGraph(plan: WorkflowGraph, run?: WorkflowRun, planned = false) {
	const levels = new Map<string, number>();
	const pending = new Set(plan.nodes.map((n) => n.id));
	for (let pass = 0; pending.size && pass <= plan.nodes.length; pass++)
		for (const id of [...pending]) {
			const parents = plan.edges.filter((e) => e.on !== 'revise' && e.to === id).map((e) => e.from);
			if (parents.every((p) => levels.has(p))) {
				levels.set(id, Math.max(-1, ...parents.map((p) => levels.get(p)!)) + 1);
				pending.delete(id);
			}
		}
	const rows = new Map<number, number>();
	const nodes: Node[] = plan.nodes.map((node) => {
		const level = levels.get(node.id) ?? 0,
			row = rows.get(level) ?? 0;
		rows.set(level, row + 1);
		const state = run?.node_states?.[node.id];
		const module = run?.modules?.find((m) => m.id === node.binding);
		const participant = run?.participants?.find((p) => p.id === node.binding);
		const composite =
			node.op === 'workflow' ||
			module?.module_type === 'workflow' ||
			module?.framework_id === 'workflow' ||
			participant?.module_type === 'workflow' ||
			participant?.framework_id === 'workflow' ||
			Boolean(state?.workflow);
		const child = state?.workflow?.path ? run?.child_workflows?.[state.workflow.path] : undefined;
		const saved = run?.modules?.find(
			(m) =>
				m.id === participant?.binding_id ||
				(Boolean(state?.workflow?.workflow_id) &&
					m.id === 'workflow:' + state?.workflow?.workflow_id)
		);
		const ids = state?.call_ids ?? [];
		const calls =
			run?.calls.filter(
				(c) =>
					(c.workflow_path ?? '') === (run.workflow_path ?? '') &&
					(c.node_id === node.id || (!c.node_id && ids.includes(c.id)))
			) ?? [];
		// Composite call_ids are descendant ledger references, never a wrapper
		// model invocation. Its own identity comes from the nested scope.
		const last = composite ? undefined : calls.at(-1);
		const status =
			node.enabled === false
				? 'held'
				: planned
					? 'pending'
					: (run?.node_states?.[node.id]?.status ?? 'pending');
		return {
			id: 'node:' + node.id,
			type: 'agent',
			position: { x: (level + (plan.controller ? 1 : 0)) * 245, y: row * 125 },
			data: {
				kind: 'role',
				feedbackIn: plan.edges.some((e) => e.on === 'revise' && e.to === node.id),
				feedbackOut: plan.edges.some((e) => e.on === 'revise' && e.from === node.id),
				node_id: node.id,
				role_id: composite
					? (state?.workflow?.path ??
						(node.context_key
							? [run?.workflow_path, node.context_key].filter(Boolean).join('/')
							: node.binding))
					: (last?.participant_id ?? node.binding),
				name: node.label ?? node.id,
				status,
				framework: composite ? 'Workflow' : (module?.framework_id ?? ''),
				model: composite
					? (child?.template?.name ?? saved?.name ?? module?.name ?? 'Saved Workflow')
					: run
						? (last?.observed?.actual_model ?? module?.model_name ?? node.binding ?? '')
						: 'Project binding',
				task: node.instruction ?? node.question ?? node.op,
				phase: status === 'running' ? (last?.phase ?? 'Running') : statusLabel(status)
			}
		};
	});
	if (plan.controller)
		nodes.push({
			id: 'controller',
			type: 'agent',
			position: { x: 0, y: 60 },
			data: {
				kind: 'boundary',
				node_id: '_controller',
				role_id: plan.controller.binding,
				name: 'Controller',
				status:
					run && !isLive(run.status)
						? run.status
						: run?.control?.controller_running
							? 'running'
							: 'waiting',
				phase:
					run && !isLive(run.status)
						? statusLabel(run.status)
						: run?.control?.controller_running
							? 'Planning'
							: 'Watching results'
			}
		});
	const edges: Edge[] = plan.edges.map((e) => ({
		id: `${e.from}->${e.to}`,
		source: 'node:' + e.from,
		target: 'node:' + e.to,
		type: 'smoothstep',
		markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8' },
		label: e.case ?? (e.on === 'terminal' ? 'All outcomes' : undefined),
		animated: !planned && run?.node_states?.[e.to]?.status === 'running',
		style:
			planned || run?.node_states?.[e.from]?.status !== 'completed'
				? 'stroke: #cbd5e1; stroke-dasharray: 5 4;'
				: 'stroke: #94a3b8; stroke-width: 1.5;'
	}));
	for (const edge of edges) {
		const spec = plan.edges.find((e) => edge.id === `${e.from}->${e.to}`)!;
		if (spec.on === 'revise')
			Object.assign(edge, {
				type: 'feedback',
				sourceHandle: 'feedback-out',
				targetHandle: 'feedback-in',
				markerEnd: { type: MarkerType.ArrowClosed, color: '#b77944' },
				animated: false,
				data: {
					floor: Math.max(...nodes.map((n) => n.position.y)) + 120,
					limit: plan.nodes.find((n) => n.id === spec.from)?.review?.max_repairs ?? 0,
					used: run
						? Math.min(
								plan.nodes.find((n) => n.id === spec.from)?.review?.max_repairs ?? 0,
								run.review_attempts?.filter(
									(a) => a.node_id === spec.from && a.status === 'changes_requested'
								).length ?? 0
							)
						: undefined
				}
			});
	}
	return {
		nodes,
		edges,
		layoutKey:
			plan.nodes.map((n) => n.id).join('/') + '|' + plan.edges.map((e) => e.from + e.to).join('/')
	};
}
export function graphChanges(before: WorkflowGraph, after: WorkflowGraph) {
	const changes: Record<string, unknown>[] = [];
	for (const edge of before.edges)
		if (!after.edges.some((e) => JSON.stringify(e) === JSON.stringify(edge)))
			changes.push({ op: 'remove_edge', edge });
	for (const node of before.nodes)
		if (!after.nodes.some((n) => n.id === node.id))
			changes.push({ op: 'remove_node', node_id: node.id });
	for (const node of after.nodes) {
		const previous = before.nodes.find((n) => n.id === node.id);
		if (!previous || JSON.stringify(previous) !== JSON.stringify(node))
			changes.push({ op: previous ? 'update_node' : 'add_node', node });
	}
	for (const edge of after.edges)
		if (!before.edges.some((e) => JSON.stringify(e) === JSON.stringify(edge)))
			changes.push({ op: 'add_edge', edge });
	if (after.schema_version === '2.0') {
		for (const [port, ref] of Object.entries(after.outputs ?? {}))
			if (JSON.stringify(before.outputs?.[port]) !== JSON.stringify(ref))
				changes.push({ op: 'set_output', port, ref });
	} else if (before.result_node !== after.result_node)
		changes.push({ op: 'set_result', node_id: after.result_node });
	return changes;
}

/** Template structure has operations and dependencies, never invocation states. */
export function definitionGraph(plan: WorkflowGraph, modules: { id: string; name: string }[]) {
	const dependencies = dataEdges(plan);
	const plannedEdges = new Map<string, string>();
	for (const node of plan.nodes)
		for (const ref of Object.values(node.inputs))
			if (ref.source === 'output' && !plan.outputs?.[ref.port ?? '']) {
				const producer = plan.nodes.find(
					(n) => n.op === 'plan' && (n.planning?.output_port ?? 'candidate') === ref.port
				);
				if (producer) {
					dependencies.push({ from: producer.id, to: node.id, on: 'success', generated: true });
					plannedEdges.set(`${producer.id}->${node.id}`, ref.port!);
				}
			}
	const graph = planGraph({ ...plan, edges: dependencies });
	const inputNodes = plan.nodes.some((n) => n.op === 'input')
		? []
		: plan.nodes.filter((node) =>
				[
					...Object.values(node.inputs),
					...(node.op === 'map' && node.items ? [node.items] : [])
				].some((ref) => ref.source === 'input')
			);
	const columns = new Map<number, Node[]>();
	for (const item of graph.nodes) {
		const column = columns.get(item.position.x) ?? [];
		column.push(item);
		columns.set(item.position.x, column);
	}
	const rows = Math.max(1, ...[...columns.values()].map((column) => column.length));
	const nodes: Node[] = [];
	for (const column of columns.values())
		column.forEach((item, index) => {
			const node = plan.nodes.find((n) => n.id === item.data.node_id);
			if (!node) return;
			nodes.push({
				id: item.id,
				type: 'definition',
				position: {
					x: item.position.x + (inputNodes.length ? 245 : 0),
					y: index * 115 + ((rows - column.length) * 115) / 2
				},
				ariaLabel: `${node.label ?? node.id} · ${node.op}`,
				data: {
					node_id: node.id,
					name: node.label ?? node.id,
					operation: node.op,
					feedbackIn: item.data.feedbackIn,
					feedbackOut: item.data.feedbackOut,
					role: modules.find((m) => m.id === node.binding)?.name ?? node.binding,
					policy: node.policy,
					result: node.id === outputNode(plan)
				}
			});
		});
	// Full bindings remain in the definition and inspector; avoid a redundant
	// input-to-later-step line across nodes already connected by a path.
	const visibleEdges = graph.edges.filter((edge) => {
		if (plan.nodes.find((n) => 'node:' + n.id === edge.source)?.op !== 'input') return true;
		const seen = new Set([edge.source]);
		for (let pass = 0; pass < plan.nodes.length; pass++)
			for (const other of graph.edges)
				if (other !== edge && other.type !== 'feedback' && seen.has(other.source))
					seen.add(other.target);
		return !seen.has(edge.target);
	});
	const edges: Edge[] = visibleEdges.map((edge) => ({
		...edge,
		label: edge.label === 'All outcomes' ? undefined : edge.label,
		animated: false,
		style: 'stroke: #9b91ba; stroke-width: 1.5;',
		markerEnd: { type: MarkerType.ArrowClosed, color: '#9b91ba' }
	}));
	for (const edge of edges) {
		if (edge.type === 'feedback') {
			edge.markerEnd = { type: MarkerType.ArrowClosed, color: '#b77944' };
			edge.data = { ...edge.data, floor: Math.max(...nodes.map((n) => n.position.y)) + 100 };
			continue;
		}
		const target = plan.nodes.find((n) => 'node:' + n.id === edge.target);
		const source = plan.nodes.find((n) => 'node:' + n.id === edge.source);
		const fields = Object.entries(target?.inputs ?? {})
			.filter(([, ref]) => source && referenceNodes(ref, plan).includes(source.id))
			.map(([, ref]) => ref.pointer?.replace(/^\//, '') || 'output');
		if (fields.length) edge.label = fields.join(', ');
		else if (source && target) edge.style = 'stroke: #a7a0b2; stroke-dasharray: 4 3;';
		const port = plannedEdges.get(edge.id);
		if (port) {
			edge.label = `${port} · generated work`;
			edge.style = 'stroke: #9b91ba; stroke-dasharray: 5 4;';
		}
	}
	if (inputNodes.length) {
		nodes.unshift({
			id: 'input',
			type: 'definition',
			position: { x: 0, y: ((rows - 1) * 115) / 2 },
			data: { name: 'User task', operation: 'input' }
		});
		for (const node of inputNodes)
			edges.push({
				id: 'input->' + node.id,
				source: 'input',
				target: 'node:' + node.id,
				type: 'smoothstep',
				style: 'stroke: #9b91ba; stroke-width: 1.5;',
				markerEnd: { type: MarkerType.ArrowClosed, color: '#9b91ba' }
			});
	}
	return { nodes, edges, layoutKey: graph.layoutKey + '|definition' };
}
export function branchWorkflow(history: {
	currentId?: string;
	messages?: Record<
		string,
		{
			parentId?: string;
			meta?: { workflow_run_id?: string };
			statusHistory?: { workflow?: { run_id?: string } }[];
			done?: boolean;
		}
	>;
}) {
	let id = history.currentId;
	const seen = new Set<string>();
	while (id && !seen.has(id)) {
		seen.add(id);
		const message = history.messages?.[id];
		if (!message) break;
		const runId =
			message.meta?.workflow_run_id ??
			message.statusHistory?.findLast((s) => s.workflow?.run_id)?.workflow?.run_id;
		if (runId) return { runId, running: message.done === false };
		id = message.parentId;
	}
	return null;
}
