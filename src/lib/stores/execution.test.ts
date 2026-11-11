import { describe, it, expect } from 'vitest';
import type { Model } from '$lib/stores';
import {
	chooseFrameworkModel,
	modelsForFramework,
	modelsForProject,
	upgradeModelIds
} from './execution';
import type { Project } from '$lib/apis/execution';
const models = [
	{ id: 'direct' },
	{ id: 'hermes-agent', execution: { framework_id: 'hermes', model_id: 'gpt-real' } },
	{ id: 'cc-pro', execution: { framework_id: 'claude-code', default: true } },
	{ id: 'cc-flash', execution: { framework_id: 'claude-code' } }
] as Model[];
describe('framework then model', () => {
	it('restores legacy Claude model choices using the current provider identity', () => {
		const choices = [
			{
				id: 'scoped-model',
				execution: {
					framework_id: 'claude-code',
					provider_id: 'one',
					current_provider: true,
					model_id: 'old-model'
				}
			}
		] as Model[];
		expect(upgradeModelIds(choices, ['execution:claude-code:old-model'])).toEqual(['scoped-model']);
		expect(upgradeModelIds(choices, ['execution:claude-code:unavailable'])).toEqual([
			'execution:claude-code:unavailable'
		]);
	});
	it('keeps API models separate from native agent model catalogs', () => {
		expect(modelsForFramework(models, 'direct').map((m) => m.id)).toEqual(['direct']);
		expect(modelsForFramework(models, 'hermes').map((m) => m.id)).toEqual(['hermes-agent']);
	});
	it('never carries a Hermes model into Claude Code', () => {
		expect(chooseFrameworkModel(models, 'claude-code', ['hermes-agent'])).toEqual(['cc-pro']);
		expect(chooseFrameworkModel(models, 'claude-code', ['cc-flash'])).toEqual(['cc-flash']);
	});
	it('a project limits reply choices to providers it has added', () => {
		const choices = [
			{ id: 'a', execution: { framework_id: 'claude-code', provider_id: 'one' } },
			{ id: 'b', execution: { framework_id: 'claude-code', provider_id: 'two' } }
		] as Model[];
		const project = { data: { execution_project: { provider_ids: ['two'] } } } as Project;
		expect(modelsForProject(choices, project).map((model) => model.id)).toEqual(['b']);
		expect(chooseFrameworkModel(modelsForProject(choices, project), 'claude-code', ['a'])).toEqual([
			'b'
		]);
	});
});
