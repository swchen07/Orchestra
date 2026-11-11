<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getProjectParticipants,
		saveProjectParticipants,
		type Participant
	} from '$lib/apis/execution';
	export let projectId: string;
	let participants: Participant[] = [];
	let selected: string[] = [];
	let error = '';
	let saved = '';
	let busy = false;
	let loading = true;
	onMount(async () => {
		try {
			const result = await getProjectParticipants(projectId);
			participants = result.available;
			selected = result.selected_ids;
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});
	function toggle(id: string, checked: boolean) {
		selected = checked ? [...selected, id] : selected.filter((value) => value !== id);
		saved = '';
	}
	async function save() {
		busy = true;
		error = '';
		try {
			await saveProjectParticipants(projectId, selected);
			saved = 'Participants saved for new chats and workflows.';
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
</script>

<section class="max-w-4xl space-y-5">
	<div>
		<h2 class="font-medium">Participants</h2>
		<p class="mt-2 text-sm text-gray-500">
			Choose which models and agent presets this project can use. Add provider connections in
			Project settings; create named participants in Configuration → Presets.
		</p>
	</div>
	<div class="flex gap-4 text-sm text-gray-500">
		<button on:click={() => (selected = participants.map((p) => p.id))}>Select all</button><button
			on:click={() => (selected = [])}>Clear selection</button
		>
	</div>
	<div class="divide-y rounded-lg border dark:divide-gray-700 dark:border-gray-700">
		{#each participants as p}<label class="flex cursor-pointer items-center gap-3 px-4 py-3"
				><input
					type="checkbox"
					checked={selected.includes(p.id)}
					on:change={(event) => toggle(p.id, event.currentTarget.checked)}
				/><span class="min-w-0 text-sm"
					><span class="block font-medium">{p.name}</span><span class="text-xs text-gray-500"
						>{p.provider_name} · {p.model_name}</span
					></span
				></label
			>{:else}<p class="p-5 text-sm text-gray-500">{loading ? 'Loading participants…' : 'Add a provider to this project first.'}</p>{/each}
	</div>
	{#if error}<p role="alert" class="text-sm text-red-600">{error}</p>{/if}{#if saved}<p
			role="status"
			class="text-sm text-green-700"
		>
			{saved}
		</p>{/if}
	<button
		disabled={busy || loading}
		class="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50"
		on:click={save}>{busy ? 'Saving…' : 'Save participants'}</button
	>
</section>
