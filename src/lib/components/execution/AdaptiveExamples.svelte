<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { request } from '$lib/apis/execution';
	import { refreshFolderChatLists } from '$lib/stores/chatList';
	export let projectId: string;
	type Suite = {
		id: string;
		status: string;
		chats: {
			chat_id: string;
			name: string;
			status: string;
			runs: { id: string; status: string; review?: { status: string } }[];
		}[];
	};
	let suite: Suite | null = null;
	let participants: Record<string, { model_name: string; provider_name: string }> = {};
	let error = '';
	let busy = false;
	let timer: ReturnType<typeof setTimeout>;
	let disposed = false;
	const path = () => `/projects/${encodeURIComponent(projectId)}/adaptive-examples`;
	async function load() {
		try {
			const value = await request<{ suite: Suite | null; participants: typeof participants }>(
				path()
			);
			if (disposed) return;
			suite = value.suite;
			participants = value.participants;
			error = '';
		} catch (e) {
			error = String(e);
		}
		if (!disposed && suite?.status === 'running') timer = setTimeout(load, 2500);
	}
	async function start(action = 'start') {
		busy = true;
		error = '';
		try {
			suite = await request<Suite>(path(), 'POST', { action });
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

<section class="space-y-5 max-w-5xl" aria-label="Adaptive module examples">
	<div class="flex items-start justify-between gap-4">
		<div>
			<h2 class="font-semibold">Adaptive examples</h2>
			<p class="mt-2 text-sm leading-6 text-gray-500">
				Three tasks × three frameworks, plus one mixed team. The Controller generates each graph; an
				independent Reviewer checks the result.
			</p>
		</div>
		<div class="flex shrink-0 gap-3">
			{#if !suite}<button
					disabled={busy || Boolean(error)}
					on:click={() => start()}
					class="shrink-0 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white dark:bg-white dark:text-gray-900"
					>{busy ? 'Creating…' : 'Start 10 chats'}</button
				>
			{:else}
				<button on:click={load} class="text-sm text-gray-500">Refresh</button>
				{#if suite.status === 'running'}<button
						disabled={busy}
						on:click={() => start('stop')}
						class="text-sm text-gray-500">Stop suite</button
					>
				{:else if suite.status !== 'completed'}<button
						disabled={busy}
						on:click={() => start('retry')}
						class="rounded-lg border px-3 py-2 text-sm">Continue failed chats</button
					>{/if}
			{/if}
		</div>
	</div>
	{#if error}<p role="alert" class="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950">
			{error}
		</p>{/if}
	<div class="grid gap-3 sm:grid-cols-3">
		{#each [['dsh', 'DSH'], ['claude-code', 'Claude Code'], ['hermes', 'Hermes']] as [id, name]}
			<div class="rounded-xl border border-gray-200 p-3 text-sm dark:border-gray-700">
				<strong class="font-medium">{name}</strong>
				<p class="mt-1 text-xs text-gray-500">
					{participants[id]?.model_name ?? 'Checking Flash model…'}
				</p>
				<p class="mt-1 text-xs text-gray-500">{participants[id]?.provider_name ?? ''}</p>
			</div>
		{/each}
	</div>
	<p class="text-xs leading-5 text-gray-500">
		Parallel ledger · Dependent quote · Incremental dispatch. Each chat has two real turns with
		retained module contexts. Chats run one at a time so other tests do not distort branch timing.
		These calls use your configured providers.
	</p>
	{#if suite}
		<p role="status" class="text-sm">
			{suite.status.replaceAll('_', ' ')} · {suite.chats.filter((c) => c.status === 'completed')
				.length} / {suite.chats.length} chats completed
		</p>
		<div
			class="divide-y rounded-xl border border-gray-200 dark:border-gray-700 dark:divide-gray-700"
		>
			{#each suite.chats as chat}<a
					href={`/c/${chat.chat_id}`}
					class="flex flex-wrap items-center justify-between gap-2 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-900"
					><span class="text-sm">{chat.name}</span><span class="text-xs text-gray-500"
						>{chat.status.replaceAll('_', ' ')} · {chat.runs.length} runs{#if chat.runs.at(-1)?.review?.status}
							· Review {chat.runs.at(-1)?.review?.status.replaceAll('_', ' ')}{/if}</span
					></a
				>{/each}
		</div>
	{/if}
</section>
