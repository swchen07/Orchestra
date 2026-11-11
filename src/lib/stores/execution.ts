import type { Model } from '$lib/stores';
import type { FrameworkId, Project } from '$lib/apis/execution';
export function modelsForProject(models: Model[], project: Project | null): Model[] {
	return project
		? models.filter((model) =>
				project.data.execution_project.provider_ids.includes(model.execution?.provider_id ?? '')
			)
		: models;
}
export function providerOf(model: Pick<Model, 'execution'> | undefined): string {
	return model?.execution?.provider_id ?? '';
}
export function upgradeModelIds(models: Model[], ids: string[]): string[] {
	return ids.map((id) => {
		if (models.some((model) => model.id === id) || !id.startsWith('execution:claude-code:'))
			return id;
		try {
			const native = decodeURIComponent(id.slice('execution:claude-code:'.length));
			return (
				models.find(
					(model) =>
						model.execution?.framework_id === 'claude-code' &&
						model.execution.current_provider &&
						model.execution.model_id === native
				)?.id ?? id
			);
		} catch {
			return id;
		}
	});
}
export const frameworkNames: Record<FrameworkId, string> = {
	direct: 'Direct',
	hermes: 'Hermes',
	'claude-code': 'Claude Code',
	codex: 'Codex',
	dsh: 'DSH'
};
export function frameworkOf(model: Pick<Model, 'execution'> | undefined): FrameworkId {
	return model?.execution?.framework_id ?? 'direct';
}
export function modelsForFramework(models: Model[], framework: FrameworkId): Model[] {
	return models.filter((model) => frameworkOf(model) === framework);
}
export function chooseFrameworkModel(
	models: Model[],
	framework: FrameworkId,
	current: string[]
): string[] {
	const available = modelsForFramework(models, framework);
	const kept = current.filter((id) => available.some((model) => model.id === id));
	return kept.length
		? kept
		: [
				available.find((model) => model.execution?.default && model.execution?.current_provider)
					?.id ??
					available.find((model) => model.execution?.default)?.id ??
					available[0]?.id ??
					''
			];
}
