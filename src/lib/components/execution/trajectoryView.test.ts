import { describe, it, expect } from 'vitest';
import type { WorkflowRun, WorkflowEvent } from '$lib/apis/execution';
import { trajectoryRecords } from './trajectoryView';
const event = (seq: number, type: string, data = {}): WorkflowEvent => ({
	seq,
	type,
	at: 100000 + seq * 1000,
	call_id: 'a',
	title: type,
	data
});
const run = (events: WorkflowEvent[]): WorkflowRun => ({
	id: 'r',
	status: 'completed',
	created_at: 100,
	finished_at: 120,
	calls: [
		{
			id: 'a',
			participant_id: 'worker',
			name: 'Worker',
			input: 'task',
			status: 'completed',
			created_at: 100
		}
	],
	events
});
describe('categorized trajectory', () => {
	it('keeps rejected and approved reviews separate and attributes old verdict events to their Reviewer calls', () => {
		const record = run([]);
		record.calls = [
			{ ...record.calls[0], id: 'first', participant_id: 'reviewer', kind: 'review' },
			{ ...record.calls[0], id: 'second', participant_id: 'reviewer', kind: 'review' }
		];
		record.review_attempts = [
			{
				node_id: 'review',
				call_id: 'first',
				summary: 'Wrong subtotal',
				status: 'changes_requested',
				revision: 1
			},
			{
				node_id: 'review',
				call_id: 'second',
				summary: 'Corrected subtotal',
				status: 'approved',
				revision: 2
			}
		];
		record.events = [
			{
				...event(1, 'review.changes_requested', { summary: 'Wrong subtotal' }),
				call_id: undefined
			},
			{ ...event(2, 'review.feedback', { from: 'review', to: 'dispatch' }), call_id: undefined },
			{ ...event(3, 'review.approved', { summary: 'Corrected subtotal' }), call_id: undefined }
		];
		const verdicts = trajectoryRecords(record).filter((r) => r.category === 'review');
		expect(verdicts.map((r) => r.callId)).toEqual(['first', 'second']);
		expect(verdicts.map((r) => r.status)).toEqual(['changes_requested', 'completed']);
		expect(record.events.every((e) => !e.call_id)).toBe(true);
	});
	it('keeps an independent review verdict distinct from model text and execution counters', () => {
		const record = run([
			event(1, 'model.output', { text: 'review JSON' }),
			event(2, 'review.approved', { verdict: 'approve', checked_report_refs: ['r/worker'] })
		]);
		record.calls[0].kind = 'review';
		expect(trajectoryRecords(record).find((row) => row.category === 'review')).toMatchObject({
			title: 'Independent review verdict',
			status: 'completed',
			output: { verdict: 'approve', checked_report_refs: ['r/worker'] }
		});
	});
	it('pairs a tool start with its result and preserves historical payloads without displaying their state as current', () => {
		const record = run([
			event(1, 'tool.started', {
				id: 't',
				name: 'terminal',
				done: false,
				status: 'running',
				input: { command: 'pwd' }
			}),
			event(2, 'tool.completed', {
				id: 't',
				name: 'terminal',
				done: true,
				status: 'completed',
				output: 'workspace'
			})
		]);
		const rows = trajectoryRecords(record);
		expect(rows).toHaveLength(1);
		expect(rows[0]).toMatchObject({
			category: 'tool',
			status: 'completed',
			input: { command: 'pwd' },
			output: 'workspace',
			end: 102000
		});
		expect(record.events![0].data!.done).toBe(false);
	});
	it('does not reopen a settled invocation after late progress or duplicate start', () => {
		const rows = trajectoryRecords(
			run([
				event(1, 'tool.started', { id: 't', name: 'Read' }),
				event(2, 'tool.completed', { id: 't', done: true }),
				event(3, 'tool.updated', { id: 't', done: false }),
				event(4, 'tool.started', { id: 't', name: 'Read', done: false })
			])
		);
		expect(rows).toHaveLength(1);
		expect(rows[0].status).toBe('completed');
		expect(rows[0].end).toBe(102000);
	});
	it('retains sequential tool calls when a framework only supplies names', () => {
		const rows = trajectoryRecords(
			run([
				event(1, 'tool.started', { name: 'terminal' }),
				event(2, 'tool.completed', { name: 'terminal', done: true }),
				event(3, 'tool.started', { name: 'terminal' }),
				event(4, 'tool.completed', { name: 'terminal', done: true })
			])
		);
		expect(rows).toHaveLength(2);
		expect(rows.every((r) => r.status === 'completed')).toBe(true);
	});
	it('recognizes terminal native status even without a done flag', () => {
		expect(
			trajectoryRecords(run([event(1, 'tool.updated', { id: 't', status: 'completed' })]))[0].status
		).toBe('completed');
	});
	it('marks missing tool completion as unconfirmed without inventing success or timing', () => {
		const rows = trajectoryRecords(run([event(1, 'tool.started', { id: 't', done: false })]));
		expect(rows[0].status).toBe('unconfirmed');
		expect(rows[0].end).toBeUndefined();
	});
	it('keeps active tools running while their parent is live', () => {
		const record = run([event(1, 'tool.started', { id: 't', done: false })]);
		record.status = 'running';
		record.calls[0].status = 'running';
		expect(trajectoryRecords(record)[0].status).toBe('running');
	});
	it('separates replies, restoration and execution and coalesces noisy phase events', () => {
		const rows = trajectoryRecords(
			run([
				event(1, 'context.restored', { method: 'native', history_messages: 4 }),
				event(2, 'model.metadata', { actual_model: 'model' }),
				event(3, 'call.started'),
				event(4, 'call.phase'),
				{ ...event(5, 'model.output'), text: 'Answer' },
				event(6, 'call.completed')
			])
		);
		expect(rows.map((r) => r.category)).toEqual(['session', 'lifecycle', 'reply']);
		expect(rows[0].output).toEqual({
			method: 'native',
			history_messages: 4,
			actual_model: 'model'
		});
		expect(rows[1].events).toHaveLength(3);
	});
});
