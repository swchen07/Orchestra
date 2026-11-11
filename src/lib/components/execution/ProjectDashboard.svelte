<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { showSidebar, selectedFolder, models } from '$lib/stores';
	import { refreshFolderChatLists } from '$lib/stores/chatList';
	import { frameworkNames } from '$lib/stores/execution';
	import {
		getProjectDashboard,
		getExecutionCatalog,
		saveProjectBindings,
		getResourcePreview,
		getNativeSession,
		moveProjectSession,
		createProjectTask,
		createProjectProfile,
		type ProjectDashboard,
		type ProjectBindings,
		type Resource,
		type Session
	} from '$lib/apis/execution';
	import TopologyEditor from './TopologyEditor.svelte';
	import ProjectParticipants from './ProjectParticipants.svelte';
	import ProjectWorkflows from './ProjectWorkflows.svelte';
	import AdaptiveExamples from './AdaptiveExamples.svelte';
	import NativeTeamCheck from './NativeTeamCheck.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	export let projectId: string;
	let data: ProjectDashboard | null = null;
	let tab = 'board';
	let error = '';
	let notice = '';
	let loading = true;
	let refreshing = false;
	let bindings: ProjectBindings = {};
	let bindingsDirty = false;
	let search = '';
	let resourceKey: 'skill_ids' | 'prompt_ids' | 'mcp_ids' = 'skill_ids';
	let preview: { title: string; content: string } | null = null;
	let sessionPreview: Awaited<ReturnType<typeof getNativeSession>> | null = null;
	let taskName = '';
	let profileName = '';
	let profileFramework = 'claude-code';
	let profilePrompt = '';
	let timer: ReturnType<typeof setInterval>;
	let disposed = false;
	const tabs = [
		['participants', 'Participants'],
		['workflows', 'Workflows'],
		['examples', 'Examples'],
		['board', 'Board'],
		['sessions', 'Chats'],
		['skills', 'Skills'],
		['prompts', 'Prompts'],
		['mcp', 'MCP'],
		['profiles', 'Agent / Profiles'],
		['usage', 'Usage'],
		['topology', 'Legacy DAG']
	];
	const stages = [
		['backlog', 'Backlog'],
		['running', 'Running'],
		['review', 'Review'],
		['done', 'Done']
	];
	const frameworkLabels: Record<string, string> = { ...frameworkNames, codex: 'Codex', dsh: 'DSH' };
	async function refresh(catalog = false) {
		if (refreshing) return;
		refreshing = true;
		try {
			const result = await getProjectDashboard(projectId);
			if (disposed) return;
			data = result;
			if (!bindingsDirty) bindings = structuredClone(result.bindings);
			if (catalog) models.set((await getExecutionCatalog()).models);
			error = '';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			refreshing = false;
			loading = false;
		}
	}
	async function newChat() {
		if (!data) return;
		selectedFolder.set(data.project as unknown as Parameters<typeof selectedFolder.set>[0]);
		await goto(`/folders/${projectId}`);
	}
	async function createTask() {
		try {
			await createProjectTask(projectId, taskName);
			taskName = '';
			notice = 'Task chat created.';
			await refreshFolderChatLists(null);
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
	async function move(session: Session, stage: string) {
		try {
			await moveProjectSession(projectId, session.id, stage);
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
	function toggleResource(
		key: 'skill_ids' | 'prompt_ids' | 'mcp_ids',
		id: string,
		selected: boolean
	) {
		const values = new Set(bindings[key] ?? []);
		if (selected) values.add(id);
		else values.delete(id);
		bindings = { ...bindings, [key]: [...values] };
		bindingsDirty = true;
		notice = '';
	}
	async function saveBindings() {
		try {
			bindings = await saveProjectBindings(projectId, bindings);
			bindingsDirty = false;
			notice = 'Resource bindings saved for future replies.';
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
	async function showResource(resource: Resource) {
		try {
			preview = {
				title: resource.name,
				content: (await getResourcePreview(projectId, resource.id)).content
			};
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
	async function showSession(session: Session) {
		try {
			sessionPreview = await getNativeSession(projectId, session.id);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
	async function addProfile() {
		try {
			await createProjectProfile(projectId, {
				name: profileName,
				framework_id: profileFramework,
				prompt: profilePrompt
			});
			profileName = '';
			profilePrompt = '';
			notice = 'Preset saved for chats and workflow nodes.';
			await refresh();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
	function number(value: number) {
		return value.toLocaleString();
	}
	function usd(value: string | null) {
		return value !== null && Number.isFinite(Number(value))
			? '$' + Number(value).toFixed(6)
			: 'Not reported';
	}
	function date(value: number) {
		return new Date(value * 1000).toLocaleString();
	}
	$: resourceGroup =
		data && ['skills', 'prompts', 'mcp'].includes(tab)
			? data.resources[tab as 'skills' | 'prompts' | 'mcp']
			: [];
	$: resourceKey = tab === 'skills' ? 'skill_ids' : tab === 'prompts' ? 'prompt_ids' : 'mcp_ids';
	$: resourceList = resourceGroup.filter((resource) =>
		`${resource.name} ${resource.description ?? ''}`.toLowerCase().includes(search.toLowerCase())
	);
	onMount(() => {
		void refresh(true);
		timer = setInterval(() => {
			if (data?.runs.some((run) => ['queued', 'running'].includes(run.status))) void refresh();
		}, 3000);
	});
	onDestroy(() => {
		disposed = true;
		clearInterval(timer);
	});
</script>

<svelte:head><title>{data?.project.name ?? 'Project'} · Open WebUI</title></svelte:head>
<div
	class="flex flex-col flex-1 min-w-0 w-full h-screen max-h-[100dvh] {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: 'md:max-w-[calc(100%-42px)]'} overflow-y-auto"
>
	<div class="px-4 pt-3 md:hidden">
		<button type="button" aria-label="Open sidebar" on:click={() => showSidebar.set(!$showSidebar)}
			><SidebarIcon className="size-5" /></button
		>
	</div>
	<main class="max-w-7xl mx-auto w-full px-5 py-7 md:px-8 space-y-6">
		<div class="flex flex-wrap justify-between items-start gap-4">
			<div>
				<a href="/projects" class="text-xs text-gray-500">← Projects</a>
				<h1 class="text-2xl font-semibold mt-2">{data?.project.name ?? 'Loading project…'}</h1>
				{#if data?.project.data.description}<p class="text-sm text-gray-500 mt-2">
						{data.project.data.description}
					</p>{/if}
			</div>
			<div class="flex gap-3 items-center">
				<button
					type="button"
					disabled={refreshing}
					class="text-sm text-gray-500"
					on:click={() => refresh(true)}>Refresh</button
				><a href={`/projects?edit=${projectId}`} class="text-sm text-gray-500">Project settings</a
				><button
					type="button"
					on:click={newChat}
					class="rounded-lg bg-gray-900 text-white dark:bg-white dark:text-gray-900 px-4 py-2 text-sm"
					>New Chat</button
				>
			</div>
		</div>
		{#if error}<p role="alert" class="text-sm text-red-600 dark:text-red-300">{error}</p>{/if}
		{#if notice}<p role="status" class="text-sm text-gray-600 dark:text-gray-300">{notice}</p>{/if}
		{#if data}
			<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
				<span
					>CC Switch {data.integration.version ?? ''} · schema {data.integration.schema_version ??
						'—'}</span
				><span
					>{data.integration.compatible
						? 'Pinned integration connected'
						: data.integration.error}</span
				><span>Project bindings saved separately</span>
			</div>
			<nav
				aria-label="Project views"
				class="flex gap-5 overflow-x-auto border-b border-gray-200 dark:border-gray-800 pb-3 text-sm"
			>
				{#each tabs as [key, label]}<button
						type="button"
						aria-current={tab === key ? 'page' : null}
						class="shrink-0 {tab === key ? 'font-semibold' : 'text-gray-500'}"
						on:click={() => {
							tab = key;
							search = '';
						}}>{label}</button
					>{/each}
			</nav>
			{#if tab === 'board'}
				<div class="flex flex-wrap justify-between gap-3 items-center">
					<p class="text-sm text-gray-500">
						Each card is a project chat. Columns track work stages; execution status appears
						separately.
					</p>
					<form class="flex gap-2" on:submit|preventDefault={createTask}>
						<input
							aria-label="Task name"
							required
							bind:value={taskName}
							placeholder="Task name"
							class="rounded-lg border bg-transparent px-3 py-2 text-sm min-w-0"
						/><button type="submit" class="rounded-lg border px-3 py-2 text-sm shrink-0"
							>Add task</button
						>
					</form>
				</div>
				<div class="grid grid-flow-col auto-cols-[minmax(12rem,1fr)] gap-4 overflow-x-auto pb-2">
					{#each stages as [stage, label]}<section
							class="rounded-xl bg-gray-50 dark:bg-gray-850 p-3 min-w-0"
						>
							<h2 class="text-sm font-medium mb-3">
								{label}
								<span class="text-gray-400"
									>{data.sessions.filter((session) => session.stage === stage).length}</span
								>
							</h2>
							<div class="space-y-2">
								{#each data.sessions.filter((session) => session.stage === stage) as session}<article
										class="rounded-lg bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-800 p-3"
									>
										<a
											href={`/c/${session.id}`}
											class="text-sm font-medium break-words hover:underline">{session.title}</a
										>
										<p class="text-xs text-gray-500 mt-2">
											{session.active
												? 'Model running'
												: session.workflow_status
													? `Workflow ${session.workflow_status.replaceAll('_', ' ')}`
													: session.error
														? 'Execution error'
														: session.response_done
															? 'Reply complete'
															: 'No reply yet'}
										</p>
										<select
											aria-label={`Move task ${session.title}`}
											value={session.stage}
											on:change={(event) => move(session, event.currentTarget.value)}
											class="mt-3 rounded-md border bg-transparent p-1 text-xs max-w-full"
											>{#each stages as [value, text]}<option {value}>{text}</option>{/each}</select
										>
									</article>{:else}<p class="text-xs text-gray-400 py-4 text-center">
										No tasks
									</p>{/each}
							</div>
						</section>{/each}
				</div>
			{:else if tab === 'participants'}
				<ProjectParticipants {projectId} />
			{:else if tab === 'workflows'}
				<ProjectWorkflows {projectId} />
			{:else if tab === 'examples'}
				<NativeTeamCheck {projectId} />
				<AdaptiveExamples {projectId} />
			{:else if tab === 'sessions'}
				<section class="space-y-3">
					<h2 class="font-medium">Project chats</h2>
					{#each data.sessions as session}<a
							class="flex flex-wrap gap-3 justify-between rounded-lg border p-3 text-sm hover:bg-gray-50 dark:hover:bg-gray-850"
							href={`/c/${session.id}`}
							><span>{session.title}</span><span class="text-xs text-gray-500"
								>{date(session.updated_at)}</span
							></a
						>{:else}<p class="text-sm text-gray-500">No chats yet.</p>{/each}
				</section>
				<section class="space-y-3">
					<h2 class="font-medium">Native history</h2>
					<p class="text-xs text-gray-500">
						Native transcripts matched to the project directory. Browse Chats to find other
						conversations.
					</p>
					<a href="/chats" class="block text-sm underline">Browse all conversations</a>
					{#each data.native_sessions as session}<button
							type="button"
							class="w-full flex flex-wrap gap-3 justify-between text-left rounded-lg border p-3 text-sm"
							on:click={() => showSession(session)}
							><span class="break-words min-w-0">{session.title.slice(0, 100)}</span><span
								class="text-xs text-gray-500"
								>{frameworkLabels[session.framework_id ?? ''] ?? session.framework_id} · {date(
									session.updated_at
								)}</span
							></button
						>{:else}<p class="text-sm text-gray-500">
							No native conversations match this directory.
						</p>{/each}
				</section>
			{:else if ['skills', 'prompts', 'mcp'].includes(tab)}
				<div class="flex flex-wrap gap-3 justify-between items-center">
					<div>
						<h2 class="font-medium">
							{tab === 'skills' ? 'Skills' : tab === 'prompts' ? 'Prompts' : 'MCP resources'}
						</h2>
						<p class="text-xs text-gray-500 mt-1">
							Browse CC Switch resources and save project bindings.
						</p>
					</div>
					<button
						type="button"
						disabled={!bindingsDirty}
						on:click={saveBindings}
						class="rounded-lg bg-gray-900 text-white dark:bg-white dark:text-gray-900 px-4 py-2 text-sm disabled:opacity-40"
						>Save bindings</button
					>
				</div>
				{#if tab === 'mcp'}<p
						class="rounded-lg bg-gray-50 dark:bg-gray-850 p-3 text-sm text-gray-500"
					>
						MCP catalog bindings are available. Server execution is not enabled.
					</p>{/if}
				<input
					aria-label="Search project resources"
					bind:value={search}
					placeholder="Search resource names and descriptions"
					class="rounded-lg border bg-transparent px-3 py-2 text-sm w-full"
				/>
				<div class="space-y-2">
					{#each resourceList.slice(0, 80) as resource}<article
							class="rounded-xl border border-gray-200 dark:border-gray-800 p-4 flex items-start gap-3"
						>
							<input
								type="checkbox"
								class="mt-1"
								aria-label={`Bind resource ${resource.name}`}
								disabled={!resource.available}
								checked={(bindings[resourceKey] ?? []).includes(resource.id)}
								on:change={(event) =>
									toggleResource(resourceKey, resource.id, event.currentTarget.checked)}
							/>
							<div class="min-w-0 flex-1">
								<h3 class="text-sm font-medium">{resource.name}</h3>
								<p class="mt-1 text-xs text-gray-500 break-words">{resource.description}</p>
								<p class="mt-2 text-xs text-gray-400">
									{resource.frameworks
										?.map((framework) => frameworkLabels[framework] ?? framework)
										.join(' / ') || 'Framework not declared'}{!resource.available
										? ' · Source unavailable'
										: ''}
								</p>
							</div>
							{#if tab !== 'mcp'}<button
									type="button"
									class="text-xs text-gray-500 shrink-0"
									on:click={() => showResource(resource)}>Preview</button
								>{/if}
						</article>{:else}<p class="text-sm text-gray-500">No matching resources.</p>{/each}
				</div>
				<p class="text-xs text-gray-500">
					{resourceList.length} resources; showing up to 80. Search to narrow results.
				</p>
				{#if tab === 'prompts'}<div class="border-t pt-4">
						<h3 class="text-sm font-medium">CC Switch connection presets</h3>
						<p class="text-xs text-gray-500 mt-2">
							Connection presets are separate from agent profiles. {data.resources
								.connection_presets.length} found.
						</p>
						{#each data.resources.connection_presets as preset}<p class="text-sm mt-2">
								{preset.name}
							</p>{/each}
					</div>{/if}
			{:else if tab === 'profiles'}
				<p class="text-sm text-gray-500">
					Presets define behavior separately from provider connections. Their source and supported
					scope appear below.
				</p>
				<div class="flex flex-wrap gap-3 items-center">
					<label class="text-sm"
						>Default preset <select
							aria-label="Project default preset"
							bind:value={bindings.profile_id}
							on:change={() => {
								bindingsDirty = true;
								notice = '';
							}}
							class="rounded-lg border bg-transparent p-2 ml-2"
							><option value={null}>None</option
							>{#each data.profiles.filter((profile) => profile.available) as profile}<option
									value={profile.id}
									>{frameworkLabels[profile.framework_id]} / {profile.name}</option
								>{/each}</select
						></label
					><button
						type="button"
						disabled={!bindingsDirty}
						on:click={saveBindings}
						class="text-sm rounded-lg border px-3 py-2 disabled:opacity-40"
						>Save default preset</button
					>
				</div>
				<div class="grid md:grid-cols-2 gap-3">
					{#each data.profiles as profile}<article class="rounded-xl border p-4">
							<h3 class="text-sm font-medium">
								{profile.name} · {frameworkLabels[profile.framework_id] ?? profile.framework_id}
							</h3>
							<p class="text-xs text-gray-500 mt-2">{profile.source} · {profile.kind}</p>
							<p class="text-xs text-gray-500 mt-2">
								{profile.available
									? 'Preset available within the current tool boundary'
									: 'Definition only; execution unavailable'}{profile.note
									? ` · ${profile.note}`
									: ''}
							</p>
						</article>{/each}
				</div>
				<form on:submit|preventDefault={addProfile} class="rounded-xl border p-4 space-y-3">
					<h3 class="text-sm font-medium">New project role</h3>
					<div class="flex flex-wrap gap-3">
						<input
							aria-label="Preset name"
							required
							bind:value={profileName}
							placeholder="Fact checker"
							class="min-w-0 flex-1 rounded-lg border bg-transparent p-2 text-sm"
						/><select
							aria-label="Preset framework"
							bind:value={profileFramework}
							class="rounded-lg border bg-transparent p-2 text-sm"
							><option value="claude-code">Claude Code</option><option value="hermes">Hermes</option
							></select
						>
					</div>
					<textarea
						aria-label="Preset instructions"
						required
						bind:value={profilePrompt}
						rows="3"
						placeholder="Describe how this role should work"
						class="w-full rounded-lg border bg-transparent p-2 text-sm"
					></textarea><button type="submit" class="rounded-lg border px-3 py-2 text-sm"
						>Save preset</button
					>
				</form>
			{:else if tab === 'usage'}
				<div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
					{#each [['Input tokens', number(data.usage.summary.input_tokens)], ['Output tokens', number(data.usage.summary.output_tokens)], ['Cache read / write', number(data.usage.summary.cache_read_tokens) + ' / ' + number(data.usage.summary.cache_write_tokens)], ['Reported / estimated cost', usd(data.usage.summary.known_cost_usd)]] as [label, value]}<div
							class="rounded-xl bg-gray-50 dark:bg-gray-850 p-4"
						>
							<p class="text-xs text-gray-500">{label}</p>
							<p class="text-lg font-semibold mt-2 break-all">{value}</p>
						</div>{/each}
				</div>
				<p class="text-xs text-gray-500">
					{data.usage.summary.request_count} metered replies / requests; {data.usage.summary
						.unknown_cost_records} have no reported cost. {data.usage.coverage}
					{data.usage.summary.unmetered_records ?? 0} calls have no reported token usage.
				</p>
				<div class="overflow-x-auto">
					<table class="w-full text-sm text-left">
						<thead class="text-xs text-gray-500"
							><tr
								><th class="p-2">Source / model</th><th class="p-2">Input</th><th class="p-2"
									>Output</th
								><th class="p-2">Cache read / write</th><th class="p-2">USD</th></tr
							></thead
						><tbody
							>{#each data.usage.records as record}<tr class="border-t"
									><td class="p-2 break-all"
										><span>{record.model}</span><span class="block text-xs text-gray-500"
											>{record.source}</span
										></td
									><td class="p-2"
										>{record.usage_available === false
											? 'Unknown'
											: number(record.input_tokens)}</td
									><td class="p-2"
										>{record.usage_available === false
											? 'Unknown'
											: number(record.output_tokens)}</td
									><td class="p-2"
										>{number(record.cache_read_tokens)} / {number(record.cache_write_tokens)}</td
									><td class="p-2 whitespace-nowrap">{usd(record.cost_usd)}</td></tr
								>{/each}</tbody
						>
					</table>
				</div>
			{:else if tab === 'topology'}<TopologyEditor {data} onRefresh={() => refresh()} />{/if}
		{:else if loading}<p class="text-sm text-gray-500">
				Loading project and integration data…
			</p>{/if}
	</main>
</div>
{#if preview || sessionPreview}<div
		class="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-4"
	>
		<div
			role="dialog"
			aria-modal="true"
			aria-label="Content preview"
			class="bg-white dark:bg-gray-900 rounded-2xl w-full max-w-3xl max-h-[85vh] overflow-y-auto p-5"
		>
			<div class="flex justify-between items-start gap-3 mb-4">
				<h2 class="font-medium">{preview?.title ?? sessionPreview?.session.title}</h2>
				<button
					type="button"
					aria-label="Close preview"
					class="text-sm text-gray-500 shrink-0"
					on:click={() => {
						preview = null;
						sessionPreview = null;
					}}>Close</button
				>
			</div>
			{#if preview}<pre
					class="whitespace-pre-wrap break-words text-xs">{preview.content}</pre>{:else if sessionPreview}<p
					class="text-xs text-gray-500 mb-4"
				>
					Read-only native history{sessionPreview.truncated
						? ' · File tail, up to 200 text messages'
						: ''}
				</p>
				{#each sessionPreview.messages as message}<article class="mb-4">
						<h3 class="text-xs font-semibold text-gray-500 mb-1">{message.role}</h3>
						<pre class="whitespace-pre-wrap break-words text-sm">{message.content}</pre>
					</article>{/each}{/if}
		</div>
	</div>{/if}
