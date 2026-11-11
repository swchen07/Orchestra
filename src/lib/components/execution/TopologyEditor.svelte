<script lang="ts">
	import { models } from '$lib/stores';
	import { frameworkNames, modelsForProject } from '$lib/stores/execution';
	import {
		saveProjectTopology,
		startTopologyRun,
		stopTopologyRun,
		type ProjectDashboard,
		type Topology,
		type TopologyNode
	} from '$lib/apis/execution';
	export let data: ProjectDashboard;
	export let onRefresh: () => Promise<void>;
	let draft: Topology | null = null;
	let error = '';
	let busy = false;
	let selected = '';
	let task = '';
	$: choices = modelsForProject($models, data.project).filter(
		(model) => model.execution?.framework_id !== 'direct'
	);
	$: definition = data.topologies.find((topology) => topology.id === selected);
	const labels: Record<string, string> = {
		waiting: 'Waiting',
		queued: 'Queued',
		running: 'Running',
		done: 'Done',
		failed: 'Failed',
		cancelled: 'Stopped',
		blocked: 'Blocked',
		interrupted: 'Interrupted'
	};
	function template(parallel = false) {
		const defaultModel =
			choices.find((model) => model.id === data.project.data.execution_project.default_model_id) ??
			choices[0];
		if (!defaultModel) {
			error = 'Add an available provider to this project first.';
			return;
		}
		const roles = parallel
			? ['Analysis A', 'Analysis B', 'Summary']
			: ['Planner', 'Execute', 'Review'];
		draft = {
			name: parallel ? 'Parallel analysis & summary' : 'Plan, execute & review',
			nodes: roles.map((name, index) => ({
				id: `n${index + 1}`,
				name,
				model_id: defaultModel.id,
				profile_id: `role:${defaultModel.execution?.framework_id}:${['planner', 'worker', 'reviewer'][index]}`,
				prompt: parallel
					? index === 2
						? 'Integrate upstream results, explain disagreements and state a conclusion.'
						: 'Analyze independently and provide evidence for your answer.'
					: [
							'Break down goals and constraints and propose an actionable plan.',
							'Complete the task using the upstream plan.',
							'Check upstream results for errors and actionable improvements.'
						][index],
				depends_on: parallel ? (index === 2 ? ['n1', 'n2'] : []) : index ? [`n${index}`] : []
			}))
		};
		error = '';
	}
	function edit(topology: Topology) {
		draft = structuredClone(topology);
		error = '';
	}
	function updateDependency(node: TopologyNode, id: string, checked: boolean) {
		node.depends_on = checked
			? [...node.depends_on, id]
			: node.depends_on.filter((value) => value !== id);
		draft = draft;
	}
	function addNode() {
		if (!draft || draft.nodes.length >= 8 || !choices[0]) return;
		const id = `node${crypto.randomUUID().slice(0, 8)}`;
		draft = {
			...draft,
			nodes: [
				...draft.nodes,
				{ id, name: 'New node', model_id: choices[0].id, prompt: '', depends_on: [] }
			]
		};
	}
	function removeNode(id: string) {
		if (draft)
			draft = {
				...draft,
				nodes: draft.nodes
					.filter((node) => node.id !== id)
					.map((node) => ({ ...node, depends_on: node.depends_on.filter((dep) => dep !== id) }))
			};
	}
	async function save() {
		if (!draft) return;
		busy = true;
		error = '';
		try {
			const saved = await saveProjectTopology(data.project.id, draft);
			selected = saved.id ?? '';
			draft = null;
			await onRefresh();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}
	async function start() {
		if (!selected) return;
		busy = true;
		error = '';
		try {
			await startTopologyRun(data.project.id, selected, task);
			await onRefresh();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}
	async function stop(id: string) {
		busy = true;
		try {
			await stopTopologyRun(data.project.id, id);
			await onRefresh();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busy = false;
		}
	}
	function layout(topology: Topology) {
		const positions: Record<string, { x: number; y: number }> = {};
		const done = new Set<string>();
		let layer = 0;
		while (done.size < topology.nodes.length && layer < 9) {
			const nodes = topology.nodes.filter(
				(node) => !done.has(node.id) && node.depends_on.every((id) => done.has(id))
			);
			if (!nodes.length) break;
			nodes.forEach(
				(node, index) => (positions[node.id] = { x: 30 + layer * 235, y: 25 + index * 110 })
			);
			nodes.forEach((node) => done.add(node.id));
			layer++;
		}
		return positions;
	}
	function nodeSource(node: TopologyNode) {
		const execution = $models.find((model) => model.id === node.model_id)?.execution;
		return execution
			? `${frameworkNames[execution.framework_id]} / ${execution.provider_name}`
			: 'Not configured';
	}
	$: preview = draft ?? definition;
	$: positions = preview ? layout(preview) : {};
	$: graphWidth = Math.max(680, ...Object.values(positions).map((p) => p.x + 215));
	$: graphHeight = Math.max(150, ...Object.values(positions).map((p) => p.y + 100));
</script>

<div class="space-y-5">
	<div class="flex flex-wrap justify-between gap-3 items-center">
		<div>
			<h2 class="font-medium">Legacy DAG workflows</h2>
			<p class="text-sm text-gray-500 mt-1">
				Fixed task dependencies. Use Configuration → Workflows for reusable participant
				collaboration.
			</p>
		</div>
		<div class="flex gap-2">
			<button type="button" class="rounded-lg border px-3 py-2 text-sm" on:click={() => template()}
				>Sequential template</button
			><button
				type="button"
				class="rounded-lg border px-3 py-2 text-sm"
				on:click={() => template(true)}>Parallel template</button
			>
		</div>
	</div>
	{#if error}<p role="alert" class="text-sm text-red-600">{error}</p>{/if}
	<div class="flex flex-wrap gap-2">
		{#each data.topologies as topology}<button
				type="button"
				class="rounded-lg px-3 py-2 text-sm border {selected === topology.id
					? 'bg-gray-100 dark:bg-gray-800'
					: ''}"
				on:click={() => {
					selected = topology.id ?? '';
					draft = null;
				}}>{topology.name}</button
			>{/each}
	</div>
	{#if preview}
		<div
			class="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-800 p-2"
			aria-label="Workflow preview"
		>
			<svg
				width={graphWidth}
				height={graphHeight}
				viewBox={`0 0 ${graphWidth} ${graphHeight}`}
				role="img"
				aria-label="Nodes and dependencies"
			>
				<defs
					><marker
						id="topology-arrow"
						viewBox="0 0 10 10"
						refX="9"
						refY="5"
						markerWidth="6"
						markerHeight="6"
						orient="auto-start-reverse"
						><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker
					></defs
				>
				{#each preview.nodes as node}{#if positions[node.id]}{#each node.depends_on as parent}{#if positions[parent]}<path
									d={`M ${positions[parent].x + 195} ${positions[parent].y + 37} C ${positions[parent].x + 220} ${positions[parent].y + 37}, ${positions[node.id].x - 25} ${positions[node.id].y + 37}, ${positions[node.id].x} ${positions[node.id].y + 37}`}
									fill="none"
									stroke="currentColor"
									class="text-gray-400"
									stroke-width="1.5"
									marker-end="url(#topology-arrow)"
								/>{/if}{/each}
						<g transform={`translate(${positions[node.id].x},${positions[node.id].y})`}
							><rect
								width="195"
								height="74"
								rx="12"
								fill="currentColor"
								class="text-gray-50 dark:text-gray-850"
								stroke="#9ca3af"
							/><text
								x="12"
								y="22"
								fill="currentColor"
								class="text-gray-900 dark:text-gray-100"
								font-size="13">{node.name.slice(0, 18)}</text
							><text x="12" y="42" fill="currentColor" class="text-gray-500" font-size="10"
								>{nodeSource(node).slice(0, 28)}</text
							><text x="12" y="60" fill="currentColor" class="text-gray-500" font-size="9"
								>{(
									$models.find((model) => model.id === node.model_id)?.execution?.model_id ??
									node.model_id
								).slice(0, 30)}</text
							></g
						>
					{/if}{/each}
			</svg>
			{#if Object.keys(positions).length !== preview.nodes.length}<p class="text-xs text-red-600">
					Invalid dependency order. Save to check cycles and missing nodes.
				</p>{/if}
		</div>
	{/if}
	{#if draft}
		<label class="block text-sm"
			>Preset name<input
				aria-label="Workflow name"
				bind:value={draft.name}
				class="block w-full rounded-lg border bg-transparent p-2 mt-2"
			/></label
		>
		<div class="space-y-4">
			{#each draft.nodes as node (node.id)}<section
					class="rounded-xl border border-gray-200 dark:border-gray-800 p-4 space-y-3"
				>
					<div class="flex gap-3">
						<input
							aria-label={`Node name ${node.id}`}
							bind:value={node.name}
							class="flex-1 min-w-0 border-b bg-transparent py-1 text-sm"
						/><button
							type="button"
							class="text-xs text-gray-500"
							on:click={() => removeNode(node.id)}>Remove node</button
						>
					</div>
					<label class="block text-xs text-gray-500"
						>Framework / provider / model<select
							aria-label={`Node model ${node.id}`}
							bind:value={node.model_id}
							on:change={() => (node.profile_id = null)}
							class="block w-full mt-1 rounded-lg border bg-transparent p-2 text-sm"
							>{#each choices as model}<option value={model.id}
									>{frameworkNames[model.execution?.framework_id ?? 'direct']} / {model.execution
										?.provider_name} / {model.name}</option
								>{/each}</select
						></label
					>
					<label class="block text-xs text-gray-500"
						>Agent / Profile<select
							aria-label={`Node preset ${node.id}`}
							bind:value={node.profile_id}
							class="block w-full mt-1 rounded-lg border bg-transparent p-2 text-sm"
							><option value={null}>Project default</option
							>{#each data.profiles.filter((profile) => profile.available && profile.framework_id === $models.find((model) => model.id === node.model_id)?.execution?.framework_id) as profile}<option
									value={profile.id}>{profile.name} · {profile.source}</option
								>{/each}</select
						></label
					>
					<label class="block text-xs text-gray-500"
						>Node instructions<textarea
							aria-label={`Node instructions ${node.id}`}
							rows="2"
							bind:value={node.prompt}
							class="block mt-1 w-full rounded-lg border bg-transparent p-2 text-sm"
						></textarea></label
					>
					<fieldset>
						<legend class="text-xs text-gray-500 mb-2">Dependencies</legend>
						<div class="flex flex-wrap gap-3">
							{#each draft.nodes.filter((parent) => parent.id !== node.id) as parent}<label
									class="text-sm flex items-center gap-1.5"
									><input
										type="checkbox"
										aria-label={`${node.name} depends on ${parent.name}`}
										checked={node.depends_on.includes(parent.id)}
										on:change={(event) =>
											updateDependency(node, parent.id, event.currentTarget.checked)}
									/>{parent.name}</label
								>{/each}
						</div>
					</fieldset>
				</section>{/each}
		</div>
		<div class="flex flex-wrap justify-between gap-3">
			<button type="button" disabled={draft.nodes.length >= 8} class="text-sm" on:click={addNode}
				>Add node</button
			>
			<div class="flex gap-3">
				<button type="button" class="text-sm text-gray-500" on:click={() => (draft = null)}
					>Cancel</button
				><button
					type="button"
					class="rounded-lg bg-gray-900 text-white dark:bg-white dark:text-gray-900 px-4 py-2 text-sm"
					disabled={busy}
					on:click={save}>Save & validate</button
				>
			</div>
		</div>
	{:else if definition}
		<div class="flex justify-between items-center">
			<p class="text-sm text-gray-500">
				{definition.nodes.length} nodes; up to two run concurrently.
			</p>
			<button type="button" class="text-sm" on:click={() => edit(definition)}>Edit preset</button>
		</div>
		<label class="block text-sm"
			>Task<textarea
				aria-label="Workflow task"
				rows="3"
				bind:value={task}
				placeholder="Describe the task for this group"
				class="mt-2 block w-full rounded-lg border bg-transparent p-3"
			></textarea></label
		>
		<div class="flex flex-wrap justify-between items-center gap-3">
			<p class="text-xs text-gray-500">
				Each node runs independently and saves its result in a project chat.
			</p>
			<button
				type="button"
				disabled={busy || !task.trim()}
				class="rounded-lg bg-gray-900 text-white dark:bg-white dark:text-gray-900 px-4 py-2 text-sm disabled:opacity-40"
				on:click={start}>Run workflow</button
			>
		</div>
	{:else}<p class="text-sm text-gray-500">
			Choose a template, configure participants and dependencies, then save.
		</p>{/if}
	<div class="pt-4 border-t space-y-4">
		<h3 class="font-medium">Run history</h3>
		{#each data.runs as run}<article
				class="rounded-xl border border-gray-200 dark:border-gray-800 p-4"
			>
				<div class="flex justify-between gap-3">
					<div>
						<h4 class="text-sm font-medium">{run.name} · {labels[run.status] ?? run.status}</h4>
						<p class="text-xs text-gray-500 mt-1 whitespace-pre-wrap">{run.task}</p>
					</div>
					{#if ['queued', 'running'].includes(run.status)}<button
							type="button"
							class="text-sm text-red-600 shrink-0"
							disabled={busy}
							on:click={() => stop(run.id)}>Stop run</button
						>{/if}
				</div>
				{#if run.error}<p class="text-xs text-red-600 mt-2">{run.error}</p>{/if}
				<div class="flex flex-wrap gap-2 mt-3">
					{#each Object.values(run.nodes) as node}<div
							class="rounded-lg bg-gray-50 dark:bg-gray-850 px-3 py-2 text-xs"
						>
							<span>{node.name} · {labels[node.status] ?? node.status}</span>{#if node.chat_id}<a
									href={`/c/${node.chat_id}`}
									class="ml-2 underline">Open chat</a
								>{/if}{#if node.error}<p class="mt-1 text-red-600">{node.error}</p>{/if}
						</div>{/each}
				</div>
			</article>{:else}<p class="text-sm text-gray-500">No runs yet.</p>{/each}
	</div>
</div>
