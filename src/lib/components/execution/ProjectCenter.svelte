<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { models, user, showSidebar } from '$lib/stores';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import { refreshFolderChatLists } from '$lib/stores/chatList';
	import { frameworkNames } from '$lib/stores/execution';
	import {
		getExecutionCatalog,
		getProjects,
		createProject,
		updateProject,
		type Project,
		type ProjectForm,
		type Provider
	} from '$lib/apis/execution';
	let projects: Project[] = [];
	let providers: Provider[] = [];
	let editing: string | null = null;
	let form: ProjectForm | null = null;
	let busy = false;
	let loading = true;
	let error = '';
	let createRequest: string | null = null;
	let commandSource = '[]';
	$: if (
		!loading &&
		$page.url.searchParams.has('new') &&
		$page.url.searchParams.get('new') !== createRequest
	) {
		createRequest = $page.url.searchParams.get('new');
		edit();
	}
	const empty = (): ProjectForm => ({
		name: '',
		description: '',
		system_prompt: '',
		directory: '',
		provider_ids: [],
		default_model_id: ''
	});
	$: availableProviders = providers.filter(
		(provider) => provider.available && provider.model_ids.length
	);
	$: projectModels = form
		? $models.filter((model) => form?.provider_ids.includes(model.execution?.provider_id ?? ''))
		: [];
	$: if (form && !projectModels.some((model) => model.id === form?.default_model_id)) {
		form.default_model_id =
			projectModels.find((model) => model.execution?.default && model.execution?.current_provider)
				?.id ??
			projectModels.find((model) => model.execution?.default)?.id ??
			projectModels[0]?.id ??
			'';
	}
	async function load() {
		try {
			const [list, catalog] = await Promise.all([getProjects(), getExecutionCatalog()]);
			projects = list;
			providers = catalog.providers;
			models.set(catalog.models);
			const editId = $page.url.searchParams.get('edit');
			if (editId) {
				const project = projects.find((project) => project.id === editId);
				if (project) edit(project);
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}
	function edit(project?: Project) {
		commandSource = JSON.stringify(
			project?.data.execution_project.workspace?.commands ?? [],
			null,
			2
		);
		error = '';
		editing = project?.id ?? null;
		form = project
			? {
					name: project.name,
					description: project.data.description ?? '',
					system_prompt: project.data.system_prompt ?? '',
					directory: project.data.execution_project.directory ?? '',
					provider_ids: [...project.data.execution_project.provider_ids],
					default_model_id: project.data.execution_project.default_model_id,
					...(project.data.execution_project.workspace
						? { workspace: structuredClone(project.data.execution_project.workspace) }
						: {})
				}
			: empty();
	}
	function toggleProvider(id: string, checked: boolean) {
		if (!form) return;
		form = {
			...form,
			provider_ids: checked
				? [...form.provider_ids, id]
				: form.provider_ids.filter((value) => value !== id)
		};
	}
	async function enter(project: Project) {
		await goto(`/projects/${project.id}`);
	}
	async function save(openChat: boolean) {
		if (!form) return;
		busy = true;
		error = '';
		try {
			if (form.workspace?.enabled) form.workspace.commands = JSON.parse(commandSource);
			const project = editing ? await updateProject(editing, form) : await createProject(form);
			await refreshFolderChatLists(null);
			form = null;
			editing = null;
			if (openChat) await enter(project);
			else await load();
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

<svelte:head><title>Projects · Open WebUI</title></svelte:head>
<div
	class="flex flex-col flex-1 min-w-0 w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: 'md:max-w-[calc(100%-42px)]'} overflow-y-auto"
>
	<div class="px-4 pt-3 {$showSidebar ? 'md:hidden' : ''}">
		<button
			type="button"
			aria-label={$showSidebar ? 'Close sidebar' : 'Open sidebar'}
			on:click={() => showSidebar.set(!$showSidebar)}
			class="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800"
			><SidebarIcon className="size-5" /></button
		>
	</div>
	<main class="max-w-5xl mx-auto px-5 py-7 md:px-10 md:py-10">
		<div class="flex flex-wrap justify-between items-start gap-4 mb-7">
			<div>
				<h1 class="text-2xl font-semibold">Projects</h1>
				<p class="mt-2 text-sm text-gray-500">
					Keep conversations, instructions, participants and a working directory together.
				</p>
			</div>
			<button
				type="button"
				on:click={() => edit()}
				class="rounded-lg px-4 py-2 text-sm bg-gray-900 text-white dark:bg-white dark:text-gray-900"
				>New project</button
			>
		</div>
		{#if error}<p role="alert" class="mb-4 text-sm text-red-600 dark:text-red-300">{error}</p>{/if}
		{#if form}
			<form
				on:submit|preventDefault={() => save(true)}
				aria-label={editing ? 'Edit project' : 'New project'}
				class="border border-gray-200 dark:border-gray-800 rounded-2xl p-5 mb-8 space-y-5"
			>
				<div class="flex justify-between items-center">
					<h2 class="font-medium">{editing ? 'Edit project' : 'New project'}</h2>
					<button type="button" on:click={() => (form = null)} class="text-sm text-gray-500"
						>Cancel</button
					>
				</div>
				<label class="block text-sm"
					>Project name<input
						aria-label="Project name"
						required
						maxlength="120"
						bind:value={form.name}
						placeholder="Research, writing or product development"
						class="block mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2.5"
					/></label
				>
				<label class="block text-sm"
					>Description<textarea
						aria-label="Description"
						bind:value={form.description}
						rows="2"
						placeholder="What would you like to accomplish?"
						class="block mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2.5"
					></textarea></label
				>
				<fieldset>
					<legend class="text-sm font-medium">Available providers</legend>
					<p class="text-xs text-gray-500 mt-1 mb-3">
						Choose the providers that participants in this project can use.
					</p>
					<div class="space-y-4">
						{#each Object.entries(frameworkNames) as [framework, name]}
							{@const items = availableProviders.filter(
								(provider) => provider.framework_id === framework
							)}
							{#if items.length}<div>
									<h3 class="text-xs text-gray-500 mb-2">{name}</h3>
									<div class="flex flex-wrap gap-2">
										{#each items as provider}<label
												class="inline-flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 py-2 px-3 text-sm"
												><input
													type="checkbox"
													aria-label={`Add ${name} / ${provider.name}`}
													checked={form.provider_ids.includes(provider.id)}
													on:change={(event) =>
														toggleProvider(provider.id, event.currentTarget.checked)}
												/>{provider.name}</label
											>{/each}
									</div>
								</div>{/if}
						{/each}
					</div>
					{#if !availableProviders.length}<p class="text-sm text-gray-500">
							No providers are available. Configure a connection first.
						</p>{/if}
					{#if $user?.role === 'admin'}<a
							href="/workspace/connections"
							class="inline-block text-xs text-gray-500 mt-3 hover:underline">Manage connections</a
						>{/if}
				</fieldset>
				<label class="block text-sm"
					>Default participant<select
						aria-label="Default participant"
						required
						bind:value={form.default_model_id}
						class="block mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2.5"
						><option value="" disabled>Choose a provider first</option
						>{#each projectModels as model}<option value={model.id}
								>{frameworkNames[model.execution?.framework_id ?? 'direct']} / {model.execution
									?.provider_name} / {model.name}</option
							>{/each}</select
					></label
				>
				{#if $user?.role === 'admin'}<label class="block text-sm"
						>Working directory <span class="text-gray-500">(optional)</span><input
							aria-label="Project directory"
							bind:value={form.directory}
							placeholder="Absolute path to an existing project; leave empty for chat only"
							class="block mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2.5"
						/><span class="block mt-2 text-xs text-gray-500"
							>Read-only by default. Enable code workspaces below to create private writable copies
							from a pinned clean Git baseline.</span
						></label
					>{/if}
				{#if $user?.role === 'admin'}
					<fieldset class="rounded-xl border border-gray-200 dark:border-gray-700 p-4 space-y-3">
						<legend class="px-1 text-sm font-medium">Code workspaces</legend>
						<label class="flex items-center gap-2 text-sm"
							><input
								type="checkbox"
								aria-label="Enable coding workspaces"
								checked={form.workspace?.enabled ?? false}
								on:change={(e) => {
									if (form)
										form.workspace = {
											...(form.workspace ?? {
												backend: 'local-worktree',
												baseline_revision: '',
												command_backend: 'docker',
												docker_image: 'python:3.14-slim'
											}),
											enabled: e.currentTarget.checked
										};
								}}
							/>Give each module its own writable Git workspace</label
						>
						{#if form.workspace?.enabled}
							<p class="text-xs text-gray-500">
								Native agents run as trusted local processes in separate Git trees. This does not
								provide OS filesystem or network isolation. Unsupported sandbox requirements are
								rejected.
							</p>
							<label class="block text-sm"
								>Pinned baseline commit<input
									aria-label="Workspace baseline commit"
									bind:value={form.workspace.baseline_revision}
									required
									placeholder="Full commit from the clean Project repository"
									class="mt-1 block w-full rounded-lg border bg-transparent p-2"
								/></label
							>
							<label class="block text-sm"
								>Host evaluation environment<select
									aria-label="Host command backend"
									bind:value={form.workspace.command_backend}
									class="mt-1 block w-full rounded-lg border bg-transparent p-2"
									><option value="docker"
										>Docker · only evaluation scratch mounted, no network</option
									><option value="local-worktree">Trusted local process</option></select
								></label
							>
							{#if form.workspace.command_backend === 'docker'}<label class="block text-sm"
									>Approved evaluation image<input
										aria-label="Evaluation Docker image"
										bind:value={form.workspace.docker_image}
										class="mt-1 block w-full rounded-lg border bg-transparent p-2"
									/></label
								>{/if}
							<label class="block text-sm"
								>Hermes native profile directory<input
									aria-label="Hermes workspace profile"
									bind:value={form.workspace.hermes_profile}
									placeholder="Explicit trusted profile matching the selected provider and model"
									class="mt-1 block w-full rounded-lg border bg-transparent p-2"
								/></label
							>
							<details>
								<summary class="cursor-pointer text-sm"
									>Authoritative checks and named commands</summary
								>
								<p class="my-2 text-xs text-gray-500">
									Administrator-defined commands. Each has an id, argument list, timeout_seconds and
									authoritative_paths restored from the pinned baseline before evaluation.
								</p>
								<textarea
									aria-label="Workspace named commands"
									bind:value={commandSource}
									rows="8"
									spellcheck="false"
									class="w-full rounded-lg border bg-transparent p-2 font-mono text-xs"
								></textarea>
							</details>
						{/if}
					</fieldset>
				{/if}
				<label class="block text-sm"
					>Project instructions <span class="text-gray-500">(optional)</span><textarea
						aria-label="Project instructions"
						bind:value={form.system_prompt}
						rows="3"
						placeholder="Instructions shared by conversations in this project"
						class="block mt-2 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-transparent p-2.5"
					></textarea></label
				>
				<div class="flex justify-end gap-3">
					<button
						type="button"
						disabled={busy || !form.default_model_id}
						on:click={() => save(false)}
						class="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2 text-sm disabled:opacity-40"
						>Save project</button
					><button
						type="submit"
						disabled={busy || !form.default_model_id}
						class="rounded-lg bg-gray-900 text-white dark:bg-white dark:text-gray-900 px-4 py-2 text-sm disabled:opacity-40"
						>{busy ? 'Saving…' : 'Save & open project'}</button
					>
				</div>
			</form>
		{/if}
		{#if loading}<p class="text-sm text-gray-500">
				Loading projects…
			</p>{:else if !projects.length && !form}<div
				class="rounded-2xl bg-gray-50 dark:bg-gray-850 p-8"
			>
				<h2 class="font-medium">Start a project</h2>
				<p class="mt-2 text-sm text-gray-500">
					Create a project, choose providers, then start a conversation.
				</p>
			</div>{:else}
			<div class="grid md:grid-cols-2 gap-4">
				{#each projects as project}<article
						class="rounded-2xl border border-gray-200 dark:border-gray-800 p-5 min-w-0"
					>
						<div class="flex justify-between items-start gap-3">
							<h2 class="font-medium break-words">{project.name}</h2>
							<button
								type="button"
								aria-label={`Edit project ${project.name}`}
								on:click={() => edit(project)}
								class="text-xs text-gray-500 shrink-0">Edit</button
							>
						</div>
						{#if project.data.description}<p class="text-sm text-gray-500 mt-2">
								{project.data.description}
							</p>{/if}
						<div class="flex flex-wrap gap-1.5 mt-4">
							{#each project.data.execution_project.provider_ids as id}{@const provider =
									providers.find((item) => item.id === id)}<span
									class="rounded-md bg-gray-100 dark:bg-gray-800 px-2 py-1 text-xs"
									>{provider
										? `${frameworkNames[provider.framework_id]} / ${provider.name}`
										: 'Provider removed'}</span
								>{/each}
						</div>
						{#if project.data.execution_project.directory}<p
								class="mt-3 text-xs text-gray-500 break-all"
							>
								{project.data.execution_project.directory}
							</p>{/if}
						<button
							type="button"
							aria-label={`Open project ${project.name}`}
							on:click={() => enter(project)}
							class="mt-5 text-sm font-medium hover:underline">Open project →</button
						>
					</article>{/each}
			</div>
		{/if}
	</main>
</div>
