<script lang="ts">
	import { onMount } from 'svelte';
	import { hasPlanning } from './nodeProgram';
	import {
		getProjectWorkflowBindings,
		matchProjectWorkflows,
		saveProjectWorkflowBindings,
		type Workflow,
		type Participant,
		type CallableModule
	} from '$lib/apis/execution';
	export let projectId: string;
	import ModulePicker from './ModulePicker.svelte';
	let workflows: Workflow[] = [];
	let participants: Participant[] = [];
	let modules: CallableModule[] = [];
	let drafts: Record<string, Record<string, string>> = {};
	let error = '';
	let busy = '';
	let saved = '';
	let selectedId = '';
	let loading = true;
	onMount(async () => {
		try {
			const data = await getProjectWorkflowBindings(projectId);
			workflows = data.workflows;
			participants = data.participants;
			modules = data.modules ?? data.participants;
			drafts = Object.fromEntries(
				workflows.map((w) => [
					w.id!,
					Object.fromEntries((w.roles ?? []).map((r) => [r.id, w.bindings?.[r.id] ?? '']))
				])
			);
			selectedId = workflows.find((w) => w.available)?.id ?? workflows[0]?.id ?? '';
		} catch (e) {
			error = String(e);
		} finally {
			loading = false;
		}
	});
	async function match(workflowId?: string) {
		busy = workflowId ?? 'all';
		error = '';
		saved = '';
		try {
			const data = await matchProjectWorkflows(projectId, workflowId);
			workflows = data.workflows;
			participants = data.participants;
			modules = data.modules ?? data.participants;
			drafts = Object.fromEntries(workflows.map((w) => [w.id!, { ...w.bindings }]));
			saved = workflowId ?? 'all';
		} catch (e) {
			error = String(e);
		} finally {
			busy = '';
		}
	}
	async function save(workflow: Workflow) {
		if (!workflow.id) return;
		busy = workflow.id;
		error = '';
		saved = '';
		try {
			await saveProjectWorkflowBindings(projectId, workflow.id, drafts[workflow.id]);
			const data = await getProjectWorkflowBindings(projectId);
			workflows = data.workflows;
			modules = data.modules ?? data.participants;
			saved = workflow.id;
		} catch (e) {
			error = String(e);
		} finally {
			busy = '';
		}
	}
</script>

<section class="max-w-4xl space-y-6">
	<div>
		<div class="flex flex-wrap items-center justify-between gap-3">
			<h2 class="font-medium">Workflow defaults</h2>
			<button
				type="button"
				disabled={Boolean(busy) || !participants.length}
				on:click={() => match()}
				class="rounded-lg bg-gray-900 px-3 py-2 text-xs text-white dark:bg-white dark:text-gray-900 disabled:opacity-50"
				>{busy === 'all' ? 'Matching…' : 'Match all & save'}</button
			>
		</div>
		<p class="mt-2 text-sm leading-6 text-gray-500">
			Match uses this Project’s enabled participants, keeps valid existing bindings, and fills
			missing roles. Saved defaults are ready in new chats; individual chat changes stay local.
		</p>
		<a href="/workspace/workflows" class="mt-2 inline-block text-xs text-blue-600"
			>Manage workflow templates</a
		>
	</div>
	{#if loading}<p class="text-sm text-gray-500">
			Loading workflow bindings…
		</p>{:else if workflows.length}<label class="block text-sm"
			>Workflow template<select
				aria-label="Workflow template"
				bind:value={selectedId}
				class="mt-2 block w-full rounded-lg border bg-transparent px-3 py-2 text-sm dark:border-gray-700"
				>{#each workflows as workflow}<option value={workflow.id}>{workflow.name}</option
					>{/each}</select
			></label
		>{/if}
	{#each workflows.filter((w) => w.id === selectedId) as workflow}<form
			class="space-y-4 rounded-xl border p-5 dark:border-gray-700"
			on:submit|preventDefault={() => save(workflow)}
		>
			<div>
				<h3 class="text-sm font-medium">{workflow.name}</h3>
				<p class="mt-2 text-xs leading-5 text-gray-500">
					{workflow.description ?? 'Custom collaboration template'}
				</p>
				<p class="mt-1 text-xs text-gray-500">
					{hasPlanning(workflow.graph) ? 'AI-generated work' : 'Saved graph'} · {workflow.roles
						?.length ?? 0} module bindings · template v{workflow.version}
				</p>
			</div>
			<button
				type="button"
				disabled={Boolean(busy) || !participants.length}
				on:click={() => match(workflow.id)}
				class="rounded-lg border px-3 py-2 text-xs disabled:opacity-50"
				>Match & save this workflow</button
			>
			<details class="text-xs text-gray-500">
				<summary class="cursor-pointer">Shared instructions</summary>
				<p class="mt-2 whitespace-pre-wrap leading-5">{workflow.instructions}</p>
			</details>
			{#each workflow.roles ?? [] as role}<label class="block text-sm"
					><span class="font-medium">{role.name}</span><ModulePicker
						modules={modules.filter((m) => m.id !== 'workflow:' + workflow.id)}
						label={`${workflow.name} / ${role.name}`}
						bind:value={drafts[workflow.id!][role.id]}
						disabled={Boolean(busy)}
					/>{#if role.instructions}<span class="mt-1 block text-xs leading-5 text-gray-500"
							>{role.instructions}</span
						>{/if}</label
				>{/each}
			{#if hasPlanning(workflow.graph)}<div class="rounded-xl bg-gray-50 p-4 dark:bg-gray-850">
					<h4 class="text-sm font-medium">Executor pool</h4>
					<p class="mt-2 text-xs leading-5 text-gray-500">
						Controller selects from this Project’s enabled participants and creates execution
						instances at runtime. Manage this pool in the Participants tab; no fixed executor slots
						are required.
					</p>
					<div class="mt-3 flex flex-wrap gap-2">
						{#each participants as p}<span
								class="rounded-lg border px-2 py-1 text-xs dark:border-gray-700"
								>{p.name} · {p.provider_name ?? p.framework_id}</span
							>{/each}
					</div>
				</div>{/if}

			<div class="flex items-center justify-between gap-4">
				{#if saved === workflow.id || saved === 'all'}<span
						role="status"
						class="text-xs text-green-700">Bindings saved for this Project.</span
					>{:else}<span class="text-xs text-gray-500">Template rules stay in Configuration.</span
					>{/if}<button
					disabled={Boolean(busy)}
					class="rounded-lg bg-gray-900 px-3 py-2 text-xs text-white dark:bg-white dark:text-gray-900"
					>{busy === workflow.id ? 'Saving…' : 'Save bindings'}</button
				>
			</div>
		</form>{:else}{#if !loading}<p class="text-sm text-gray-500">
				Create a workflow template in Configuration first.
			</p>{/if}{/each}
	{#if error}<p role="alert" class="text-sm text-red-600">{error}</p>{/if}
</section>
