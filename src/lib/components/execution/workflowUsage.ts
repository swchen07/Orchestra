import type { WorkflowCall, WorkflowRun } from '$lib/apis/execution';

const count = (value: unknown): number | undefined =>
	typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;
export const tokenNumber = (value: number | undefined) =>
	value === undefined
		? '—'
		: new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
export function callUsage(run: WorkflowRun, call: WorkflowCall) {
	const event = run.events?.findLast((e) => e.call_id === call.id && e.type === 'model.usage');
	const usage = {
		...(event?.data ?? {}),
		...(call.usage ?? {}),
		...(call.execution?.native_usage?.inclusive ?? {})
	};
	const input = count(usage.prompt_tokens ?? usage.input_tokens);
	const output = count(usage.completion_tokens ?? usage.output_tokens);
	const total =
		input !== undefined && output !== undefined
			? input + output
			: (count(usage.total_tokens) ??
				(input !== undefined || output !== undefined ? (input ?? 0) + (output ?? 0) : undefined));
	return {
		input,
		output,
		total,
		cacheRead: count(usage.cache_read_tokens),
		cacheWrite: count(usage.cache_write_tokens),
		reported: input !== undefined && output !== undefined,
		complete:
			input !== undefined &&
			output !== undefined &&
			call.usage?.native_tree_total_complete !== false
	};
}

export function nativeTotals(calls: WorkflowCall[]) {
	const trees = calls.map((c) => c.execution?.native_usage);
	const sum = (scope: 'self' | 'children') => {
		const known = trees
			.map((t) => count(t?.[scope].total_tokens))
			.filter((v): v is number => v !== undefined);
		return known.length ? known.reduce((a, b) => a + b, 0) : undefined;
	};
	return {
		observed: trees.filter(Boolean).length,
		self: sum('self'),
		children: sum('children'),
		childCount: trees.reduce((n, t) => n + (t?.child_count ?? 0), 0),
		complete: trees.every((t) => !!t?.complete),
		metered: trees.filter((t) => t?.complete).length
	};
}
export function summarizeUsage(run: WorkflowRun, calls = run.calls) {
	const values = calls.map((c) => callUsage(run, c));
	const sum = (key: 'input' | 'output' | 'total' | 'cacheRead' | 'cacheWrite') => {
		const known = values.map((v) => v[key]).filter((v): v is number => v !== undefined);
		return known.length ? known.reduce((a, b) => a + b, 0) : undefined;
	};
	const metered = values.filter((v) => v.complete).length;
	const reported = values.filter((v) => v.reported).length;
	return {
		input: sum('input'),
		output: sum('output'),
		total: sum('total'),
		cacheRead: sum('cacheRead'),
		cacheWrite: sum('cacheWrite'),
		calls: calls.length,
		metered,
		reported,
		unreported: calls.length - reported,
		missing: calls.length - metered,
		partial: metered < calls.length
	};
}
export function usageLabel(summary: ReturnType<typeof summarizeUsage>) {
	return `${summary.partial && summary.total !== undefined ? '≥ ' : ''}${tokenNumber(summary.total)}`;
}
