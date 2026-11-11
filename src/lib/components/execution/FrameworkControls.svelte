<script lang="ts">
	import { models, user } from '$lib/stores';
	import { frameworkOf } from '$lib/stores/execution';
	import { getContext, createEventDispatcher } from 'svelte';
	import { writable, type Writable } from 'svelte/store';
	import {
		getProjectProfiles,
		getNativeProfiles,
		type ExecutionProfile,
		type Project
	} from '$lib/apis/execution';
	import { createRequestGate } from '$lib/utils/requestGate';
	import AdjustmentsHorizontal from '$lib/components/icons/AdjustmentsHorizontal.svelte';
	const project =
		getContext<Writable<Project | null>>('executionProject') ?? writable<Project | null>(null);
	const dispatch = createEventDispatcher<{ profileChange: string }>();
	const requests = createRequestGate();
	export let selectedModels: string[] = [];
	export let profileId = '';
	export let disabled = false;
	let profiles: ExecutionProfile[] = [];
	let loadedProject: string | null = null;
	let error = '';
	$: chosen = $models.find((model) => model.id === selectedModels[0]);
	$: framework = frameworkOf(chosen);
	$: compatible = profiles.filter(
		(profile) => profile.available && profile.framework_id === framework
	);
	$: if ($user?.role === 'admin' && ($project?.id ?? '') !== loadedProject) {
		loadedProject = $project?.id ?? '';
		void load(loadedProject);
	}
	async function load(id: string) {
		const current = requests.begin();
		if ($user?.role !== 'admin') return;
		try {
			const result = await (id ? getProjectProfiles(id) : getNativeProfiles());
			if (current()) {
				profiles = result;
				error = '';
			}
		} catch {
			if (current()) error = 'Presets could not be loaded.';
		}
	}
	function change(value: string) {
		profileId = value;
		dispatch('profileChange', value);
	}
</script>

{#if $user?.role === 'admin'}
	<details class="relative self-center text-sm text-gray-500 dark:text-gray-400">
		<summary
			aria-label="Participant options"
			class="list-none cursor-pointer rounded-lg p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800"
			><AdjustmentsHorizontal className="size-4" /></summary
		>
		<div
			class="absolute bottom-full right-0 z-30 mb-3 w-72 max-w-[85vw] rounded-xl border border-gray-200 bg-white p-4 text-left shadow-lg dark:border-gray-700 dark:bg-gray-900 space-y-3"
		>
			<div>
				<p class="font-medium text-gray-900 dark:text-gray-100">Participant options</p>
				<p class="mt-1 text-xs break-words">
					{chosen?.execution?.framework_name ?? 'Direct'} · {chosen?.execution?.provider_name ??
						'Model API'}
				</p>
			</div>
			<label class="block text-xs font-medium"
				>Preset
				<select
					aria-label="Agent preset"
					value={profileId ?? ''}
					{disabled}
					on:change={(event) => change(event.currentTarget.value)}
					class="mt-2 block w-full rounded-lg border border-gray-200 bg-transparent px-2 py-2 text-sm dark:border-gray-700"
				>
					<option value="">Default</option>
					{#each compatible as profile}<option value={profile.id}
							>{profile.name}{profile.kind.startsWith('native') ? ' · Native' : ''}</option
						>{/each}
				</select>
			</label>
			{#if error}<p role="alert" class="text-xs text-red-600">{error}</p>{/if}
			{#if profileId}<p class="text-xs leading-5">
					{profiles.find((profile) => profile.id === profileId)?.note ??
						'Role instructions apply to this conversation.'}
				</p>{/if}
			{#if framework === 'hermes'}<p class="text-xs leading-5">Hermes uses the gateway's existing tools and working environment.</p>
			{:else if framework !== 'direct'}<p class="text-xs leading-5">
					Native tools use a read-only boundary. Presets do not grant additional permissions.
				</p>{/if}
		</div>
	</details>
{/if}
