<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import { writable, type Writable } from 'svelte/store';
	import { models, user } from '$lib/stores';
	import { modelsForProject, frameworkNames } from '$lib/stores/execution';
	import {
		getParticipants,
		getNativeProfiles,
		getProjectProfiles,
		type Participant,
		type Project,
		type ExecutionProfile
	} from '$lib/apis/execution';
	import { createRequestGate } from '$lib/utils/requestGate';
	import ComposerSelect from './ComposerSelect.svelte';
	const project =
		getContext<Writable<Project | null>>('executionProject') ?? writable<Project | null>(null);
	const requests = createRequestGate();
	const dispatch = createEventDispatcher<{
		profileChange: string;
		effortChange: string;
		selectionChange: { participant_id: string; effort: string | null };
	}>();
	export let selectedModels: string[] = [];
	export let profileId = '';
	export let effort = '';
	export let disabled = false;
	export let bindingId = '';
	let participants: Participant[] | null = null;
	let profiles: ExecutionProfile[] = [];
	let loadedProject: string | null = null;
	let modelControl: ComposerSelect;
	let error = '';
	$: fallback = modelsForProject($models, $project).map((model) => ({
		id: 'model:' + model.id,
		model_id: model.id,
		profile_id: null,
		effort: null,
		name: model.name,
		framework_id: model.execution?.framework_id ?? 'direct',
		provider_id: model.execution?.provider_id ?? 'direct',
		provider_name: model.execution?.provider_name ?? 'Model API',
		model_name: model.name,
		builtin: true
	}));
	$: choices =
		participants ??
		fallback.filter(
			(p) =>
				!$project?.data?.execution_participants ||
				$project.data.execution_participants.includes(p.id)
		);
	$: selectedModel = $models.find((m) => m.id === selectedModels[0]);
	$: framework =
		selectedModel?.execution?.framework_id ??
		choices.find((p) => p.model_id === selectedModels[0])?.framework_id ??
		'direct';
	$: provider =
		selectedModel?.execution?.provider_id ??
		choices.find((p) => p.model_id === selectedModels[0])?.provider_id ??
		'direct';
	$: frameworks = Object.entries(frameworkNames).filter(([id]) =>
		choices.some((p) => p.framework_id === id)
	);
	$: providers = Array.from(
		new Map(
			choices
				.filter((p) => p.framework_id === framework)
				.map((p) => [p.provider_id ?? 'direct', p.provider_name ?? 'Model API'])
		).entries()
	);
	$: modelChoices = Array.from(
		new Map(
			choices
				.filter((p) => p.framework_id === framework && (p.provider_id ?? 'direct') === provider)
				.map((p) => [p.model_id, p.model_name ?? p.name])
		).entries()
	);
	$: compatible = profiles.filter(
		(p) =>
			p.available &&
			p.framework_id === framework &&
			(!$project?.data?.execution_participants ||
				choices.some((c) => c.model_id === selectedModels[0] && c.profile_id === p.id))
	);
	$: defaultAllowed =
		!$project?.data?.execution_participants ||
		choices.some((p) => p.model_id === selectedModels[0] && !p.profile_id);
	$: efforts = selectedModel?.execution?.effort_options ?? [];
	$: if ($user?.role === 'admin' && ($project?.id ?? '') !== loadedProject) {
		loadedProject = $project?.id ?? '';
		void load(loadedProject);
	}
	async function load(id: string) {
		const current = requests.begin();
		try {
			const result = await Promise.all([
				getParticipants(id),
				id ? getProjectProfiles(id) : getNativeProfiles()
			]);
			if (current()) {
				[participants, profiles] = result;
				error = '';
			}
		} catch {
			if (current()) {
				participants = [];
				error = 'Could not load participants';
			}
		}
	}
	function chooseModel(id: string) {
		const row =
			choices.find((p) => p.model_id === id && !p.profile_id) ??
			choices.find((p) => p.model_id === id);
		if (!row) return;
		selectedModels = [id];
		profileId = row.profile_id ?? '';
		effort = row.effort ?? '';
		bindingId = row.id;
		dispatch('profileChange', profileId);
		dispatch('effortChange', effort);
		notifySelection();
	}
	function notifySelection() {
		const matches = choices.filter(
			(p) => p.model_id === selectedModels[0] && (p.profile_id ?? '') === (profileId ?? '')
		);
		const row = matches.find((p) => p.id === bindingId) ?? matches[0];
		if (row) {
			bindingId = row.id;
			dispatch('selectionChange', { participant_id: row.id, effort: effort || null });
		}
	}
	function chooseFramework(id: string) {
		const row =
			choices.find(
				(p) => p.framework_id === id && $models.find((m) => m.id === p.model_id)?.execution?.default
			) ?? choices.find((p) => p.framework_id === id);
		if (row) chooseModel(row.model_id);
	}
	function chooseProvider(id: string) {
		const row = choices.find(
			(p) => p.framework_id === framework && (p.provider_id ?? 'direct') === id
		);
		if (row) chooseModel(row.model_id);
	}
	export const open = () => modelControl?.open();
</script>

<div
	class="execution-selection flex shrink-0 items-center gap-0.5"
	aria-label="Execution selection"
>
	<ComposerSelect
		label="Framework"
		value={framework}
		{disabled}
		options={frameworks.map(([value, label]) => ({ value, label }))}
		on:change={(e) => chooseFramework(e.detail)}
	/>
	<ComposerSelect
		label="Provider"
		value={provider}
		{disabled}
		options={providers.map(([value, label]) => ({ value, label }))}
		on:change={(e) => chooseProvider(e.detail)}
	/>
	<ComposerSelect
		label="Model"
		value={selectedModels[0] ?? ''}
		{disabled}
		prominent
		searchable
		bind:this={modelControl}
		options={modelChoices.map(([value, label]) => ({ value, label }))}
		on:change={(e) => chooseModel(e.detail)}
	/>
	<ComposerSelect
		label="Preset"
		value={profileId ?? ''}
		{disabled}
		placeholder="Preset"
		options={[
			{ value: '', label: 'Default', disabled: !defaultAllowed },
			...compatible.map((p) => ({ value: p.id, label: p.name }))
		]}
		on:change={(e) => {
			profileId = e.detail;
			dispatch('profileChange', profileId);
			notifySelection();
		}}
	/>
	{#if efforts.length}
		<ComposerSelect
			label="Effort"
			value={effort ?? ''}
			{disabled}
			placeholder="Effort"
			options={[
				{ value: '', label: 'Default' },
				...efforts.map((value) => ({
					value,
					label: value.charAt(0).toUpperCase() + value.slice(1)
				}))
			]}
			on:change={(e) => {
				effort = e.detail;
				dispatch('effortChange', effort);
				notifySelection();
			}}
		/>
	{/if}
	{#if error}<span role="alert" class="px-2 text-xs text-red-600">{error}</span>{/if}
</div>

<style>
	@media (max-width: 640px) {
		.execution-selection :global(button[aria-label='Model']) {
			order: -2;
		}
		.execution-selection :global(button[aria-label='Effort']) {
			order: -1;
		}
	}
</style>
