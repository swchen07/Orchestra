<script lang="ts">
	import { saveApiProvider, type ApiProvider } from '$lib/apis/execution';
	export let connection: ApiProvider | null = null;
	export let onSaved: () => Promise<void>;
	export let onClose: () => void;
	export let framework: 'direct' | 'hermes' =
		connection?.framework_id === 'hermes' ? 'hermes' : 'direct';
	let kind: 'openai' | 'ollama' = connection?.kind ?? 'openai';
	let name = connection?.name ?? '';
	let url = connection?.url ?? '';
	let key = '';
	let clearKey = false;
	let enabled = connection?.enabled ?? true;
	let modelIds = connection?.model_ids.join('\n') ?? '';
	let prefix = connection?.prefix_id ?? '';
	let nativeModel = connection?.native_model ?? '';
	let nativeProvider = connection?.native_provider ?? '';
	let busy = false;
	let error = '';
	async function save() {
		busy = true;
		error = '';
		try {
			await saveApiProvider({
				kind,
				index: connection?.index,
				name,
				url,
				key,
				clear_key: clearKey,
				enabled,
				model_ids: modelIds
					.split(/[\n,]/)
					.map((s) => s.trim())
					.filter(Boolean),
				prefix_id: prefix,
				framework_id: kind === 'openai' ? framework : 'direct',
				native_model: framework === 'hermes' ? nativeModel : '',
				native_provider: framework === 'hermes' ? nativeProvider : ''
			});
			key = '';
			await onSaved();
			onClose();
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
</script>

<form
	on:submit|preventDefault={save}
	class="space-y-4 rounded-xl border border-gray-200 p-4 dark:border-gray-700"
>
	<div class="flex items-center justify-between">
		<h3 class="text-sm font-medium">{connection ? 'Edit provider' : 'Add API provider'}</h3>
		<button type="button" on:click={onClose} class="text-xs text-gray-500">Cancel</button>
	</div>
	<div class="grid gap-4 sm:grid-cols-2">
		<label class="text-xs text-gray-500"
			>API type<select
				aria-label="API type"
				bind:value={kind}
				disabled={Boolean(connection)}
				class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
				><option value="openai">OpenAI-compatible</option><option value="ollama">Ollama</option
				></select
			></label
		>
		<label class="text-xs text-gray-500"
			>Name<input
				aria-label="Provider name"
				required
				maxlength="100"
				bind:value={name}
				class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
			/></label
		>
	</div>
	{#if kind === 'openai'}
		<label class="block text-xs text-gray-500"
			>Framework<select
				aria-label="Connection framework"
				bind:value={framework}
				class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
				><option value="direct">Direct</option><option value="hermes">Hermes gateway</option
				></select
			></label
		>
		{#if framework === 'hermes'}
			<div class="grid gap-4 sm:grid-cols-2">
				<label class="text-xs text-gray-500"
					>Profile model<input
						aria-label="Hermes profile model"
						bind:value={nativeModel}
						maxlength="256"
						placeholder="deepseek-v4-flash"
						class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
					/></label
				>
				<label class="text-xs text-gray-500"
					>Profile provider<input
						aria-label="Hermes profile provider"
						bind:value={nativeProvider}
						maxlength="256"
						placeholder="deepseek"
						class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
					/></label
				>
			</div>
			<p class="text-xs text-gray-500">
				These identify the model configured in this gateway's native profile.
			</p>
		{/if}
	{/if}
	<label class="block text-xs text-gray-500"
		>Base URL<input
			aria-label="Provider base URL"
			type="url"
			required
			bind:value={url}
			placeholder={kind === 'openai' ? 'https://api.example.com/v1' : 'http://localhost:11434'}
			class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
		/></label
	>
	<label class="block text-xs text-gray-500"
		>API key<input
			aria-label="Provider API key"
			type="password"
			autocomplete="new-password"
			bind:value={key}
			placeholder={connection?.has_key
				? 'Saved · leave blank to keep'
				: 'Optional for local services'}
			class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
		/></label
	>
	{#if connection?.has_key}<label class="flex items-center gap-2 text-xs text-gray-500"
			><input type="checkbox" bind:checked={clearKey} />Clear saved key</label
		>{/if}
	<details class="text-xs text-gray-500">
		<summary class="cursor-pointer">Model discovery options</summary>
		<div class="mt-3 space-y-3">
			<label class="block"
				>Model IDs<textarea
					aria-label="Provider model IDs"
					bind:value={modelIds}
					rows="3"
					placeholder="Leave empty to discover from the API"
					class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
				></textarea></label
			>
			<label class="block"
				>Model prefix<input
					aria-label="Provider model prefix"
					bind:value={prefix}
					maxlength="100"
					class="mt-1 block w-full rounded-lg border bg-transparent p-2 text-sm dark:border-gray-700"
				/></label
			>
			<p class="leading-5">
				Use a prefix when providers share model IDs. Existing access rules and advanced connection
				options are preserved.
			</p>
		</div>
	</details>
	<div class="flex items-center justify-between gap-3">
		<label class="flex items-center gap-2 text-xs"
			><input type="checkbox" bind:checked={enabled} />Connect to this API</label
		><button
			disabled={busy}
			class="rounded-lg bg-gray-900 px-3 py-2 text-xs text-white dark:bg-white dark:text-gray-900 disabled:opacity-50"
			>{busy ? 'Saving…' : 'Save provider'}</button
		>
	</div>
	{#if error}<p role="alert" class="text-xs text-red-600">{error}</p>{/if}
</form>
