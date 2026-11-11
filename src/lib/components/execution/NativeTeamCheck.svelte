<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { request } from '$lib/apis/execution';
	import { refreshFolderChatLists } from '$lib/stores/chatList';
	export let projectId: string;
	type Check = {
		status: string;
		error?: string;
		chats: { chat_id: string; name: string; runs: { id: string; status: string }[] }[];
	};
	let check: Check | null = null,
		error = '',
		busy = false,
		disposed = false;
	let timer: ReturnType<typeof setTimeout>;
	const path = () => `/projects/${encodeURIComponent(projectId)}/native-accounting-check`;
	async function load() {
		clearTimeout(timer);
		try {
			check = await request<Check | null>(path());
		} catch (e) {
			error = String(e);
		}
		if (!disposed && check?.status === 'running') timer = setTimeout(load, 2500);
	}
	async function act(action: string) {
		busy = true;
		error = '';
		try {
			check = await request<Check>(path(), 'POST', { action });
			await refreshFolderChatLists(null);
			await load();
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
	onMount(() => {
		void load();
	});
	onDestroy(() => {
		disposed = true;
		clearTimeout(timer);
	});
</script>

<section
	aria-label="Native team accounting check"
	class="my-8 max-w-5xl rounded-xl border border-gray-200 p-5 dark:border-gray-700"
>
	<div class="flex items-start justify-between gap-4">
		<div>
			<h2 class="font-semibold">Native team accounting</h2>
			<p class="mt-2 text-sm leading-6 text-gray-500">
				DSH Standard, Claude Code and Hermes Flash each delegate to two native leaf agents, across
				two real turns. This checks the inner teams, not just outer module calls.
			</p>
		</div>
		<button
			disabled={busy}
			on:click={() =>
				act(
					check?.status === 'running'
						? 'stop'
						: check?.status === 'failed' && check.chats[0]?.runs.length === 1
							? 'continue'
							: 'start'
				)}
			class="shrink-0 rounded-lg border px-3 py-2 text-sm"
			>{busy
				? 'Working…'
				: check?.status === 'running'
					? 'Stop check'
					: check?.status === 'failed' && check.chats[0]?.runs.length === 1
						? 'Continue check'
						: 'Run native check'}</button
		>
	</div>
	<p class="mt-3 text-xs leading-5 text-gray-500">
		Uses the existing Compare template and your configured Flash providers. DSH Standard is enabled
		for this Project; existing Workflow defaults are preserved.
	</p>
	{#if error || check?.error}<p role="alert" class="mt-3 text-sm text-red-600">
			{error || check?.error}
		</p>{/if}
	{#if check}<p role="status" class="mt-3 text-sm">{check.status.replaceAll('_', ' ')}</p>
		{#each check.chats as chat}<a
				href={`/c/${chat.chat_id}`}
				class="mt-2 block text-sm text-blue-600">{chat.name} · {chat.runs.length} runs →</a
			>{/each}{/if}
</section>
