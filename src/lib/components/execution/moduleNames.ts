import type { WorkflowRun, Workflow } from '$lib/apis/execution';
import { planningBinding, reviewingBinding } from './nodeProgram';

export function displayModuleName(base: string, name = '') {
	const alias = name
		.trim()
		.replace(/^(?:Executor\s+\d+(?:\.\d+)?|Controller|Reviewer)\s*·\s*/i, '');
	const generic = /^(?:Executor(?:\s+\d+(?:\.\d+)?)?|Worker(?:[ _-]+(?:[A-Z]|\d+))?|Module)$/i;
	const defaults =
		base === 'Controller'
			? /^(?:Manager|Controller|Coordinator|Lead)$/i
			: base === 'Reviewer'
				? /^Reviewer$/i
				: /^$/;
	return !alias || generic.test(alias) || defaults.test(alias)
		? base
		: `${base} · ${alias}`.slice(0, 100);
}

export function moduleName(run: WorkflowRun, identity: string) {
	if (identity.includes('/'))
		return run.calls.find((c) => c.participant_id === identity)?.name ?? identity;
	if (run.module_names?.[identity]) return run.module_names[identity];
	const controller =
		planningBinding(run.plan) ??
		planningBinding(run.template?.graph) ??
		run.calls.find((c) => c.kind === 'decision')?.participant_id;
	const reviewer =
		reviewingBinding(run.plan) ??
		reviewingBinding(run.template?.graph) ??
		run.calls.find((c) => c.kind === 'review')?.participant_id;
	const roles = run.template?.roles ?? [];
	const alias = roles.find((r) => r.id === identity)?.name ?? '';
	if (alias === 'Controller' || alias === 'Reviewer') return alias;
	if (identity === controller) return displayModuleName('Controller', alias);
	if (identity === reviewer) return displayModuleName('Reviewer', alias);
	const ids = [
		...new Set([
			...roles.map((r) => r.id),
			...(run.participants ?? []).map((p) => p.id),
			...run.calls.map((c) => c.participant_id)
		])
	].filter((id) => id !== controller && id !== reviewer);
	return displayModuleName(
		`Executor ${String(Math.max(0, ids.indexOf(identity)) + 1).padStart(2, '0')}`,
		alias
	);
}

export function nextExecutorRole(roles: NonNullable<Workflow['roles']>, reserved?: string[]) {
	const core = new Set(reserved ?? ['manager', 'controller', 'reviewer']);
	const executors = roles.filter((r) => !core.has(r.id));
	const existing = executors.flatMap((r) =>
		[r.id.match(/^executor_(\d+)(?:_|$)/), r.name.match(/^Executor (\d+)(?: ·|$)/)]
			.filter((m): m is RegExpMatchArray => !!m)
			.map((m) => Number(m[1]))
	);
	const number = Math.max(executors.length, 0, ...existing) + 1;
	return {
		// Readable order is separate from identity: replacement slots cannot inherit deleted contexts.
		id: `executor_${String(number).padStart(2, '0')}_${crypto.randomUUID().slice(0, 8)}`,
		name: `Executor ${String(number).padStart(2, '0')}`,
		instructions:
			'Execute only the assigned task with explicit inputs and your own retained dialogue. Return an attributed report.'
	};
}
