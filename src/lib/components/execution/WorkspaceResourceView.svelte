<script lang="ts">
	import { onMount } from 'svelte';
	import { user, config, workspaceActions } from '$lib/stores';
	import {
		getNativeResources,
		getNativeResourceText,
		type Resource,
		type ResourceCatalog
	} from '$lib/apis/execution';
	import Skills from '$lib/components/workspace/Skills.svelte';
	import Prompts from '$lib/components/workspace/Prompts.svelte';
	import Tools from '$lib/components/workspace/Tools.svelte';
	export let kind: 'skills' | 'prompts' | 'mcp';
	let source = $user?.role === 'admin' ? 'native' : 'webui';
	let catalog: ResourceCatalog | null = null;
	let loading = true;
	let error = '';
	let search = '';
	let framework = '';
	let preview: { name: string; content: string } | null = null;
	const names: Record<string, string> = {
		'claude-code': 'Claude Code',
		codex: 'Codex',
		hermes: 'Hermes',
		dsh: 'DSH'
	};
	const headings = { skills: 'Skills', prompts: 'Prompts', mcp: 'MCP' };
	const components = { skills: Skills, prompts: Prompts, mcp: Tools };
	$: resources = catalog?.resources[kind] ?? [];
	$: filtered = resources.filter(
		(r) =>
			(!framework || r.frameworks?.includes(framework)) &&
			`${r.name} ${r.description ?? ''}`.toLowerCase().includes(search.toLowerCase())
	);
	$: if (source === 'native') workspaceActions.set([]);
	async function load() {
		loading = true;
		try {
			catalog = await getNativeResources();
			error = catalog.integration.compatible
				? ''
				: (catalog.integration.error ?? 'Native data source unavailable');
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	}
	async function show(resource: Resource) {
		try {
			preview = {
				name: resource.name,
				content: (await getNativeResourceText(resource.id)).content
			};
		} catch (e) {
			error = String(e);
		}
	}
	onMount(() => {
		if ($user?.role === 'admin') void load();
	});
</script>

<div class="flex flex-col flex-1 min-h-0 w-full">
	{#if $user?.role === 'admin'}
		<div class="flex flex-wrap items-center gap-3 px-5 py-3 border-b dark:border-gray-800 text-sm">
			<button
				type="button"
				class:font-semibold={source === 'native'}
				on:click={() => (source = 'native')}
				>Native · CC Switch{catalog ? ` ${resources.length}` : ''}</button
			>
			<button
				type="button"
				class:font-semibold={source === 'webui'}
				on:click={() => (source = 'webui')}>WebUI resources</button
			>
			<a href="/projects" class="ml-auto text-gray-500 underline">Use in a project</a>
		</div>
	{/if}
	{#if source === 'webui'}
		{#if kind !== 'mcp' || $config?.features?.enable_tools}<svelte:component
				this={components[kind]}
			/>
		{:else}<p class="p-5 text-sm text-gray-500">
				WebUI tools are disabled. Native MCP entries are available above.
			</p>{/if}
	{:else}
		<div class="overflow-y-auto flex-1 min-h-0 p-5 space-y-4">
			<div class="flex justify-between items-start gap-3">
				<div>
					<h1 class="text-xl font-semibold">{headings[kind]}</h1>
					<p class="text-sm text-gray-500 mt-1">
						Managed by CC Switch {catalog?.integration.version ?? ''}. Projects save their own
						resource bindings.
					</p>
				</div>
				<button type="button" class="text-sm underline shrink-0" on:click={load}>Refresh</button>
			</div>
			{#if kind === 'mcp'}<p class="text-sm text-gray-500">
					MCP entries can be browsed and bound to projects. Server execution is not enabled.
				</p>{/if}
			{#if error}<p role="alert" class="text-sm text-red-600">{error}</p>{/if}
			<div class="flex flex-wrap gap-3">
				<input
					aria-label="Search native resources"
					placeholder="Search names and descriptions"
					bind:value={search}
					class="border rounded-lg px-3 py-2 bg-transparent text-sm min-w-0 flex-1"
				/>
				<select
					aria-label="Resource framework"
					bind:value={framework}
					class="border rounded-lg px-3 py-2 text-sm bg-transparent"
					><option value="">All frameworks</option
					>{#each Object.entries(names) as [id, name]}<option value={id}>{name}</option
						>{/each}</select
				>
			</div>
			{#if preview}<section
					aria-label="Resource preview"
					class="border dark:border-gray-700 rounded-xl p-4"
				>
					<div class="flex justify-between gap-2">
						<h2 class="font-medium">{preview.name}</h2>
						<button type="button" on:click={() => (preview = null)}>Close preview</button>
					</div>
					<pre
						class="whitespace-pre-wrap break-words max-h-80 overflow-y-auto mt-3 text-xs">{preview.content}</pre>
				</section>{/if}
			{#if loading}<p class="text-sm text-gray-500">Loading native resources…</p>
			{:else}<p class="text-xs text-gray-500">
					{filtered.length} resources{filtered.length > 100
						? '; showing the first 100. Search to narrow results.'
						: ''}
				</p>
				<div class="grid lg:grid-cols-2 gap-3">
					{#each filtered.slice(0, 100) as resource}<article
							class="border dark:border-gray-800 rounded-xl p-4 min-w-0"
						>
							<h2 class="font-medium break-words">{resource.name}</h2>
							<p class="text-sm text-gray-500 mt-2 break-words">{resource.description ?? ''}</p>
							<p class="text-xs text-gray-500 mt-3">
								{resource.frameworks?.map((id) => names[id] ?? id).join(' · ')}
							</p>
							{#if kind !== 'mcp'}<button
									type="button"
									class="text-xs underline mt-3"
									disabled={!resource.available}
									on:click={() => show(resource)}>Preview</button
								>{/if}
						</article>{/each}
				</div>
				{#if !filtered.length && !error}<p class="text-sm text-gray-500">
						No resources match. Try another framework or clear the search.
					</p>{/if}
			{/if}
		</div>
	{/if}
</div>
