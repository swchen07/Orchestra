<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { CallableModule, Workflow, WorkflowOverrides } from '$lib/apis/execution';
	import ParticipantPicker from './ParticipantPicker.svelte';
	import ModulePicker from './ModulePicker.svelte';
	export let workflow: Workflow | null = null;
	export let participants: CallableModule[] = [];
	export let roleId = '';
	export let overrides: WorkflowOverrides | null | undefined = null;
	export let selectedModels: string[] = [];
	export let profileId = '';
	export let effort = '';
	export let disabled = false;
	const dispatch = createEventDispatcher<{
		change: WorkflowOverrides;
		profileChange: string;
		effortChange: string;
	}>();
	let picker: ParticipantPicker;
	let modulePicker: ModulePicker;
	let bindingId = '';
	let draftModels: string[] = [];
	let draftProfile = '';
	let draftEffort = '';
	let loadedSelection = '';
	$: if (workflow && !workflow.roles?.some((role) => role.id === roleId))
		roleId = workflow.lead_role_id ?? workflow.roles?.[0]?.id ?? '';
	$: edits = overrides?.workflow_id === workflow?.id ? (overrides?.roles ?? {}) : {};
	$: edit = edits[roleId];
	$: participant = participants.find(
		(p) => p.id === (edit?.participant_id ?? workflow?.bindings?.[roleId])
	);
	$: roleEffort = edit && 'effort' in edit ? edit.effort : participant?.effort;
	$: selection = JSON.stringify([workflow?.id, roleId, participant?.id, roleEffort]);
	$: if (participant && selection !== loadedSelection) {
		loadedSelection = selection;
		bindingId = participant.id;
		draftModels = participant.model_id ? [participant.model_id] : [];
		draftProfile = participant.profile_id ?? '';
		draftEffort = roleEffort ?? '';
	}
	$: leadEdit = edits[workflow?.lead_role_id ?? ''];
	$: lead = participants.find(
		(p) => p.id === (leadEdit?.participant_id ?? workflow?.bindings?.[workflow.lead_role_id ?? ''])
	);
	$: leadEffort = leadEdit && 'effort' in leadEdit ? leadEdit.effort : lead?.effort;
	$: if (lead?.model_id) {
		if (selectedModels[0] !== lead.model_id) selectedModels = [lead.model_id];
		if ((profileId ?? '') !== (lead.profile_id ?? '')) {
			profileId = lead.profile_id ?? '';
			dispatch('profileChange', profileId);
		}
		if ((effort ?? '') !== (leadEffort ?? '')) {
			effort = leadEffort ?? '';
			dispatch('effortChange', effort);
		}
	}
	function change(value: { participant_id: string; effort: string | null }) {
		if (!workflow?.id || !roleId) return;
		overrides = {
			...(overrides?.workflow_id === workflow.id ? overrides : {}),
			workflow_id: workflow.id,
			roles: { ...edits, [roleId]: value }
		};
		dispatch('change', overrides);
	}
	export const open = () => modulePicker?.open();
</script>

{#if workflow}
	<ModulePicker
		bind:this={modulePicker}
		modules={participants}
		value={participant?.id ?? ''}
		label="Role module"
		{disabled}
		on:change={(event) =>
			change({
				participant_id: event.detail,
				effort: participants.find((p) => p.id === event.detail)?.effort ?? null
			})}
	/>
{/if}
{#if participant?.model_id}
	<ParticipantPicker
		bind:this={picker}
		bind:bindingId
		bind:selectedModels={draftModels}
		bind:profileId={draftProfile}
		bind:effort={draftEffort}
		{disabled}
		on:selectionChange={(e) => change(e.detail)}
	/>
{:else if !participant}
	<span class="px-2 text-xs text-gray-500" role="status">
		{workflow ? 'Bind this role in Project → Workflows.' : 'Loading workflow roles…'}
	</span>
{/if}
