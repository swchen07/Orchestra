import type { WorkflowCall, WorkflowRun } from '$lib/apis/execution';
import type { Node, Edge } from '@xyflow/svelte';
import { MarkerType } from '@xyflow/system';
import { duration, statusLabel, currentCallStatus } from './workflowView';
import { summarizeUsage, usageLabel } from './workflowUsage';
import { moduleName } from './moduleNames';
import { hasPlanning } from './nodeProgram';
export const GRAPH_COLUMN = 280;
export const STEP_WIDTH = 244;
export const MODULE_WIDTH = 168;
export const BUS_INSET = 18;
export { currentCallStatus } from './workflowView';

export const timeMs = (value: number | undefined) =>
	!value ? 0 : value < 1e11 ? value * 1000 : value;
export const callStart = (call: WorkflowCall) => call.started_at ?? timeMs(call.created_at);
export function callEnd(run: WorkflowRun, call: WorkflowCall) {
	const event = run.events?.findLast(
		(e) =>
			e.call_id === call.id &&
			['call.completed', 'call.failed', 'call.cancelled', 'call.interrupted'].includes(e.type)
	);
	return event?.at ?? timeMs(call.finished_at);
}
function decisionAction(call: WorkflowCall) {
	try {
		const text = (call.output ?? '').replace(/^```(?:json)?\s*|\s*```$/g, '');
		const action = JSON.parse(text).action;
		return (
			(
				{
					propose_plan: 'Plan',
					patch_plan: 'Adjust',
					dispatch: 'Dispatch',
					wait: 'Observe',
					finish: 'Finish',
					final: 'Finish',
					request_input: 'Review'
				} as Record<string, string>
			)[action] ?? 'Decide'
		);
	} catch {
		return call.status === 'running' ? 'Planning' : 'Decide';
	}
}
export type ExecutionCycle = {
	id: string;
	index: number;
	label: string;
	decision?: WorkflowCall;
	calls: WorkflowCall[];
	start: number;
	end: number;
};
export function executionCycles(run: WorkflowRun): ExecutionCycle[] {
	const leadCalls = !hasPlanning(run.plan)
		? run.calls.filter((c) => c.participant_id === run.template?.lead_id)
		: [];
	const anchors = run.calls.filter((c) => c.kind === 'decision');
	const plannedLeadCalls =
		run.plan?.nodes.filter((n) => n.binding === run.template?.lead_id).length ?? 0;
	if (!anchors.length && (leadCalls.length > 1 || (leadCalls.length > 0 && plannedLeadCalls > 1)))
		anchors.push(...leadCalls);
	const decisions = anchors.slice().sort((a, b) => callStart(a) - callStart(b));
	if (!decisions.length)
		return [
			{
				id: 'defined',
				index: 1,
				label: 'Defined flow',
				calls: run.calls,
				start: run.created_at * 1000,
				end: 0
			}
		];
	const cycles: ExecutionCycle[] = decisions.map((decision, i) => ({
		id: decision.id,
		index: i + 1,
		label:
			decision.kind === 'decision'
				? decisionAction(decision)
				: (run.plan?.nodes.find((n) => n.id === decision.node_id)?.label ?? 'Coordinator'),
		decision,
		calls: [],
		start: callStart(decision),
		end: decisions[i + 1] ? callStart(decisions[i + 1]) : timeMs(run.finished_at)
	}));
	const byId = new Map(cycles.map((c) => [c.id, c]));
	for (const call of run.calls.filter((c) => !byId.has(c.id))) {
		const direct = (call.depends_on ?? [])
			.map((id) => byId.get(id))
			.filter((c): c is ExecutionCycle => !!c)
			.at(-1);
		const inferred = cycles.findLast(
			(c) => callEnd(run, c.decision!) && callEnd(run, c.decision!) <= callStart(call)
		);
		(direct ?? inferred ?? cycles[0]).calls.push(call);
	}
	return cycles;
}
export function statusAt(run: WorkflowRun, call: WorkflowCall, at: number) {
	const end = callEnd(run, call);
	if (end && end <= at) return currentCallStatus(run, call);
	return call.started_at && call.started_at <= at ? 'running' : 'queued';
}
const frameworkNames: Record<string, string> = {
	direct: 'Direct',
	hermes: 'Hermes',
	codex: 'Codex',
	'claude-code': 'Claude Code',
	dsh: 'DSH'
};
export function callIdentity(run: WorkflowRun, call: WorkflowCall) {
	const binding = call.binding ?? run.participants?.find((p) => p.id === call.participant_id);
	function findScope(source: WorkflowRun): WorkflowRun | undefined {
		if (source.workflow_path === call.workflow_path) return source;
		for (const child of Object.values(source.child_workflows ?? {})) {
			const found = findScope(child);
			if (found) return found;
		}
	}
	const scope = call.workflow_path ? findScope(run) : run;
	const definition =
		scope?.plan_revisions?.find((r) => r.revision === call.plan_revision)?.graph ?? scope?.plan;
	return {
		name: moduleName(run, call.participant_id),
		task:
			call.kind === 'decision'
				? 'Plan and control'
				: call.kind === 'review'
					? 'Independent result review'
					: (definition?.nodes.find((n) => n.id === call.node_id)?.label ??
						call.node_id ??
						call.name),
		framework: call.observed?.framework_name ?? frameworkNames[binding?.framework_id ?? ''] ?? '',
		model: call.observed?.actual_model ?? binding?.model_name ?? ''
	};
}
/** Controller decisions form one spine. Dispatch/return edges keep their originating owner. */
export function executionMap(run: WorkflowRun, stepId = '', now = Date.now(), dataLinks = true) {
	const cycles = executionCycles(run);
	const nodes: Node[] = [],
		edges: Edge[] = [];
	const owners = new Map(cycles.flatMap((c) => c.calls.map((call) => [call.id, c] as const)));
	const height = Math.max(330, 145 + Math.max(0, ...cycles.map((c) => c.calls.length)) * 106);
	const edge = (id: string, source: string, target: string, kind: string, label = '') => ({
		id,
		source,
		target,
		type: kind === 'dependency' ? 'smoothstep' : 'execution',
		sourceHandle: ['dispatch', 'trunk'].includes(kind) ? 'dispatch' : 'next',
		targetHandle: ['dispatch', 'trunk'].includes(kind)
			? 'dispatch-in'
			: ['return', 'return-trunk'].includes(kind)
				? 'result'
				: 'previous',
		label,
		data: { kind },
		markerEnd:
			kind === 'trunk'
				? undefined
				: {
						type: MarkerType.ArrowClosed,
						color: kind.startsWith('return') ? '#16826e' : '#9184b7'
					},
		style: `stroke: ${kind.startsWith('return') ? '#16826e' : '#9184b7'}; stroke-width: 1.5;`
	});
	cycles.forEach((step, index) => {
		const ownerId = 'step:' + step.id;
		const x = 20 + index * GRAPH_COLUMN;
		const usage = summarizeUsage(run, step.decision ? [step.decision] : step.calls);
		nodes.push({
			id: ownerId,
			type: 'runStep',
			position: { x, y: 28 },
			data: {
				name: step.decision ? `Controller · Step ${String(step.index).padStart(2, '0')}` : 'Task',
				phase: step.label,
				status: step.decision ? currentCallStatus(run, step.decision) : run.status,
				count: step.calls.length,
				tokens: usageLabel(usage),
				tokenTotal: usage.total,
				tokenScope: step.decision ? 'own' : 'stage',
				selected: step.id === stepId,
				call_id: step.decision?.id
			}
		});
		if (index) edges.push(edge('spine:' + index, 'step:' + cycles[index - 1].id, ownerId, 'spine'));
		const last = step.calls.at(-1);
		if (last) edges.push(edge('trunk:' + step.id, ownerId, 'call:' + last.id, 'trunk'));
		const lastReturned = step.calls.findLast(
			(call) => !['queued', 'running'].includes(currentCallStatus(run, call))
		);
		if (lastReturned)
			edges.push(
				edge('return-trunk:' + step.id, 'call:' + lastReturned.id, ownerId, 'return-trunk')
			);
		step.calls.forEach((call, row) => {
			const status = currentCallStatus(run, call);
			const usage = summarizeUsage(run, [call]);
			const end =
				callEnd(run, call) ||
				(!['running', 'queued'].includes(run.status) ? timeMs(run.finished_at) : now);
			const observedBy = cycles.find((c) => c.decision?.depends_on?.includes(call.id));
			nodes.push({
				id: 'call:' + call.id,
				type: 'runStep',
				position: { x: x + (STEP_WIDTH - MODULE_WIDTH) / 2, y: 145 + row * 106 },
				data: {
					...callIdentity(run, call),
					kind: 'work',
					tokens: usageLabel(usage),
					tokenTotal: usage.total,
					tokenScope: 'own',
					status,
					call_id: call.id,
					phase: statusLabel(status),
					elapsed: duration(Math.max(0, end - callStart(call))),
					returned: !['queued', 'running'].includes(status),
					returnNote: observedBy ? `Observed in step ${observedBy.index}` : '',
					selected: false
				}
			});
			edges.push(edge('dispatch:' + call.id, ownerId, 'call:' + call.id, 'dispatch'));
			if (!['queued', 'running'].includes(status))
				edges.push(edge('return:' + call.id, 'call:' + call.id, ownerId, 'return'));
			for (const parent of dataLinks ? (call.depends_on ?? []) : [])
				if (owners.has(parent))
					edges.push(
						edge(
							'dependency:' + parent + ':' + call.id,
							'call:' + parent,
							'call:' + call.id,
							'dependency'
						)
					);
		});
	});
	return {
		nodes,
		edges,
		height,
		width: Math.max(320, cycles.length * GRAPH_COLUMN + 20),
		focusIndex: Math.max(
			0,
			cycles.findIndex((c) => c.id === stepId)
		),
		layoutKey: nodes.map((n) => n.id).join('|'),
		cycles,
		selected: cycles.find((c) => c.id === stepId)
	};
}
