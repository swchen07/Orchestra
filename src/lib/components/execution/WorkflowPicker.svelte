<script lang="ts">
	import { getContext, createEventDispatcher } from 'svelte';
	import { writable, type Writable } from 'svelte/store';
	import { user } from '$lib/stores';
	import {
		getWorkflows,
		getModules,
		type Project,
		type Workflow,
		type CallableModule
	} from '$lib/apis/execution';
	import { createRequestGate } from '$lib/utils/requestGate';
	import ComposerSelect from './ComposerSelect.svelte';
	const project =
		getContext<Writable<Project | null>>('executionProject') ?? writable<Project | null>(null);
	const requests = createRequestGate();
	const dispatch = createEventDispatcher<{
		workflowChange: string;
		profileChange: string;
		effortChange: string;
	}>();
	export let workflowId = '';
	export let selectedModels: string[] = [];
	export let profileId = '';
	export let effort = '';
	export let disabled = false;
	export let workflow: Workflow | null = null;
	export let participants: CallableModule[] = [];
	let loadedProject: string | null = null;
	let loadedWorkflow: string | null = null;
	let workflows: Workflow[] = [];
	$: workflow = workflows.find((item) => item.id === workflowId) ?? null;
	$: if (
		$user?.role === 'admin' &&
		(($project?.id ?? '') !== loadedProject || (workflowId ?? '') !== loadedWorkflow)
	) {
		loadedProject = $project?.id ?? '';
		loadedWorkflow = workflowId ?? '';
		void load(loadedProject, loadedWorkflow);
	}
	async function load(id: string, selected: string) {
		if ($user?.role !== 'admin') return;
		const current = requests.begin();
		try {
			const result = await Promise.all([getWorkflows(id, selected), getModules(id)]);
			if (current()) [workflows, participants] = result;
		} catch {
			if (current()) workflows = [];
		}
	}
	function change(id: string) {
		workflowId = id;
		const workflow = workflows.find((item) => item.id === id);
		const lead = participants.find(
			(item) => item.id === workflow?.bindings?.[workflow.lead_role_id ?? '']
		);
		if (lead?.model_id) {
			selectedModels = [lead.model_id];
			if ((profileId ?? '') !== (lead.profile_id ?? '')) {
				profileId = lead.profile_id ?? '';
				dispatch('profileChange', profileId);
			}
			effort = lead.effort ?? '';
			dispatch('effortChange', effort);
		}
		dispatch('workflowChange', id);
	}
</script>

{#if $user?.role === 'admin'}
	<ComposerSelect
		label="Workflow"
		value={workflowId ?? ''}
		{disabled}
		placeholder="Workflow"
		options={[
			{ value: '', label: 'None' },
			...workflows.map((w) => ({
				value: w.id!,
				label: w.name + (w.archived ? ' (Archived)' : ''),
				disabled: !w.available,
				description: w.available ? undefined : w.unavailable_reason
			}))
		]}
		on:change={(e) => change(e.detail)}
	/>
{/if}
