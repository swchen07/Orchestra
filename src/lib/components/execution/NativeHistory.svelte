<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { user, showSidebar } from '$lib/stores';
	import { refreshChatList, refreshFolderChatLists } from '$lib/stores/chatList';
	import { createRequestGate } from '$lib/utils/requestGate';
	import {
		listConversations,
		readNativeSession,
		importNativeSession,
		getExecutionCatalog,
		getProjects,
		type NativePreview,
		type NativeSession,
		type Project,
		type Catalog
	} from '$lib/apis/execution';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	const requests = createRequestGate();
	const previews = createRequestGate();
	let result: Awaited<ReturnType<typeof listConversations>> | null = null;
	let preview: NativePreview | null = null;
	let projects: Project[] = [];
	let catalog: Catalog | null = null;
	let framework = '';
	let query = '';
	let offset = 0;
	let automation = false;
	let loading = false;
	let opening = false;
	let importing = false;
	let error = '';
	let projectId = '';
	let modelId = '';
	const names: Record<string, string> = {
		webui: 'WebUI',
		dsh: 'DSH',
		hermes: 'Hermes',
		'claude-code': 'Claude Code',
		codex: 'Codex'
	};
	$: project = projects.find((p) => p.id === projectId);
	$: choices = (catalog?.models ?? []).filter(
		(m) =>
			m.execution?.framework_id === preview?.session.framework_id &&
			(!project ||
				project.data.execution_project.provider_ids.includes(m.execution?.provider_id ?? ''))
	);
	$: if (!choices.some((m) => m.id === modelId))
		modelId =
			choices.find((m) => m.execution?.model_id === preview?.session.model)?.id ??
			choices[0]?.id ??
			'';
	async function load(reset = false) {
		if (reset) offset = 0;
		const current = requests.begin();
		loading = true;
		try {
			const response = await listConversations(framework, query, offset, automation);
			if (!current()) return;
			result = response;
			error = '';
		} catch (e) {
			if (current()) error = String(e);
		} finally {
			if (current()) loading = false;
		}
	}
	async function open(session: NativeSession) {
		if (session.webui_chat_id) {
			await goto(`/c/${session.webui_chat_id}`);
			return;
		}
		const current = previews.begin();
		opening = true;
		preview = null;
		try {
			const data = await readNativeSession(session.id);
			if (current()) {
				preview = data;
				error = '';
			}
		} catch (e) {
			if (current()) error = String(e);
		} finally {
			if (current()) opening = false;
		}
	}
	async function importChat() {
		if (!preview || !modelId || importing) return;
		importing = true;
		try {
			const imported = await importNativeSession(preview.session.id, projectId, modelId);
			await Promise.all([refreshChatList(localStorage.token), refreshFolderChatLists(null)]);
			await goto(`/c/${imported.id}`);
		} catch (e) {
			error = String(e);
		} finally {
			importing = false;
		}
	}
	onMount(async () => {
		if ($user?.role !== 'admin') return;
		void load();
		try {
			[catalog, projects] = await Promise.all([getExecutionCatalog(), getProjects()]);
		} catch (e) {
			error = String(e);
		}
	});
	onDestroy(() => {
		requests.invalidate();
		previews.invalidate();
	});
</script>

<svelte:head><title>Chats · Open WebUI</title></svelte:head>
<div
	class="flex-1 min-w-0 w-full h-screen overflow-y-auto {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: 'md:max-w-[calc(100%-42px)]'}"
