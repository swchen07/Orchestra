<script lang="ts">
	import { saveRuntime, type FrameworkId, type Runtime } from '$lib/apis/execution';
	export let family: FrameworkId;
	export let runtime: Runtime | undefined;
	export let onSaved: () => Promise<void>;
	let path = '';
	let busy = false;
	let error = '';
	async function save(value: string) {
		busy = true;
		error = '';
		try {
			await saveRuntime(family, value);
			await onSaved();
			path = '';
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
</script>

<details class="rounded-xl bg-gray-50 p-4 text-xs dark:bg-gray-850">
	<summary class="cursor-pointer font-medium"
		>Runtime · {runtime?.version || 'Local runtime'} · {runtime?.selection === 'manual'
			? 'Registered'
			: 'Auto'}</summary
	>
	<p class="mt-3 break-all leading-5 text-gray-500">{runtime?.path ?? 'No executable found'}</p>
	{#if runtime?.unavailable}<p class="mt-2 text-amber-700">
			The registered executable is unavailable. Choose a new path.
		</p>{/if}
	{#if family === 'hermes'}<p class="mt-2 leading-5 text-gray-500">
			Hermes replies use its configured gateway. This path identifies the local CLI; changing it
			does not restart the gateway.
		</p>{:else}<p class="mt-2 leading-5 text-gray-500">
			Model discovery and replies use this same executable.
		</p>{/if}
	<div class="mt-3 space-y-2">
		{#each runtime?.candidates ?? [] as candidate}<button
				type="button"
				disabled={busy}
				on:click={() => save(candidate.path)}
				class="block w-full rounded-lg border p-2 text-left disabled:opacity-50 dark:border-gray-700"
				><span class="font-medium"
					>{candidate.version || 'Installed executable'}{candidate.path === runtime?.path
						? ' · Selected'
						: ''}</span
				><span class="mt-1 block break-all text-gray-500">{candidate.path}</span></button
			>{/each}
	</div>
	<form on:submit|preventDefault={() => save(path)} class="mt-3 flex flex-wrap gap-2">
		<input
			aria-label="Framework executable path"
			bind:value={path}
			required
			placeholder="/absolute/path/to/executable"
			class="min-w-0 flex-1 rounded-lg border bg-transparent p-2 dark:border-gray-700"
		/><button disabled={busy} class="rounded-lg border px-3 py-2 disabled:opacity-50"
			>Use path</button
		><button
			type="button"
			disabled={busy}
			on:click={() => save('')}
			class="rounded-lg px-2 py-2 text-gray-500">Use auto-discovery</button
		>
	</form>
	{#if error}<p role="alert" class="mt-2 text-red-600">{error}</p>{/if}
</details>
