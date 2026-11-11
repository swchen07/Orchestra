import { describe, it, expect } from 'vitest';
import type { WorkflowRun, WorkflowCall } from '$lib/apis/execution';
import { callUsage, summarizeUsage, usageLabel, nativeTotals } from './workflowUsage';
const call = (id: string, usage?: Record<string, number>): WorkflowCall => ({
	id,
	name: id,
	participant_id: id,
	input: '',
	status: 'completed',
	created_at: 1,
	usage
});
const run = (calls: WorkflowCall[]): WorkflowRun => ({
	id: 'r',
	calls,
	status: 'completed',
	created_at: 1
});
describe('reported workflow token usage', () => {
	it('uses the inclusive native snapshot once and keeps self and children separate', () => {
		const c = call('native', { prompt_tokens: 10, completion_tokens: 2 });
		c.execution = {
			native_usage: {
				root_id: 'root',
				complete: true,
				child_count: 2,
				warnings: [],
				agents: [],
				timings: {},
				self: { total_tokens: 12 },
				children: { total_tokens: 28 },
				inclusive: { prompt_tokens: 32, completion_tokens: 8, total_tokens: 40 }
			}
		};
		const record = run([c]);
		expect(summarizeUsage(record).total).toBe(40);
		expect(nativeTotals([c])).toMatchObject({
			self: 12,
			children: 28,
			childCount: 2,
			complete: true
		});
		expect(nativeTotals([call('legacy')])).toMatchObject({
			self: undefined,
			children: undefined,
			complete: false
		});
	});
	it('counts Manager, worker and Reviewer invocations once despite their summary views', () => {
		const record = run([
			{ ...call('manager', { prompt_tokens: 100, completion_tokens: 10 }), kind: 'decision' },
			call('worker', { prompt_tokens: 200, completion_tokens: 20 }),
			{ ...call('reviewer', { prompt_tokens: 300, completion_tokens: 30 }), kind: 'review' }
		]);
		record.review = { status: 'approved', revision: 1, call_id: 'reviewer' };
		expect(summarizeUsage(record)).toMatchObject({ total: 660, calls: 3, metered: 3 });
	});
	it('does not count cache reads twice', () => {
		const r = run([call('a', { prompt_tokens: 100, completion_tokens: 5, cache_read_tokens: 70 })]);
		expect(summarizeUsage(r)).toMatchObject({
			input: 100,
			output: 5,
			total: 105,
			cacheRead: 70,
			partial: false
		});
	});
	it('keeps an unmetered module unknown rather than reporting zero', () => {
		const r = run([call('dsh')]);
		expect(callUsage(r, r.calls[0]).total).toBeUndefined();
		expect(usageLabel(summarizeUsage(r))).toBe('—');
	});
	it('labels a known subtotal when only some agents return usage', () => {
		const r = run([call('a', { prompt_tokens: 100, completion_tokens: 5 }), call('b')]);
		expect(summarizeUsage(r)).toMatchObject({ total: 105, metered: 1, missing: 1, partial: true });
		expect(usageLabel(summarizeUsage(r))).toBe('≥ 105');
	});
	it('uses the latest usage snapshot once, never sums streaming counters', () => {
		const r = run([call('a')]);
		r.events = [
			{
				seq: 1,
				at: 1,
				type: 'model.usage',
				title: 'Usage',
				call_id: 'a',
				data: { prompt_tokens: 40, completion_tokens: 2 }
			},
			{
				seq: 2,
				at: 2,
				type: 'model.usage',
				title: 'Usage',
				call_id: 'a',
				data: { prompt_tokens: 50, completion_tokens: 3 }
			}
		];
		expect(summarizeUsage(r).total).toBe(53);
	});
	it('recovers usage for old calls with an empty final usage object', () => {
		const r = run([call('a', {})]);
		r.events = [
			{
				seq: 1,
				at: 1,
				type: 'model.usage',
				title: 'Usage',
				call_id: 'a',
				data: { prompt_tokens: 12, completion_tokens: 4 }
			}
		];
		expect(summarizeUsage(r).total).toBe(16);
	});
	it('can aggregate one role without including other roles or tool events', () => {
		const r = run([
			call('a', { prompt_tokens: 20, completion_tokens: 1 }),
			call('b', { prompt_tokens: 80, completion_tokens: 2 })
		]);
		r.events = [
			{
				seq: 1,
				at: 1,
				type: 'tool.completed',
				title: 'Tool',
				call_id: 'a',
				data: { prompt_tokens: 999 }
			}
		];
		expect(summarizeUsage(r, [r.calls[0]]).total).toBe(21);
	});
	it('recognizes reported zeros while rejecting invalid and absent values', () => {
		const r = run([
			call('a', { prompt_tokens: 0, completion_tokens: 0 }),
			call('b', { prompt_tokens: -1, completion_tokens: NaN })
		]);
		expect(callUsage(r, r.calls[0]).complete).toBe(true);
		expect(callUsage(r, r.calls[1]).total).toBeUndefined();
	});
});

it('includes a known partial counter in the lower bound without claiming complete metering', () => {
	const r = run([call('a', { prompt_tokens: 100 })]);
	expect(summarizeUsage(r)).toMatchObject({ input: 100, total: 100, metered: 0, partial: true });
	expect(usageLabel(summarizeUsage(r))).toBe('≥ 100');
});

it('distinguishes reported counters from complete native totals without dropping the subtotal', () => {
	const partial = call('hermes', { prompt_tokens: 13, completion_tokens: 5, cache_read_tokens: 4 });
	partial.usage = { ...partial.usage, native_tree_total_complete: false };
	const r = run([
		call('dsh', { prompt_tokens: 10, completion_tokens: 2 }),
		partial,
		call('pending')
	]);
	expect(summarizeUsage(r)).toMatchObject({
		total: 30,
		reported: 2,
		metered: 1,
		unreported: 1,
		partial: true
	});
	expect(usageLabel(summarizeUsage(r))).toBe('≥ 30');
});