>
	<div class="px-5 pt-3 md:hidden">
		<button type="button" aria-label="Open sidebar" on:click={() => showSidebar.set(!$showSidebar)}
			><Sidebar className="size-5" /></button
		>
	</div>
	<div class="p-5 md:p-8 max-w-7xl mx-auto space-y-5">
		<div
			class="sticky top-0 z-20 -mx-2 -mt-2 bg-white/95 p-2 pb-4 backdrop-blur dark:bg-gray-900/95 space-y-4"
			aria-label="Conversation filters"
		>
			<div class="flex flex-wrap justify-between items-center gap-3">
				<div>
					<h1 class="text-2xl font-semibold">Chats</h1>
					<p class="text-sm text-gray-500 mt-2">
						WebUI chats and native conversations, together in one place.
					</p>
				</div>
				<a class="rounded-lg border px-3 py-2 text-sm" href="/">New Chat</a>
			</div>
			{#if $user?.role === 'admin'}
				<div class="flex flex-wrap gap-2" aria-label="Conversation source">
					<button
						type="button"
						class="rounded-lg border px-3 py-2 text-sm"
						class:font-semibold={!framework}
						on:click={() => {
							framework = '';
							void load(true);
						}}>All</button
					>{#each Object.entries(names) as [id, name]}<button
							type="button"
							class="rounded-lg border px-3 py-2 text-sm"
							class:font-semibold={framework === id}
							on:click={() => {
								framework = id;
								void load(true);
							}}>{name} {result?.counts[id] ?? '…'}</button
						>{/each}
				</div>
				<form on:submit|preventDefault={() => load(true)} class="flex flex-wrap items-center gap-3">
					<input
						aria-label="Search conversations"
						placeholder="Search titles or directories"
						bind:value={query}
						class="border rounded-lg px-3 py-2 min-w-[12rem] flex-1 text-sm bg-transparent"
					/><button type="submit" class="border rounded-lg px-4 py-2 text-sm">Search</button><label
						class="flex items-center gap-2 text-xs text-gray-500"
						><input type="checkbox" bind:checked={automation} on:change={() => load(true)} />Include
						Hermes automation</label
					>
				</form>
				<p class="text-xs text-gray-500">
					Child sessions and automation are hidden by default. Native sessions can be imported into
					a project.
				</p>
			{/if}
		</div>
		{#if $user?.role !== 'admin'}<p>
				Native conversation browsing requires an administrator.
			</p>{:else}
			{#if error}<p role="alert" class="text-sm text-red-600">{error}</p>{/if}
			{#each result?.warnings ?? [] as warning}<p class="text-sm text-amber-700">
					{warning}
				</p>{/each}
			<div
				class="grid gap-5 {preview || opening
					? 'xl:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]'
					: ''}"
			>
				<section
					aria-label="Conversation list"
					class="space-y-3 min-w-0 {preview || opening ? 'hidden xl:block' : ''}"
				>
					{#if loading}<p class="text-sm text-gray-500">Loading conversations…</p>{:else}<p
							class="text-xs text-gray-500"
						>
							{result?.total ?? 0} conversations
						</p>{/if}
					{#each result?.items ?? [] as session}<button
							type="button"
							class="block w-full text-left border dark:border-gray-800 rounded-lg px-3 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-900"
							on:click={() => open(session)}
							><span class="block text-xs text-gray-500"
								>{names[session.framework_id]} · {new Date(
									session.updated_at * 1000
								).toLocaleString()}</span
							><span
								class="block text-[13px] leading-5 font-medium mt-1 break-words line-clamp-2"
								title={session.title}>{session.title}</span
							><span class="block text-[11px] leading-4 text-gray-500 mt-1 break-all"
								>{session.directory ?? ''}</span
							></button
						>{/each}
					<div class="flex justify-between text-sm">
						<button
							type="button"
							disabled={!offset || loading}
							on:click={() => {
								offset = Math.max(0, offset - 40);
								void load();
							}}>Previous</button
						><button
							type="button"
							disabled={loading || offset + 40 >= (result?.total ?? 0)}
							on:click={() => {
								offset += 40;
								void load();
							}}>Next</button
						>
					</div>
				</section>
				{#if opening}<p>Loading preview…</p>{:else if preview}<section
						aria-label="Native conversation preview"
						class="min-w-0 space-y-4 border dark:border-gray-800 rounded-xl p-4 self-start xl:sticky xl:top-4 xl:max-h-[calc(100dvh-2rem)] xl:overflow-y-auto"
					>
						<div class="flex justify-between items-start gap-3">
							<h2 class="font-semibold break-words line-clamp-3" title={preview.session.title}>
								{preview.session.title}
							</h2>
							<button
								type="button"
								class="text-xs shrink-0"
								on:click={() => {
									preview = null;
									previews.invalidate();
								}}>Close</button
							>
						</div>
						<p class="text-xs text-gray-500">
							Native history is read-only. Import creates a WebUI text copy; new replies use your
							selected model.
						</p>
						{#if preview.truncated}<p class="text-sm text-amber-700">
								This preview is truncated. Import includes the visible text only (up to 200
								messages), without native tool state.
							</p>{/if}
						<div
							class="border rounded-lg dark:border-gray-800 p-3 max-h-[50vh] overflow-y-auto space-y-4"
						>
							{#each preview.messages as message}<article>
									<h3 class="text-xs text-gray-500">
										{message.role === 'user' ? 'User' : 'Assistant'}
									</h3>
									<p class="whitespace-pre-wrap break-words text-sm mt-1">{message.content}</p>
								</article>{/each}{#if !preview.messages.length}<p>
									No user or assistant text is available.
								</p>{/if}
						</div>
						<label class="block text-sm"
							>Project<select
								aria-label="Import project"
								bind:value={projectId}
								class="block w-full mt-2 border rounded-lg px-3 py-2 bg-transparent"
								><option value="">No project</option>{#each projects as item}<option value={item.id}
										>{item.name}</option
									>{/each}</select
							></label
						>
						<label class="block text-sm"
							>Reply with<select
								aria-label="Reply model"
								bind:value={modelId}
								class="block w-full mt-2 border rounded-lg px-3 py-2 bg-transparent"
								>{#each choices as model}<option value={model.id}
										>{names[model.execution?.framework_id ?? '']} / {model.execution?.provider_name} /
										{model.execution?.model_id}</option
									>{/each}</select
							></label
						>
						{#if !choices.length}<p class="text-sm text-gray-500">
								No compatible model is available. Choose another project or configure this framework
								first.
							</p>{/if}
						<button
							type="button"
							disabled={!modelId || importing || !preview.messages.length}
							class="rounded-lg bg-gray-900 text-white dark:bg-white dark:text-gray-900 px-4 py-2 text-sm disabled:opacity-40"
							on:click={importChat}
							>{importing
								? 'Importing…'
								: preview.truncated
									? 'Import visible text'
									: 'Import & open chat'}</button
						>
					</section>{/if}
			</div>
		{/if}
	</div>
</div>
