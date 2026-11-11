<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getWorkflows,
		saveWorkflow,
		getWorkflowStarters,
		getDeletedWorkflows,
		deleteWorkflow,
		restoreWorkflow,
		type Workflow
	} from '$lib/apis/execution';
	import GraphEditor from './GraphEditor.svelte';
	import GraphCanvas from './GraphCanvas.svelte';
	import WorkflowGuide from './WorkflowGuide.svelte';
	import WorkflowSection from './WorkflowSection.svelte';
	import { nextExecutorRole } from './moduleNames';
	import {
		hasPlanning,
		outputNode,
		boundaryNode,
		workflowInputRef,
		synchronizeGraph
	} from './nodeProgram';

	let workflows: Workflow[] = [];
	let form: Workflow | null = null;
	let busy = false,
		loading = true,
		advanced = false;
	let error = '',
		saved = '';
	let deleted: Workflow[] = [],
		starters: Workflow[] = [];
	let notice = '';
	let editStructure = false;
	let selectedTask = '';
	let creation: { name: string; source: Workflow; copy: boolean } | null = null;
	const defaultLimits = {
		max_calls: 24,
		max_seconds: 600,
		max_concurrency: 2,
		max_replans: 5,
		max_tokens: 1000000
	};
	type TopologyKind = 'generated' | 'predefined';
	let topology: TopologyKind = 'predefined';
	type TopologyDraft = Pick<Workflow, 'roles' | 'lead_role_id' | 'graph'>;
	let topologyDrafts: Partial<Record<TopologyKind, TopologyDraft>> = {};
	$: topology = hasPlanning(form?.graph) ? 'generated' : 'predefined';

	function choose(row: Workflow) {
		form = structuredClone(row);
		form.execution_policy ??= 'automatic';
		if (form.graph) form.graph.limits = { ...defaultLimits, ...form.graph.limits };
		topologyDrafts = {};
		advanced = false;
		editStructure = false;
		selectedTask = '';
		error = '';
		saved = '';
		notice = '';
	}
	function switchTopology(kind: TopologyKind) {
		if (!form?.graph || kind === topology) return;
		topologyDrafts[topology] = structuredClone({
			roles: form.roles,
			graph: form.graph,
			lead_role_id: form.lead_role_id
		});
		const source =
			topologyDrafts[kind] ?? starters.find((s) => hasPlanning(s.graph) === (kind === 'generated'));
		if (!source?.graph) return;
		const target = structuredClone(source);
		form = {
			...form,
			roles: target.roles,
			lead_role_id: target.lead_role_id,
			graph: { ...target.graph!, limits: { ...form.graph.limits } }
		};
		advanced = false;
		editStructure = false;
		selectedTask = '';
		saved = '';
		notice =
			'Topology changed in this draft. After saving changed roles, update their Project bindings.';
	}
	function flatParallel() {
		if (!form?.graph || hasPlanning(form.graph)) return false;
		const graph = form.graph,
			calls = graph.nodes.filter((n) => n.op === 'call'),
			work = graph.nodes.filter((n) => !boundaryNode(n)),
			collector = work.find((n) => n.op === 'join');
		return (
			calls.length === form.roles?.length &&
			work.length === calls.length + 1 &&
			calls.every(
				(n) =>
					form!.roles!.some((r) => r.id === n.binding) &&
					JSON.stringify(n.inputs) === JSON.stringify({ task: workflowInputRef(graph) })
			) &&
			collector?.policy === 'all_terminal' &&
			graph.edges
				.filter((e) => !e.generated)
				.every(
					(e) => calls.some((n) => n.id === e.from) && e.to === collector.id && e.on === 'terminal'
				)
		);
	}
	function refreshParallel() {
		if (!form?.graph) return;
		const graph = form.graph,
			previous = graph.nodes.filter((n) => n.op === 'call');
		const roles = form.roles ?? [];
		const nodes = roles.map((role) => {
			const old = previous.find((n) => n.binding === role.id);
			return (
				old ?? {
					id: 'task_' + crypto.randomUUID().slice(0, 8),
					op: 'call' as const,
					binding: role.id,
					context_key: role.id,
					label: role.name,
					instruction: previous[0]?.instruction ?? '',
					inputs: { task: workflowInputRef(graph) }
				}
			);
		});
		const result = graph.nodes.find((n) => n.op === 'join')!;
		form.graph = synchronizeGraph({
			...graph,
			nodes: [
				...graph.nodes.filter(boundaryNode),
				...nodes,
				{
					...result,
					inputs: Object.fromEntries(
						roles.map((r, i) => [
							r.id,
							{ source: 'node', node: nodes[i].id, pointer: '/report', optional: true }
						])
					)
				}
			],
			edges: nodes.map((n) => ({ from: n.id, to: result.id, on: 'terminal' }))
		});
	}
	function removable(id: string) {
		return (
			(form?.roles?.length ?? 0) > 1 &&
			(flatParallel() || !form?.graph?.nodes.some((n) => n.binding === id))
		);
	}
	function removeRole(id: string) {
		if (!form || !removable(id)) return;
		const parallel = flatParallel();
		form.roles = form.roles?.filter((r) => r.id !== id);
		form.lead_role_id = form.roles?.[0]?.id;
		if (parallel) refreshParallel();
	}
	function addRole() {
		if (!form || (form.roles?.length ?? 0) >= 8) return;
		const parallel = flatParallel();
		const role = nextExecutorRole(form.roles ?? []);
		if (parallel) role.instructions = form.roles?.[0]?.instructions ?? '';
		form.roles = [...(form.roles ?? []), role];
		if (parallel) refreshParallel();
	}
	function sharedInstructions(value: string) {
		if (!form) return;
		if (flatParallel())
			for (const node of form.graph!.nodes) {
				const role = form.roles?.find((r) => r.id === node.binding);
				if (
					role &&
					node.instruction ===
						role.instructions + (form.instructions ? '\n' + form.instructions : '')
				)
					node.instruction = role.instructions + (value ? '\n' + value : '');
			}
		form.instructions = value;
	}
	function roleInstructions(id: string, value: string) {
		const role = form?.roles?.find((r) => r.id === id);
		if (!role || !form) return;
		if (flatParallel())
			for (const node of form.graph!.nodes)
				if (
					node.binding === id &&
					node.instruction ===
						role.instructions + (form.instructions ? '\n' + form.instructions : '')
				)
					node.instruction = value + (form.instructions ? '\n' + form.instructions : '');
		role.instructions = value;
		form = form;
	}

	async function loadLibrary() {
		[workflows, deleted, starters] = await Promise.all([
			getWorkflows(),
			getDeletedWorkflows(),
			getWorkflowStarters()
		]);
	}

	$: graphModules = [
		...(form?.roles ?? []),
		...workflows
			.filter((w) => w.id !== form?.id)
			.map((w) => ({
				id: 'workflow:' + w.id,
				name: 'Workflow · ' + w.name,
				module_type: 'workflow',
				input_schema: w.graph?.input_schema,
				output_schema: w.graph?.output_schema
			}))
	];
	function newTemplate(copy = false) {
		const source = copy ? form : starters[0];
		if (!source) return;
		const base = copy ? `${source.name} copy` : 'My workflow';
		let name = base,
			index = 2;
		while (workflows.some((w) => w.name === name)) name = `${base} ${index++}`;
		creation = { name, source: structuredClone(source), copy };
		error = '';
		notice = '';
	}
	async function create() {
		if (!creation) return;
		busy = true;
		error = '';
		try {
			const result = await saveWorkflow({
				...creation.source,
				id: undefined,
				version: undefined,
				builtin_key: undefined,
				name: creation.name
			});
			await loadLibrary();
			choose(result);
			creation = null;
			notice = 'Template created. Choose its default participants in a Project.';
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
	async function removeTemplate() {
		if (!form?.id) return;
		busy = true;
		error = '';
		try {
			const name = form.name;
			await deleteWorkflow(form);
			await loadLibrary();
			if (workflows[0]) choose(workflows[0]);
			else form = null;
			notice = `Deleted “${name}”. Restore it from Deleted templates. Existing chats are preserved.`;
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
	async function restore(row: Workflow) {
		busy = true;
		error = '';
		try {
			const result = await restoreWorkflow(row.id!);
			await loadLibrary();
			choose(result);
			notice = 'Template restored with its original identity and Project bindings.';
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}

	async function save() {
		if (!form) return;
		busy = true;
		error = '';
		saved = '';
		try {
			const result = await saveWorkflow(form);
			await loadLibrary();
			choose(result);
			notice = '';
			saved = 'Template saved. Bind new or changed roles in the Project.';
		} catch (e) {
			error = String(e);
		} finally {
			busy = false;
		}
	}
	onMount(() => {
		void loadLibrary()
			.then(() => {
				if (workflows[0]) choose(workflows[0]);
			})
			.catch((e) => (error = String(e)))
			.finally(() => (loading = false));
	});
</script>

<div class="workflow-settings mx-auto max-w-5xl px-5 py-6 md:px-8 md:py-8">
	<header class="mb-6 flex flex-wrap items-start justify-between gap-4">
		<div>
			<h1 class="text-2xl font-semibold tracking-tight">Workflows</h1>
			<p class="mt-2 text-sm leading-6 text-gray-500">
				Reusable collaboration rules. Projects provide the actual agents and models.
			</p>
		</div>
		<button
			type="button"
			class="save-button"
			disabled={busy || !starters.length}
			on:click={() => newTemplate()}>+ New template</button
		>
	</header>
	{#if creation}<form
			aria-label="Create workflow template"
			class="mb-6 space-y-4 rounded-xl border p-5 dark:border-gray-700"
			on:submit|preventDefault={create}
		>
			<h2 class="text-sm font-semibold">{creation.copy ? 'Duplicate template' : 'New template'}</h2>
			<div class="grid gap-4 sm:grid-cols-2">
				<label
					>Name<input
						aria-label="New template name"
						required
						maxlength="100"
						bind:value={creation.name}
					/></label
				>
				{#if !creation.copy}<label
						>Start with an example<select
							aria-label="Template starting point"
							value={creation.source.name}
							on:change={(e) => {
								if (creation)
									creation.source = structuredClone(
										starters.find((w) => w.name === e.currentTarget.value)!
									);
							}}
						>
							{#each starters as starter}<option value={starter.name}
									>{starter.name} · {hasPlanning(starter.graph)
										? 'AI-generated graph'
										: 'Predefined graph'}</option
								>{/each}</select
						></label
					>{/if}
			</div>
			<p class="hint">
				A starting point supplies editable rules, not another execution mode. All templates use the
				same graph runtime.
			</p>
			{#if error}<p role="alert" class="text-xs text-red-600">{error}</p>{/if}
			<div class="flex gap-3">
				<button class="save-button" disabled={busy}>{busy ? 'Creating…' : 'Create template'}</button
				>
				<button
					type="button"
					class="secondary-button"
					disabled={busy}
					on:click={() => (creation = null)}>Cancel</button
				>
			</div>
		</form>{/if}
	<div class="mb-6 flex flex-wrap items-end gap-3">
		<label class="min-w-0 flex-1"
			>Template<select
				aria-label="Workflow template"
				value={form?.id ?? ''}
				disabled={busy || !workflows.length}
				on:change={(e) => {
					const row = workflows.find((w) => w.id === e.currentTarget.value);
					if (row) {
						choose(row);
						notice = '';
					}
				}}
			>
				{#if !workflows.length}<option value="">No active templates</option>{/if}
				{#each workflows as workflow}<option value={workflow.id}>{workflow.name}</option>{/each}
			</select></label
		>
		<button
			type="button"
			class="secondary-button"
			disabled={busy || !form?.id}
			on:click={() => newTemplate(true)}>Duplicate</button
		>
		<button
			type="button"
			class="secondary-button text-red-600"
			disabled={busy || !form?.id}
			on:click={removeTemplate}>Delete</button
		>
	</div>
	{#if notice}<p role="status" class="mb-5 text-xs leading-5 text-emerald-600">{notice}</p>{/if}
	{#if deleted.length}<details class="mb-6 rounded-xl border p-4 dark:border-gray-700">
			<summary class="cursor-pointer text-xs">Deleted templates · {deleted.length}</summary>
			<p class="hint mt-3">
				Deleted templates stay available to their existing chats. Restore them to use them in new
				chats.
			</p>
			{#each deleted as row}<div class="mt-3 flex items-center justify-between gap-3 text-sm">
					<span>{row.name}</span>
					<button
						type="button"
						class="secondary-button"
						disabled={busy}
						aria-label={`Restore ${row.name}`}
						on:click={() => restore(row)}>Restore</button
					>
				</div>{/each}
		</details>{/if}

	{#if form?.graph}
		<form class="min-w-0" on:submit|preventDefault={save}>
			<WorkflowSection
				title="Execution policy"
				description="Basic information and when execution starts."
			>
				<div class="grid gap-4 sm:grid-cols-2">
					<label
						>Name<input
							aria-label="Workflow name"
							required
							maxlength="100"
							bind:value={form.name}
						/></label
					>
					<label
						>Execution<select
							aria-label="Default workflow execution"
							bind:value={form.execution_policy}
						>
							<option value="automatic">Automatic</option><option value="review"
								>Approve plan</option
							><option value="step">Step by step</option>
						</select></label
					>
					<label class="sm:col-span-2"
						>Description<input
							aria-label="Workflow description"
							maxlength="500"
							bind:value={form.description}
						/></label
					>
				</div>
			</WorkflowSection>
			<WorkflowSection
				title="Module roles"
				description="Reusable module bindings. Nodes define the work; Projects supply the actual agents and models."
			>
				<div class="space-y-3">
					{#each form.roles ?? [] as role (role.id)}<details class="role-card">
							<summary class="flex cursor-pointer items-center justify-between gap-3">
								<span class="text-sm font-medium">{role.name}</span>
								<span class="hint">Configure</span>
							</summary>
							<div class="mt-4 space-y-3">
								<label
									>Name<input
										aria-label={`Role name ${role.id}`}
										required
										bind:value={role.name}
									/></label
								>
								<label
									>Instructions<textarea
										aria-label={`Role instructions ${role.id}`}
										value={role.instructions}
										on:input={(e) => roleInstructions(role.id, e.currentTarget.value)}
										rows="3"
									></textarea></label
								>
								<button
									type="button"
									class="remove-role"
									disabled={!removable(role.id)}
									title="Remove this role’s graph nodes first if they are referenced."
									aria-label={`Remove ${role.name}`}
									on:click={() => removeRole(role.id)}>Remove role</button
								>
							</div>
						</details>{/each}
					<button
						type="button"
						class="secondary-button"
						disabled={(form.roles?.length ?? 0) >= 8}
						on:click={addRole}>+ Add role</button
					>
					<details>
						<summary class="cursor-pointer text-xs text-gray-500">Shared instructions</summary>
						<textarea
							class="mt-3"
							aria-label="Workflow instructions"
							value={form.instructions}
							rows="3"
							on:input={(e) => sharedInstructions(e.currentTarget.value)}
						></textarea>
					</details>
				</div>
			</WorkflowSection>
			<WorkflowSection title="Topology" description="Where the execution graph comes from.">
				<label
					>Graph source<select
						aria-label="Workflow topology"
						value={topology}
						on:change={(e) => switchTopology(e.currentTarget.value as TopologyKind)}
					>
						<option value="predefined">Predefined graph</option><option value="generated"
							>AI-generated graph</option
						>
					</select></label
				>
				<p class="hint mt-3">
					{topology === 'generated'
						? 'A planning node publishes task graphs from the Project module catalog. Select any node to configure the same responsibilities, inputs, outputs and bindings.'
						: 'Execution follows the graph below. Select a node to edit its task, module and required results.'}
				</p>
				<WorkflowGuide
					bind:template={form}
					on:select={(e) => {
						selectedTask = e.detail;
						editStructure = true;
					}}
				/>
				<div class="mt-4 overflow-hidden rounded-xl border border-gray-100 dark:border-gray-800">
					<GraphCanvas
						plan={form.graph}
						modules={graphModules}
						definition
						selected={selectedTask}
						on:select={(e) => {
							if (!e.detail) return;
							selectedTask = e.detail;
							editStructure = true;
						}}
					/>
				</div>
				<p class="hint mt-3">
					Arrows show execution order; labels name source fields. Select a node for its complete
					input and output bindings. Independent branches can run together within the parallel-call
					limit.
				</p>
				<details class="mt-4" bind:open={editStructure}>
					<summary class="cursor-pointer text-xs font-medium">Edit structure</summary>
					{#if editStructure}<div class="pt-4">
							<GraphEditor
								bind:value={form.graph}
								bind:selected={selectedTask}
								modules={graphModules}
								view="editor"
							/>
						</div>{/if}
				</details>
			</WorkflowSection>
			<WorkflowSection title="Budget" description="Limits for one run.">
				{#if form.graph.limits}<div class="grid grid-cols-2 gap-4">
						<label
							>Token limit<input
								aria-label="Reported token budget"
								type="number"
								min="1"
								max="1000000"
								required
								bind:value={form.graph.limits.max_tokens}
							/></label
						>
						<label
							>Time limit · seconds<input
								aria-label="Workflow time limit"
								type="number"
								min="1"
								max="600"
								required
								bind:value={form.graph.limits.max_seconds}
							/></label
						>
						<label
							>Module calls<input
								aria-label="Workflow call limit"
								type="number"
								min="1"
								max="24"
								required
								bind:value={form.graph.limits.max_calls}
							/></label
						>
						<label
							>Parallel calls<input
								aria-label="Workflow concurrency"
								type="number"
								min="1"
								max="2"
								required
								bind:value={form.graph.limits.max_concurrency}
							/></label
						>
					</div>{/if}
			</WorkflowSection>
			<details class="advanced" bind:open={advanced}>
				<summary>Advanced</summary>
				{#if advanced}<div class="space-y-4 pt-4">
						<p class="hint">Graph definition, optional control rules and stable role IDs.</p>
						<GraphEditor bind:value={form.graph} modules={graphModules} view="definition" />
						<div class="border-t border-gray-100 pt-3 dark:border-gray-800">
							{#each form.roles ?? [] as role}<div
									class="flex flex-wrap justify-between gap-2 py-1 text-xs"
								>
									<span>{role.name}</span><code class="text-gray-500">{role.id}</code>
								</div>{/each}
						</div>
					</div>{/if}
			</details>

			<div
				class="save-bar mt-5 flex flex-wrap items-center justify-between gap-4 rounded-xl bg-gray-50 p-4 dark:bg-gray-850"
			>
				<div class="min-w-0 flex-1 text-xs leading-5 text-gray-500">
					{#if error}<p role="alert" class="text-red-600">{error}</p>{:else if saved}<p
							role="status"
							class="text-emerald-600"
						>
							{saved}
						</p>{:else}Applies to future runs across Projects. Completed runs keep their saved
						configuration.{/if}
				</div>
				<button disabled={busy} class="save-button">{busy ? 'Saving…' : 'Save template'}</button>
			</div>
		</form>
	{:else}<p role={error ? 'alert' : 'status'} class="text-sm text-gray-500">
			{error ||
				(loading
					? 'Loading workflows…'
					: 'No active templates. Create a template or restore a deleted one.')}
		</p>{/if}
</div>

<style>
	.workflow-settings label {
		display: block;
		font-size: 0.75rem;
		font-weight: 500;
	}
	.workflow-settings input:not([type='checkbox']),
	.workflow-settings select,
	.workflow-settings textarea {
		display: block;
		margin-top: 0.5rem;
		width: 100%;
		min-width: 0;
		border: 1px solid rgb(128 128 128 / 22%);
		border-radius: 0.625rem;
		padding: 0.625rem 0.75rem;
		background: transparent;
		font-size: 0.8125rem;
		font-weight: 400;
		line-height: 1.5;
	}
	.workflow-settings textarea {
		resize: vertical;
	}
	.workflow-settings select {
		appearance: none;
		padding-right: 2rem;
		background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none' stroke='%23808080' stroke-width='1.5' viewBox='0 0 24 24'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
		background-repeat: no-repeat;
		background-position: right 0.75rem center;
	}
	.workflow-settings input:focus,
	.workflow-settings select:focus,
	.workflow-settings textarea:focus {
		outline: 2px solid rgb(99 102 241 / 45%);
		outline-offset: 1px;
	}
	.hint {
		font-size: 0.75rem;
		line-height: 1.6;
		color: #808080;
	}
	.role-card {
		border: 1px solid rgb(128 128 128 / 17%);
		border-radius: 0.75rem;
		padding: 0.875rem 1rem;
	}
	.secondary-button {
		border: 1px solid rgb(128 128 128 / 25%);
		border-radius: 0.625rem;
		padding: 0.5rem 0.75rem;
		font-size: 0.75rem;
	}
	.remove-role {
		font-size: 0.6875rem;
		color: #808080;
	}
	button:disabled {
		opacity: 0.4;
		cursor: default;
	}
	.advanced {
		border-top: 1px solid rgb(128 128 128 / 15%);
		padding: 1.25rem 0;
	}
	.advanced summary {
		cursor: pointer;
		font-size: 0.875rem;
		font-weight: 500;
	}
	.save-button {
		border-radius: 0.625rem;
		padding: 0.625rem 1rem;
		background: #4f46e5;
		color: white;
		font-size: 0.8125rem;
		font-weight: 500;
		white-space: nowrap;
	}
	.save-button:hover {
		background: #4338ca;
	}
	@media (max-width: 640px) {
		.save-bar .save-button {
			width: 100%;
		}
	}
</style>
