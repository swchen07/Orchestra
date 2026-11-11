import { describe, it, expect } from 'vitest';
import { displayModuleName, moduleName, nextExecutorRole } from './moduleNames';
import type { WorkflowRun } from '$lib/apis/execution';

describe('module identity names', () => {
	it('keeps a child invocation path visible instead of colliding with root executor names', () => {
		const run = {
			calls: [{ participant_id: 'team/executor_01', name: 'team · Executor 01' }],
			module_names: { 'team/executor_01': 'Executor 01' }
		} as unknown as WorkflowRun;
		expect(moduleName(run, 'team/executor_01')).toBe('team · Executor 01');
	});
	it('separates stable module identity from a generated task label', () => {
		const run = {
			template: { roles: [] },
			calls: [{ participant_id: 'collector', name: 'Join audit results' }],
			module_names: { collector: 'Executor 01.02' }
		} as unknown as WorkflowRun;
		expect(moduleName(run, 'collector')).toBe('Executor 01.02');
	});
	it('resolves legacy controller names through their graph role', () => {
		const run = {
			template: {
				roles: [{ id: 'manager', name: 'Manager' }],
				graph: { controller: { binding: 'manager' } }
			},
			calls: []
		} as unknown as WorkflowRun;
		expect(moduleName(run, 'manager')).toBe('Controller');
	});
	it('retains a user-defined purpose alias without duplicating the prefix', () => {
		expect(displayModuleName('Executor 01', 'Researcher')).toBe('Executor 01 · Researcher');
		expect(displayModuleName('Executor 01', 'Executor 01 · Researcher')).toBe(
			'Executor 01 · Researcher'
		);
		expect(displayModuleName('Executor 01.02', 'Worker B')).toBe('Executor 01.02');
		expect(displayModuleName('Executor 03', 'Reviewer')).toBe('Executor 03 · Reviewer');
	});
	it('allocates the third and subsequent executor slots instead of random role names', () => {
		const roles = [
			{ id: 'worker_b', name: 'Executor 01', instructions: '' },
			{ id: 'worker_c', name: 'Executor 02', instructions: '' }
		];
		const next = nextExecutorRole(roles);
		expect(next.id).toMatch(/^executor_03_[0-9a-f]{8}$/);
		expect(next.name).toBe('Executor 03');
		expect(nextExecutorRole([...roles, next]).name).toBe('Executor 04');
		expect(nextExecutorRole(roles).id).not.toBe(next.id);
		expect(nextExecutorRole(roles.map((r) => ({ ...r, name: 'Custom alias' }))).name).toBe(
			'Executor 03'
		);
		expect(
			nextExecutorRole([{ id: 'reviewer', name: 'Reviewer', instructions: '' }], []).name
		).toBe('Executor 02');
	});
});
