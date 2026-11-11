<script lang="ts">
	import { onMount } from 'svelte';
	import { models } from '$lib/stores';
	import {
		getExecutionCatalog,
		getExecutionSettings,
		saveExecutionSettings,
		type ExecutionSettings,
		type FrameworkId,
		type Framework,
		getApiProviders,
		refreshConnections,
		saveRuntime,
		type ApiProvider,
		type Runtime,
		type Provider
	} from '$lib/apis/execution';
	import ApiProviderEditor from './ApiProviderEditor.svelte';
	import RuntimeSettings from './RuntimeSettings.svelte';

	let apiProviders: ApiProvider[] = [];
	let runtimes: Runtime[] = [];
	let adapters: { id: FrameworkId; name: string }[] = [];
	let showAddRuntime = false;
	let newFamily: FrameworkId = 'codex';
	let newPath = '';
	let editingApi = false;
	let addingApi = false;
	let search = '';
	let frameworks: Framework[] = [];
	let providers: Provider[] = [];
	let settings: ExecutionSettings = { frameworks: {} };
	let selected: FrameworkId = 'claude-code';
	let selectedProvider = '';
	let loading = true;
	let busy = false;
	let error = '';
	let saved = '';
	$: options = settings.frameworks[selected] ?? {};
	$: frameworkProviders = providers.filter((provider) => provider.framework_id === selected);
	$: if (!frameworkProviders.some((provider) => provider.id === selectedProvider))
		selectedProvider =
			frameworkProviders.find((provider) => provider.current)?.id ??
			frameworkProviders[0]?.id ??
			'';
	$: apiConnection = apiProviders.find((p) => p.id === selectedProvider);
	$: provider = frameworkProviders.find((item) => item.id === selectedProvider);
	$: ids = [
		...new Set([
			...(provider?.models?.length
				? provider.models
				: $models
						.filter((model) => model.execution?.provider_id === selectedProvider)
						.map((model) => model.execution?.model_id ?? model.id))
		])
	];
	async function load() {
		error = '';
		try {
			const [response, catalog, apis] = await Promise.all([
				getExecutionSettings(),
				getExecutionCatalog(),
				getApiProviders()
			]);
			settings = response.settings;
			frameworks = response.frameworks;
			runtimes = response.runtimes ?? [];
			adapters = response.adapters ?? [];
			apiProviders = apis;
			frameworks = catalog.frameworks;
			providers = catalog.providers.map((p) => {
				const api = apis.find((a) => a.id === p.id);
				return api
					? {
							...p,
							name: api.name,
							endpoint_host: api.endpoint_host,
							source: api.kind === 'ollama' ? 'Ollama API' : 'OpenAI-compatible API'
						}
					: p;
			});
			for (const api of apis)
				if (!providers.some((p) => p.id === api.id))
					providers.push({
						id: api.id,
						name: api.name,
						framework_id: api.framework_id,
						source: api.kind === 'ollama' ? 'Ollama API' : 'OpenAI-compatible API',
						models: api.model_ids,
						model_ids: [],
						model_mappings: {},
						available: true,
						endpoint_host: api.endpoint_host
					});
			for (const api of apis)
				if (!frameworks.some((f) => f.id === api.framework_id))
					frameworks.push({
						id: api.framework_id,
						name: api.framework_id === 'hermes' ? 'Hermes' : 'Direct',
						description: 'Call a configured model API'
					});
			if (!frameworks.some((f) => f.id === selected)) selected = frameworks[0]?.id ?? 'direct';
			models.set(catalog.models);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}
	async function scan() {
		busy = true;
		saved = '';
		error = '';
		try {
			await refreshConnections();
			await load();
			saved = 'Scan complete. Review the discovered runtimes and model catalogs below.';
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
	async function addRuntime() {
		busy = true;
		error = '';
		try {
			await saveRuntime(newFamily, newPath);
			selected = newFamily;
			await load();
			showAddRuntime = false;
			newPath = '';
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
	function setOption(
		key: 'enabled' | 'disabled_models' | 'disabled_providers',
		value: boolean | string[]
	) {
		settings = {
			...settings,
			frameworks: { ...settings.frameworks, [selected]: { ...options, [key]: value } }
		};
		saved = '';
	}
	function toggleList(key: 'disabled_models' | 'disabled_providers', id: string, enabled: boolean) {
		const values = new Set(options[key] ?? []);
		if (enabled) values.delete(id);
		else values.add(id);
		setOption(key, [...values]);
	}
	async function save() {
		busy = true;
		error = '';
		saved = '';
		try {
			settings = (await saveExecutionSettings(settings)).settings;
			models.set((await getExecutionCatalog()).models);
			saved = 'Saved. Available connections are now up to date.';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}
	onMount(() => {
		void load();
	});
</script>

<div class="max-w-5xl mx-auto px-4 py-6 md:px-8">
	<div class="flex flex-wrap gap-3 justify-between items-start mb-7">
		<div>
			<h1 class="text-xl font-semibold">Connections</h1>
			<p class="mt-2 text-sm text-gray-500">
				Frameworks → Providers → Models. Discover local runtimes and connect remote APIs.
			</p>
		</div>
		<div class="flex gap-2">
			<button
				type="button"
				disabled={busy}
				on:click={scan}
				class="rounded-lg border px-3 py-2 text-xs disabled:opacity-50"
				>{busy ? 'Working…' : 'Scan & refresh'}</button
			><button
				type="button"
				on:click={() => (showAddRuntime = !showAddRuntime)}
				class="rounded-lg border px-3 py-2 text-xs">Add framework</button
			>
		</div>
	</div>

	{#if showAddRuntime}<form
			on:submit|preventDefault={addRuntime}
			class="mb-6 space-y-3 rounded-xl border p-4 dark:border-gray-700"
		>
			<p class="text-sm font-medium">Register a framework runtime</p>
			<p class="text-xs leading-5 text-gray-500">
				Select a protocol adapter and an installed executable. Installed runtimes appear
				automatically after a scan; other protocols need an adapter before they can run.
			</p>
			<div class="flex flex-wrap gap-2">
				<select
					aria-label="Framework adapter"
					bind:value={newFamily}
					class="rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
					>{#each adapters as a}<option value={a.id}>{a.name}</option>{/each}</select
				><input
					aria-label="New framework path"
					required
					bind:value={newPath}
					placeholder="/absolute/path/to/executable"
					class="min-w-0 flex-1 rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
				/><button
					disabled={busy}
					class="rounded-lg bg-gray-900 px-3 py-2 text-xs text-white dark:bg-white dark:text-gray-900"
					>Register</button
				>
			</div>
		</form>{/if}

	{#if error}<p role="alert" class="mb-4 text-sm text-red-600">{error}</p>{/if}
	{#if loading}<p class="text-sm text-gray-500">Loading connections…</p>
	{:else}
		<div class="grid md:grid-cols-[10rem_minmax(0,1fr)] gap-6">
			<nav aria-label="Framework configuration" class="flex md:flex-col gap-1 overflow-x-auto">
				{#each frameworks as framework}<button
						type="button"
						on:click={() => {
							selected = framework.id;
							editingApi = false;
							search = '';
						}}
						aria-pressed={selected === framework.id}
						class="text-left shrink-0 rounded-lg px-3 py-2.5 text-sm {selected === framework.id
							? 'bg-gray-100 dark:bg-gray-800 font-medium'
							: 'text-gray-500 hover:bg-gray-50 dark:hover:bg-gray-850'}">{framework.name}</button
					>{/each}
			</nav>
			<div class="min-w-0 space-y-6">
				<section aria-label="Framework connection">
					<div class="flex justify-between gap-4 items-center">
						<h2 class="font-medium">{frameworks.find((f) => f.id === selected)?.name}</h2>
						<label class="text-sm flex gap-2 items-center"
							><input
								type="checkbox"
								checked={options.enabled ?? true}
								on:change={(event) => setOption('enabled', event.currentTarget.checked)}
							/>Enabled</label
						>
					</div>
					<p class="text-sm text-gray-500 mt-2">
						{frameworks.find((f) => f.id === selected)?.description}
					</p>
				</section>
				{#if selected !== 'direct'}{#key selected}<RuntimeSettings
							family={selected}
							runtime={runtimes.find((r) => r.id === selected)}
							onSaved={load}
						/>{/key}{:else}<p class="text-xs leading-5 text-gray-500">
						Remote providers and local model servers share this catalog. CC Switch entries are
						imported from native configuration; API entries can be edited here.
					</p>{/if}
				<section aria-label="Framework providers">
					<div class="flex justify-between items-center mb-3">
						<h2 class="font-medium">Providers</h2>
						{#if selected === 'direct' || selected === 'hermes'}<button
								type="button"
								on:click={() => {
									addingApi = true;
									editingApi = true;
								}}
								class="text-xs text-blue-600">Add API provider</button
							>{/if}
					</div>
					<div class="space-y-2">
						{#each frameworkProviders as item}<div
								class="rounded-xl border {selectedProvider === item.id
									? 'border-gray-400 dark:border-gray-500'
									: 'border-gray-200 dark:border-gray-800'} flex items-center justify-between gap-3 p-3"
							>
								<button
									type="button"
									class="text-left min-w-0 flex-1"
									aria-pressed={selectedProvider === item.id}
									on:click={() => {
										selectedProvider = item.id;
										editingApi = false;
										search = '';
									}}
									><span class="text-sm font-medium">{item.name}</span>{#if item.current}<span
											class="ml-2 text-xs text-gray-500">Current</span
										>{/if}<span class="block text-xs text-gray-500 mt-1 break-all"
										>{item.source}{item.endpoint_host ? ` · ${item.endpoint_host}` : ''} · {item
											.models.length || item.model_ids.length} models{apiProviders.find(
											(a) => a.id === item.id && (!a.enabled || !a.service_enabled)
										)
											? ' · API disconnected'
											: ''}</span
									></button
								>
								<label class="text-xs flex items-center gap-2 shrink-0"
									><input
										type="checkbox"
										aria-label={`Enable provider ${item.name}`}
										disabled={!item.available}
										checked={item.available &&
											!(options.disabled_providers ?? []).includes(item.id)}
										on:change={(event) =>
											toggleList('disabled_providers', item.id, event.currentTarget.checked)}
									/>Available to projects</label
								>
							</div>{:else}<p class="text-sm text-gray-500">
								No providers found. Configure an API connection or the native framework, then
								refresh.
							</p>{/each}
					</div>
				</section>
				{#if editingApi}{#key `${selectedProvider}:${addingApi}`}<ApiProviderEditor
							framework={selected === 'hermes' ? 'hermes' : 'direct'}
							connection={addingApi ? null : (apiConnection ?? null)}
							onSaved={load}
							onClose={() => (editingApi = false)}
						/>{/key}{/if}
				{#if provider}
					<section aria-label="Provider model mappings">
						<div class="mb-3 flex items-center justify-between gap-3">
							<h2 class="text-sm font-medium">{provider.name} · Models</h2>
							{#if apiConnection}<button
									type="button"
									on:click={() => {
										addingApi = false;
										editingApi = !editingApi;
									}}
									class="text-xs text-blue-600">Edit connection</button
								>{/if}
						</div>
						<input
							aria-label="Search provider models"
							bind:value={search}
							placeholder="Search models"
							class="mb-3 w-full rounded-lg border bg-transparent px-3 py-2 text-sm dark:border-gray-700"
						/>
						{#if selected === 'codex'}<p class="mb-3 text-xs leading-5 text-gray-500">
								Complete native catalog, including hidden models. Effort options come from each
								model's native declaration.
							</p>{/if}
						{#if provider.catalog_warning}<p class="mb-3 text-xs leading-5 text-amber-700">
								{provider.catalog_warning}
							</p>{/if}
						{#if !provider.available}<p class="text-sm text-amber-700 mb-3">
								{provider.unavailable_reason}
							</p>{/if}
						{#if Object.keys(provider.model_mappings).length}<dl
								class="rounded-xl bg-gray-50 dark:bg-gray-850 px-4 divide-y divide-gray-200/60 dark:divide-gray-700/50 text-sm mb-3"
							>
								{#each Object.entries(provider.model_mappings) as [role, model]}<div
										class="flex flex-wrap justify-between gap-2 py-3"
									>
										<dt class="text-gray-500">
											{{ default: 'Default', opus: 'Opus', sonnet: 'Sonnet', haiku: 'Haiku' }[
												role
											] ?? role}
										</dt>
										<dd class="break-all">{model}</dd>
									</div>{/each}
							</dl>{/if}
						<div
							class="rounded-xl bg-gray-50 dark:bg-gray-850 divide-y divide-gray-200/60 dark:divide-gray-700/50"
						>
							{#each ids.filter((id) => id
									.toLowerCase()
									.includes(search.toLowerCase())) as id}<label
									class="px-4 py-3 flex items-center justify-between gap-3 text-sm"
									><span class="break-all"
										>{provider.model_details?.[id]?.display_name ??
											id}{#if provider.model_details?.[id]?.display_name && provider.model_details[id].display_name !== id}<span
												class="ml-2 text-xs text-gray-500">{id}</span
											>{/if}{#if provider.model_details?.[id]?.hidden}<span
												class="ml-2 text-xs text-gray-500">Hidden in native picker</span
											>{/if}</span
									><input
										type="checkbox"
										aria-label={`Show model ${id}`}
										checked={!(options.disabled_models ?? []).some(
											(key) => key === id || key === `${selectedProvider}::${id}`
										)}
										on:change={(event) =>
											toggleList(
												'disabled_models',
												`${selectedProvider}::${id}`,
												event.currentTarget.checked
											)}
									/></label
								>{/each}
						</div>
						{#if provider.unlisted_models?.length}<p class="mt-3 text-xs leading-5 text-amber-700">
								Configured but not advertised by this CLI account: {provider.unlisted_models.join(
									', '
								)}. These entries are retained here for diagnosis; native model availability comes
								from the CLI catalog.
							</p>{/if}
						{#if selected === 'claude-code'}<p class="text-xs text-gray-500 mt-3">
								Provider settings come from CC Switch. Replies use the selected provider without
								switching global configuration.{provider.common_config
									? 'Includes the enabled shared configuration.'
									: ''}
							</p>{:else if selected === 'hermes'}<p class="text-xs text-gray-500 mt-3">
								Hermes uses its gateway model and working environment.
							</p>{/if}
					</section>
				{/if}
				<div class="flex flex-wrap items-center justify-end gap-3">
					<span role="status" class="text-xs text-gray-500">{saved}</span><button
						type="button"
						disabled={busy}
						on:click={save}
						class="rounded-lg bg-gray-900 text-white dark:bg-white dark:text-gray-900 px-5 py-2 text-sm disabled:opacity-50"
						>{busy ? 'Saving…' : 'Save changes'}</button
					>
				</div>
			</div>
		</div>
	{/if}
</div>
