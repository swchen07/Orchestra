<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getExecutionCatalog,
		getNativeProfiles,
		getParticipants,
		saveParticipant,
		type Catalog,
		type FrameworkId,
		type ExecutionProfile,
		type Participant
	} from '$lib/apis/execution';
	import { frameworkNames } from '$lib/stores/execution';
	let catalog: Catalog | null = null;
	let profiles: ExecutionProfile[] = [];
	let participants: Participant[] = [];
	let modelId = '';
	let profileId = '';
	let effort = '';
	let name = '';
	let filter = '';
	let error = '';
	let saved = '';
	let busy = false;
	$: framework = catalog?.models.find((m) => m.id === modelId)?.execution?.framework_id;
	$: compatible = profiles.filter((p) => p.available && p.framework_id === framework);
	$: efforts = catalog?.models.find((m) => m.id === modelId)?.execution?.effort_options ?? [];
	async function load() {
		[catalog, profiles, participants] = await Promise.all([
			getExecutionCatalog(),
			getNativeProfiles(),
			getParticipants()
		]);
	}
	async function save() {
		busy = true;
		error = '';
		saved = '';
		try {
			await saveParticipant({
				id: '',
				name,
				model_id: modelId,
				profile_id: profileId || null,
				effort: effort || null
			});
			await load();
			saved = 'Participant saved. It is now available to workflows.';
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
	onMount(() => {
		void load().catch((e) => (error = String(e)));
	});
</script>

<div class="mx-auto max-w-5xl space-y-8 p-5 md:p-8">
	<div>
		<h1 class="text-2xl font-semibold">Presets</h1>
		<p class="mt-2 text-sm text-gray-500">
			Native profiles, agent definitions and reusable participant bindings.
		</p>
	</div>
	<section class="space-y-4">
		<div class="flex flex-wrap items-center justify-between gap-3">
			<h2 class="font-medium">Native presets</h2>
			<select
				aria-label="Preset framework"
				bind:value={filter}
				class="rounded-lg border bg-transparent px-3 py-2 text-sm dark:border-gray-700"
				><option value="">All frameworks</option
				>{#each Object.entries(frameworkNames) as [id, label]}<option value={id}>{label}</option
					>{/each}</select
			>
		</div>
		<div class="divide-y rounded-lg border dark:divide-gray-700 dark:border-gray-700">
			{#each profiles.filter((p) => p.kind !== 'role' && (!filter || p.framework_id === filter)) as profile}<div
					class="px-4 py-3"
				>
					<div class="flex flex-wrap items-center gap-2">
						<span class="text-sm font-medium">{profile.name}</span><span
							class="text-xs text-gray-500"
							>{frameworkNames[profile.framework_id as FrameworkId] ?? profile.framework_id} · {profile.source}</span
						>
					</div>
					<p class="mt-1 text-xs leading-5 text-gray-500">
						{profile.note ?? profile.execution_mode}
					</p>
				</div>{:else}<p class="px-4 py-5 text-sm text-gray-500">
					No native presets found for this framework. Its default configuration remains available.
				</p>{/each}
		</div>
	</section>
	<form on:submit|preventDefault={save} class="space-y-4 border-t pt-7 dark:border-gray-700">
		<h2 class="font-medium">Save a participant</h2>
		<p class="text-sm text-gray-500">
			Choose a model and optional preset once, then reuse the participant in workflows.
		</p>
		<div class="grid gap-4 sm:grid-cols-2">
			<label class="text-sm"
				>Name<input
					required
					aria-label="Participant name"
					bind:value={name}
					placeholder="Code reviewer"
					class="mt-2 block w-full rounded-lg border bg-transparent px-3 py-2 dark:border-gray-700"
				/></label
			><label class="text-sm"
				>Model<select
					required
					aria-label="Participant model"
					bind:value={modelId}
					on:change={() => {
						profileId = '';
						effort = '';
					}}
					class="mt-2 block w-full rounded-lg border bg-transparent px-3 py-2 dark:border-gray-700"
					><option value="" disabled>Select a model</option
					>{#each catalog?.models ?? [] as model}<option value={model.id}
							>{model.execution?.framework_name ?? 'Direct'} · {model.execution?.provider_name ??
								''} · {model.name}</option
						>{/each}</select
				></label
			>
		</div>
		<label class="block text-sm"
			>Preset<select
				aria-label="Participant preset"
				bind:value={profileId}
				class="mt-2 block w-full rounded-lg border bg-transparent px-3 py-2 dark:border-gray-700"
				><option value="">Default</option>{#each compatible as profile}<option value={profile.id}
						>{profile.name} · {profile.kind === 'role' ? 'Role' : 'Native'}</option
					>{/each}</select
			></label
		>
		{#if efforts.length}<label class="block text-sm"
				>Effort<select
					aria-label="Participant effort"
					bind:value={effort}
					class="mt-2 block w-full rounded-lg border bg-transparent px-3 py-2 dark:border-gray-700"
					><option value="">Native default</option>{#each efforts as value}<option {value}
							>{value}</option
						>{/each}</select
				></label
			>{/if}
		{#if error}<p role="alert" class="text-sm text-red-600">{error}</p>{/if}{#if saved}<p
				role="status"
				class="text-sm text-green-700"
			>
				{saved}
			</p>{/if}
		<div class="flex justify-end">
			<button
				disabled={busy}
				class="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
				>Save participant</button
			>
		</div>
	</form>
	{#if participants.some((p) => !p.builtin)}<section>
			<h2 class="mb-4 font-medium">Saved participants</h2>
			<div class="divide-y dark:divide-gray-700">
				{#each participants.filter((p) => !p.builtin) as p}<div class="py-3 text-sm">
						<span class="font-medium">{p.name}</span><span class="ml-3 text-gray-500"
							>{p.provider_name} · {p.model_name}</span
						>
					</div>{/each}
			</div>
		</section>{/if}
</div>
