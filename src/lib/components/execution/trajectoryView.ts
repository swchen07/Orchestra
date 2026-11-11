import type { WorkflowRun, WorkflowEvent } from '$lib/apis/execution';
import { callIdentity, currentCallStatus, callEnd } from './workflowPresentation';

export type TraceRecord = {
	id: string;
	category: 'task' | 'session' | 'reply' | 'tool' | 'lifecycle' | 'report' | 'control' | 'review';
	title: string;
	role: string;
	callId?: string;
	task?: string;
	status: string;
	at: number;
	end?: number;
	input?: unknown;
	output?: unknown;
	reportId?: string;
	events: WorkflowEvent[];
};
const terminalTool = (event: WorkflowEvent) => {
	const data = event.data ?? {};
	if (event.type === 'tool.failed' || ['failed', 'error'].includes(String(data.status)))
		return 'failed';
	if (
		event.type === 'tool.blocked' ||
		['denied', 'declined', 'cancelled', 'canceled'].includes(String(data.status))
	)
		return 'cancelled';
	if (
		event.type === 'tool.completed' ||
		data.done === true ||
		['completed', 'finished', 'success', 'succeeded'].includes(String(data.status))
	)
		return 'completed';
	return 'running';
};
const content = (data: Record<string, unknown>, keys: string[]) => {
	for (const key of keys) if (data[key] !== undefined && data[key] !== null) return data[key];
	return undefined;
};
export const traceText = (value: unknown) =>
	value === undefined ? '' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);

/** One tool invocation, not one row per streaming lifecycle notification. */
export function trajectoryRecords(run: WorkflowRun): TraceRecord[] {
	const result: TraceRecord[] = [];
	const groups = new Map<string, TraceRecord>();
	const tools = new Map<string, TraceRecord>();
	const resolvedReviews = new Set<string>();
	for (const event of [...(run.events ?? [])].sort((a, b) => a.seq - b.seq)) {
		// Earlier journals saved verdict events without call IDs. Resolve them
		// through their immutable review-attempt records, without rewriting history.
		const verdictEvent = ['review.approved', 'review.changes_requested'].includes(event.type);
		const attempt =
			verdictEvent && !event.call_id
				? run.review_attempts?.find(
						(a) =>
							!resolvedReviews.has(a.call_id ?? '') &&
							a.summary === event.data?.summary &&
							a.status === (event.type === 'review.approved' ? 'approved' : 'changes_requested')
					)
				: undefined;
		const call = run.calls.find((c) => c.id === (event.call_id ?? attempt?.call_id));
		if (verdictEvent && call) resolvedReviews.add(call.id);
		const identity = call ? callIdentity(run, call) : null;
		const base = {
			role: identity?.name ?? (verdictEvent ? 'Reviewer' : 'Controller'),
			task: identity?.task,
			callId: call?.id
		};
		const data = event.data ?? {};
		if (event.type.startsWith('tool.')) {
			const key = `${event.call_id}:${data.id ?? data.tool_call_id ?? data.name ?? 'Tool'}`;
			let record = tools.get(key);
			const state = terminalTool(event);
			if (
				!record ||
				(!data.id &&
					!data.tool_call_id &&
					event.type === 'tool.started' &&
					record.status !== 'running')
			) {
				record = {
					...base,
					id: 'tool:' + event.seq,
					category: 'tool',
					title: String(data.name ?? 'Tool'),
					status: state,
					at: event.at,
					events: []
				};
				result.push(record);
				tools.set(key, record);
			}
			record.events.push(event);
			record.input = content(data, ['input', 'arguments', 'rawInput']) ?? record.input;
			record.output =
				content(data, ['output', 'result', 'content', 'rawOutput', 'error']) ?? record.output;
			// A late progress notification cannot reopen a settled tool invocation.
			if (record.status === 'running' || state !== 'running') record.status = state;
			if (state !== 'running') record.end = event.updated_at ?? event.at;
			continue;
		}
		let category: TraceRecord['category'] = 'control';
		if (event.type === 'call.queued') category = 'task';
		else if (event.type.startsWith('context.') || event.type === 'model.metadata')
			category = 'session';
		else if (event.type === 'model.output') category = 'reply';
		else if (event.type === 'report.completed') category = 'report';
		else if (verdictEvent) category = 'review';
		else if (
			event.type.startsWith('call.') ||
			event.type === 'model.usage' ||
			event.type === 'decision.completed'
		)
			category = 'lifecycle';
		const key = `${call?.id ?? event.call_id ?? (category === 'review' ? 'verdict-' + event.seq : 'run')}:${category}`;
		let record = groups.get(key);
		if (!record) {
			const titles = {
				task: call?.kind === 'decision' ? 'Plan request' : 'Assign task',
				session: 'Session & context',
				reply: call?.kind === 'decision' ? 'Controller response' : 'Model response',
				report: 'Intermediate report',
				lifecycle: 'Execution',
				control: 'Run events',
				tool: 'Tool',
				review: 'Independent review verdict'
			};
			record = {
				...base,
				id: key,
				category,
				title: titles[category],
				status: call ? currentCallStatus(run, call) : run.status,
				at: event.at,
				events: []
			};
			groups.set(key, record);
			result.push(record);
		}
		record.events.push(event);
		if (category === 'review') {
			record.output = data;
			record.status = event.type === 'review.approved' ? 'completed' : 'changes_requested';
		}
		if (category === 'task') record.input = call?.input;
		if (category === 'reply') {
			record.output = event.text ?? call?.output;
			record.end = event.updated_at ?? (call && callEnd(run, call)) ?? event.at;
		}
		if (category === 'session')
			record.output = { ...((record.output as Record<string, unknown>) ?? {}), ...data };
		if (category === 'report') {
			record.reportId = event.report_id ?? call?.report_id;
			record.output = call?.output;
		}
		if (category === 'lifecycle') {
			record.end = call && callEnd(run, call);
			record.output = call?.usage;
		}
		if (category === 'control') record.output = data;
	}
	for (const record of result) {
		const call = run.calls.find((c) => c.id === record.callId);
		if (
			record.category === 'tool' &&
			record.status === 'running' &&
			call &&
			!['running', 'queued'].includes(currentCallStatus(run, call))
		)
			record.status = 'unconfirmed';
	}
	return result.sort((a, b) => a.at - b.at);
}
