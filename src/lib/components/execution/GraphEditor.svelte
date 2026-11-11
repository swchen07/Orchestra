<script lang="ts">
	import type { GraphNode, NodeState, WorkflowGraph } from '$lib/apis/execution';
	import ReferenceEditor from './ReferenceEditor.svelte';
	import NodeOutputEditor from './NodeOutputEditor.svelte';
	import ModulePicker from './ModulePicker.svelte';
	import { bindModuleContract, bindingIssues } from './moduleSelection';
	import {
		nodeOperations,
		synchronizeGraph,
		outputNode,
		capabilitySchema,
		referenceNodes,
		boundaryNode,
		workflowInputRef,
		synchronizeBoundaryPorts,
		dependencyChoices,
		setDependency,
		actionFor,
		actionId,
		feedbackTargetChoices
	} from './nodeProgram';
	export let value: WorkflowGraph;
	export let modules: {
		id: string;
		name: string;
		module_type?: string;
		input_schema?: unknown;
		output_schema?: unknown;
	}[] = [];
	export let states: Record<string, NodeState> = {};
	export let definitionFirst = false;
	export let readOnly = false;
	export let view: 'both' | 'editor' | 'definition' = 'both';
	export let selected = '';
	let raw = definitionFirst;
	let source = JSON.stringify(value, null, 2);
	let sourceBase = source;
	let sourceEdited = false;
	let error = '';
	$: if (view !== 'both') raw = view === 'definition';
	$: definition = JSON.stringify(value, null, 2);
	$: if (raw && definition !== sourceBase && !sourceEdited) {
		sourceBase = definition;
		source = definition;
	}
	$: if (!value.nodes.some((n) => n.id === selected)) selected = value.nodes[0]?.id ?? '';
	$: node = value.nodes.find((n) => n.id === selected);
	$: selectedModule = modules.find((m) => m.id === node?.binding);
	$: moduleIssues = node ? bindingIssues(node, selectedModule) : [];
	$: locked =
		readOnly ||
		Boolean(node && states[node.id] && !['pending', 'ready'].includes(states[node.id].status));
	function touch() {
		value = synchronizeGraph({
			...value,
			nodes: value.nodes.map(synchronizeBoundaryPorts),
			edges: [...value.edges]
		});
	}
	function selectOutput(id: string) {
		if (value.schema_version === '2.0')
			value.outputs = {
				...value.outputs,
				answer: {
					source: 'node',
					node: id,
					pointer: value.nodes.find((n) => n.id === id)?.op === 'review' ? '/answer' : '/report'
				}
			};
		else value.result_node = id;
		touch();
	}
	function bindModule() {
		if (!node) return;
		const replacement = bindModuleContract(
			node,
			modules.find((m) => m.id === node!.binding),
			value
		);
		value = {
			...value,
			nodes: value.nodes.map((n) => (n.id === replacement.id ? replacement : n))
		};
		touch();
	}
	function setInputContract(text: string) {
		if (!node) return;
		try {
			node.input_schema = JSON.parse(text);
			node.input_schema_inferred = false;
			error = '';
			touch();
		} catch {
			error = 'Input contract must be valid JSON Schema.';
		}
	}
	function addInput() {
		if (!node || locked) return;
		let key = 'input',
			index = 2;
		while (node.inputs[key]) key = 'input_' + index++;
		node.inputs = {
			...node.inputs,
			[key]:
				node.op === 'input'
					? { source: 'input', pointer: '/' + key }
					: { source: 'node', node: '', pointer: '' }
		};
		touch();
	}
	function renameInput(key: string, name: string) {
		if (!node || !name || (name !== key && node.inputs[name])) return;
		if (
			node.op === 'input' &&
			node.inputs[key].source === 'input' &&
			node.inputs[key].pointer === '/' + key
		) {
			node.inputs[key] = { ...node.inputs[key], pointer: '/' + name };
			const schema = value.input_schema as Record<string, any> | undefined;
			if (schema?.properties?.[key] !== undefined) {
				schema.properties[name] = schema.properties[key];
				delete schema.properties[key];
				schema.required = (schema.required ?? []).map((field: string) =>
					field === key ? name : field
				);
			}
		}
		node.inputs = Object.fromEntries(
			Object.entries(node.inputs).map(([k, v]) => [k === key ? name : k, v])
		);
		if (
			node.op === 'output' &&
			(value.delivery_port === key || (!value.delivery_port && key === 'answer'))
		)
			value.delivery_port = name;
		touch();
	}
	function add() {
		if (readOnly) return;
		const id = 'task_' + crypto.randomUUID().slice(0, 8);
		value = {
			...value,
			nodes: [
				...value.nodes,
				{
					id,
					label: 'New node',
					op: 'call',
					context_key: id,
					binding: modules[0]?.id ?? '',
					instruction: '',
					inputs: { task: workflowInputRef(value) }
				}
			]
		};
		selected = id;
	}
	function remove() {
		if (!node || locked) return;
		if (outputNode(value) === selected) {
			error = 'Choose another final output before removing this node.';
			return;
		}
		if (
			value.nodes.some(
				(n) =>
					n.id !== selected &&
					Object.values(n.inputs).some((ref) => referenceNodes(ref, value).includes(selected))
			)
		) {
			error = 'Remove this node from downstream inputs before deleting it.';
			return;
		}
		value = {
			...value,
			nodes: value.nodes.filter((n) => n.id !== selected),
			edges: value.edges.filter((e) => e.from !== selected && e.to !== selected)
		};
	}
	function dependency(id: string, checked: boolean) {
		if (!node || locked) return;
		value = setDependency(value, node.id, id, checked);
	}
	function changeOperation(op: GraphNode['op']) {
		if (!node || locked) return;
		const replacement: GraphNode = {
			id: node.id,
			label: node.label,
			op,
			action: actionId(op),
			enabled: node.enabled,
			inputs: { ...node.inputs }
		};
		if (['call', 'route', 'map', 'loop', 'plan', 'review', 'dispatch', 'workflow'].includes(op))
			Object.assign(replacement, {
				binding: modules.find((m) => op !== 'workflow' || m.module_type === 'workflow')?.id ?? '',
				instruction: node.instruction ?? ''
			});
		if (op === 'workflow') {
			const child = modules.find((m) => m.id === replacement.binding);
			replacement.context_key = node.context_key ?? node.id;
			if (child?.input_schema) replacement.input_schema = child.input_schema;
			if (child?.output_schema) replacement.output_schema = child.output_schema;
			const fields = (child?.input_schema as any)?.properties;
			if (fields)
				replacement.inputs = Object.fromEntries(
					Object.keys(fields).map((name) => [
						name,
						replacement.inputs[name] ??
							(name === 'task'
								? workflowInputRef(value)
								: { source: 'node', node: '', pointer: '' })
					])
				);
		}
		if (op === 'plan')
			Object.assign(replacement, {
				planning: {
					initial: true,
					on_events: true,
					catalog_scope: 'project',
					output_port: 'candidate'
				},
				output_schema: capabilitySchema(op),
				inputs: { ...replacement.inputs, goal: workflowInputRef(value) }
			});
		if (op === 'review') {
			const planner = value.nodes.find((n) => n.op === 'plan');
			Object.assign(replacement, {
				output_schema: capabilitySchema(op),
				review: { max_repairs: planner ? 1 : 0, ...(planner ? { repair_node: planner.id } : {}) },
				inputs: {
					...replacement.inputs,
					goal: workflowInputRef(value),
					...(planner
						? {
								candidate: {
									source: 'output',
									port: planner.planning?.output_port ?? 'candidate',
									pointer: ''
								}
							}
						: {})
				}
			});
		}
		if (op === 'route') replacement.cases = ['accept', 'revise'];
		if (op === 'dispatch') {
			replacement.dispatch = { targets: [] };
			replacement.output_schema = {
				type: 'object',
				properties: {
					selected_nodes: {
						type: 'array',
						items: { type: 'string' },
						description: 'IDs of the declared execution branches selected for this attempt.'
					}
				},
				required: ['selected_nodes']
			};
		}
		if (op === 'join') replacement.policy = 'all_terminal';
		if (op === 'map') replacement.items = { source: 'input', pointer: '/task' };
		if (op === 'loop')
			Object.assign(replacement, {
				max_iterations: 3,
				stop_when: { pointer: '/accepted', equals: true },
				output_schema: {
					type: 'object',
					properties: { accepted: { type: 'boolean' }, report: { type: 'string' } },
					required: ['accepted', 'report']
				}
			});
		if (op === 'checkpoint') replacement.question = 'What should happen next?';
		value = { ...value, nodes: value.nodes.map((n) => (n.id === selected ? replacement : n)) };
		if (outputNode(value) === selected) selectOutput(selected);
		else touch();
	}
	function toggleRaw() {
		raw = !raw;
		source = JSON.stringify(value, null, 2);
		sourceBase = source;
		sourceEdited = false;
		error = '';
	}
	function applySource() {
		if (readOnly) return;
		try {
			if (JSON.stringify(value, null, 2) !== sourceBase)
				throw new Error(
					'Settings changed while editing. Reopen Definition to load the latest settings before applying.'
				);
			const parsed = JSON.parse(source);
			if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges))
				throw new Error('Use nodes and edges arrays.');
			value = synchronizeGraph(parsed);
			raw = view === 'definition';
			sourceEdited = false;
			error = '';
		} catch (e) {
			error = String(e);
		}
	}
</script>

<div class="graph-editor min-w-0 space-y-4 text-sm">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<div class="flex gap-2">
			{#if !raw && !readOnly}<button
					type="button"
					class="rounded-lg border px-3 py-1.5 text-xs"
					on:click={add}>Add node</button
				>{/if}{#if view === 'both'}<button
					type="button"
					class="rounded-lg px-3 py-1.5 text-xs text-gray-500"
					on:click={toggleRaw}>{raw ? 'Visual editor' : 'Definition'}</button
				>{/if}
		</div>
		{#if !raw && !value.nodes.some((n) => n.op === 'output')}<label
				class="flex items-center gap-2 text-xs text-gray-500"
				>Final output<select
					aria-label="Graph result node"
					disabled={readOnly}
					value={outputNode(value)}
					on:change={(e) => selectOutput(e.currentTarget.value)}
					class="max-w-40 rounded-lg border bg-transparent p-1.5"
					>{#each value.nodes as item}<option value={item.id}>{item.label ?? item.id}</option
						>{/each}</select
				></label
			>{/if}
		{#if !raw && value.schema_version === '2.0'}<label
				class="flex items-center gap-2 text-xs text-gray-500"
			>
				Chat delivery
				<select
					aria-label="Chat delivery port"
					disabled={readOnly}
					value={value.delivery_port ?? (value.outputs?.answer ? 'answer' : '')}
					on:change={(event) => {
						if (event.currentTarget.value) value.delivery_port = event.currentTarget.value;
						else delete value.delivery_port;
						touch();
					}}
					class="max-w-40 rounded-lg border bg-transparent p-1.5"
				>
					<option value="">Module outputs only</option>
					{#each Object.keys(value.nodes.find((n) => n.op === 'output')?.inputs ?? value.outputs ?? {}) as name}<option
							value={name}>{name}</option
						>{/each}
				</select>
			</label>{/if}
	</div>
	{#if raw}
		<textarea
			aria-label="Workflow graph definition"
			readonly={readOnly}
			bind:value={source}
			on:input={() => (sourceEdited = true)}
			rows="16"
			spellcheck="false"
			class="w-full rounded-xl border bg-transparent p-3 font-mono text-xs leading-5"
		></textarea>
		{#if !readOnly}<button
				type="button"
				on:click={applySource}
				class="rounded-lg border px-3 py-2 text-xs">Apply definition</button
			>{/if}
	{:else}
		<div class="grid min-w-0 gap-4 md:grid-cols-[10rem_minmax(0,1fr)]">
			<nav
				aria-label="Plan tasks"
				class="flex gap-1 overflow-x-auto md:max-h-80 md:flex-col md:overflow-y-auto"
			>
				{#each value.nodes as item}<button
						type="button"
						class="shrink-0 rounded-lg px-3 py-2 text-left text-xs hover:bg-gray-100 dark:hover:bg-gray-800"
						class:bg-gray-100={selected === item.id}
						on:click={() => (selected = item.id)}
						>{item.label ?? item.id}<span class="mt-1 block text-[10px] text-gray-500"
							>{nodeOperations[item.op]}{#if states[item.id]}
								· {states[item.id].status}{/if}</span
						></button
					>{/each}
			</nav>
			{#if node}<div class="min-w-0 space-y-3">
					{#if locked}<p
							role="status"
							class="rounded-lg bg-gray-50 p-2 text-xs text-gray-500 dark:bg-gray-900"
						>
							{readOnly
								? 'This graph is compiled from the template settings and is read-only.'
								: 'This invocation is frozen. Edit a pending task or add a follow-up.'}
						</p>{/if}
					<div class="grid grid-cols-2 gap-3">
						<label class="text-xs text-gray-500"
							>Node title<input
								aria-label="Task label"
								disabled={locked}
								bind:value={node.label}
								on:input={touch}
								class="mt-1 w-full rounded-lg border bg-transparent p-2 text-sm text-gray-900 dark:text-white"
							/></label
						><label class="text-xs text-gray-500"
							>Action (Operation)<select
								aria-label="Task operation"
								disabled={locked || boundaryNode(node)}
								value={node.op}
								on:change={(e) => changeOperation(e.currentTarget.value as GraphNode['op'])}
								class="mt-1 w-full rounded-lg border bg-transparent p-2 text-sm text-gray-900 dark:text-white"
								>{#each Object.entries(nodeOperations).filter( ([op]) => (boundaryNode(node!) ? op === node!.op : !['input', 'output'].includes(op) && (value.schema_version === '2.0' || !['plan', 'review'].includes(op))) ) as [op, label]}<option
										value={op}>{label}</option
									>{/each}</select
							></label
						>
					</div>
					<div class="rounded-lg bg-violet-50/40 px-3 py-2 text-xs dark:bg-violet-950/20">
						<p class="font-medium">{actionFor(node)?.summary}</p>
						<p class="mt-1 text-[11px] text-gray-500">{actionFor(node)?.when_to_use}</p>
						<details class="mt-2 text-[11px] text-gray-500">
							<summary class="cursor-pointer"
								>Action behavior · {actionFor(node)?.executor === 'agent'
									? 'Agent call'
									: actionFor(node)?.executor === 'human'
										? 'Human input'
										: 'Host code'}</summary
							>
							<p class="mt-2"><strong>Receives:</strong> {actionFor(node)?.input_contract}</p>
							<p class="mt-1"><strong>Returns:</strong> {actionFor(node)?.output_contract}</p>
							<p class="mt-1"><strong>Control:</strong> {actionFor(node)?.control_effect}</p>
							<p class="mt-1"><strong>Context:</strong> {actionFor(node)?.context_rule}</p>
						</details>
					</div>
					<fieldset
						disabled={locked}
						class="rounded-xl border border-gray-100 p-3 dark:border-gray-800"
					>
						<legend class="px-1 text-xs font-medium">1. Dependencies</legend>
						{#if node.op === 'input'}<p class="text-xs text-gray-500">
								Starts from the chat task. No predecessor is required.
							</p>
						{:else}
							<p class="mb-2 text-[11px] leading-5 text-gray-500">
								Choose what must finish first. This sets order; bind the required fields in Inputs
								below.
							</p>
							<div class="flex flex-wrap gap-2">
								{#each dependencyChoices(value, node.id) as parent}
									{@const linked = Object.entries(node.inputs)
										.filter(([, ref]) => referenceNodes(ref, value).includes(parent.id))
										.map(([name]) => name)}
									<label
										class="flex items-center gap-1.5 rounded-lg border border-gray-100 px-2 py-1.5 text-xs dark:border-gray-800"
										><input
											type="checkbox"
											aria-label={`Depends on ${parent.label ?? parent.id}`}
											disabled={locked || Boolean(linked.length)}
											checked={value.edges.some(
												(e) => e.on !== 'revise' && e.from === parent.id && e.to === node!.id
											)}
											on:change={(e) => dependency(parent.id, e.currentTarget.checked)}
										/>{parent.label ?? parent.id}{#if linked.length}<span
												class="text-[10px] text-violet-600">Input: {linked.join(', ')}</span
											>{/if}</label
									>
								{/each}
							</div>
							<p class="mt-2 text-[11px] text-gray-500">
								Field bindings add their required dependencies automatically. Change the binding to
								remove them.
							</p>
						{/if}
						{#if node.op === 'join'}<label class="block text-xs text-gray-500"
								>Wait for<select
									aria-label="Join policy"
									disabled={locked}
									bind:value={node.policy}
									on:change={touch}
									class="ml-2 rounded-lg border bg-transparent p-2"
									><option value="all_terminal">All outcomes</option><option value="all_success"
										>All successful results</option
									><option value="first_success">First successful result</option><option
										value="quorum">A number of successful results</option
									></select
								></label
							>{#if node.policy === 'quorum'}<input
									aria-label="Join quorum"
									disabled={locked}
									type="number"
									min="1"
									bind:value={node.quorum}
									on:input={touch}
									class="w-20 rounded border bg-transparent p-2"
								/>{/if}{/if}
					</fieldset>
					{#if view !== 'editor' && !boundaryNode(node)}<label
							class="flex items-center gap-2 text-xs text-gray-500"
						>
							<input
								type="checkbox"
								aria-label="Hold task until a plan update"
								disabled={locked}
								checked={node.enabled === false}
								on:change={(e) => {
									if (node) node.enabled = !e.currentTarget.checked;
									touch();
								}}
							/>
							Hold until a plan update
						</label>{/if}
					<fieldset
						disabled={locked}
						class="node-inputs space-y-3 rounded-xl border border-gray-100 p-3 dark:border-gray-800"
					>
						<legend class="px-1 text-xs font-medium">2. Inputs · field bindings</legend>
						<p class="text-[11px] leading-5 text-gray-500">
							Connect a source node and one of its output fields. The input name is how this node
							refers to that value; it does not create another task.
						</p>
						{#each Object.entries(node.inputs) as [key, ref] (key)}
							<div
								class="space-y-2 border-b border-gray-100 pb-3 last:border-0 dark:border-gray-800"
							>
								<span class="text-[11px] font-medium text-gray-500">Input name in this node</span>
								<div class="flex items-center gap-2">
									<input
										aria-label={`Input name ${key}`}
										title="Name used inside this node, such as question, assignment or reference_report"
										placeholder="Input name in this node"
										required
										pattern="[A-Za-z_][A-Za-z0-9_]*"
										value={key}
										disabled={locked}
										on:change={(e) => renameInput(key, e.currentTarget.value)}
										class="min-w-0 flex-1 rounded-lg border bg-transparent p-1.5 text-xs"
									/>
									<label class="flex items-center gap-1 text-[11px]"
										><input
											type="checkbox"
											aria-label={`Optional input ${key}`}
											disabled={boundaryNode(node)}
											checked={ref.optional ?? false}
											on:change={(e) => {
												if (node) node.inputs[key] = { ...ref, optional: e.currentTarget.checked };
												touch();
											}}
										/> Allow missing (Optional)</label
									>
									<button
										type="button"
										aria-label={`Remove input ${key}`}
										disabled={locked}
										on:click={() => {
											if (node)
												node.inputs = Object.fromEntries(
													Object.entries(node.inputs).filter(([k]) => k !== key)
												);
											touch();
										}}>×</button
									>
								</div>
								<p class="text-[11px] leading-5 text-gray-500">
									{ref.optional
										? 'Optional: if this value is unavailable, pass a status notice and continue. Explicit dependencies still apply.'
										: 'Required: this node needs this value before it can complete.'}
								</p>
								<ReferenceEditor
									bind:value={node.inputs[key]}
									graph={value}
									name={`Input ${key}`}
									excludeNode={node.id}
									disabled={locked}
									on:change={touch}
								/>
							</div>
						{/each}
						<button type="button" class="text-xs text-gray-500" on:click={addInput}
							>+ Connect an input</button
						>
					</fieldset>
					{#if node}
						<NodeOutputEditor
							bind:node
							graph={value}
							disabled={locked || node.op === 'workflow'}
							on:change={() => {
								value.nodes = value.nodes.map((n) => (n.id === node!.id ? node! : n));
								touch();
							}}
						/>
					{/if}
					{#if ['call', 'route', 'map', 'loop', 'plan', 'review', 'dispatch', 'workflow'].includes(node.op)}
						<ModulePicker
							modules={modules.filter(
								(m) => node!.op !== 'workflow' || m.module_type === 'workflow'
							)}
							label="Task module"
							bind:value={node.binding}
							disabled={locked}
							on:change={bindModule}
						/>
						{#each moduleIssues as issue}<p class="text-xs text-red-600" role="alert">
								{issue}
							</p>{/each}
						{#if node.op !== 'workflow'}<label class="block text-xs text-gray-500"
								>Responsibilities<textarea
									aria-label="Task instructions"
									disabled={locked}
									bind:value={node.instruction}
									on:input={touch}
									rows="4"
									class="mt-1 w-full rounded-lg border bg-transparent p-2 text-sm leading-6 text-gray-900 dark:text-white"
								></textarea></label
							>{:else}<p class="text-xs leading-5 text-gray-500">
								The saved Workflow defines its internal responsibilities. Connect public inputs
								here; its Project role bindings supply the agents.
							</p>{/if}
					{/if}
					{#if ['call', 'route', 'map', 'loop', 'plan', 'review', 'dispatch', 'workflow'].includes(node.op)}
						<label class="block text-xs text-gray-500"
							>Private context key<input
								aria-label="Node context key"
								disabled={locked}
								value={node.context_key ?? node.id}
								on:input={(e) => {
									if (node) node.context_key = e.currentTarget.value;
									touch();
								}}
								class="mt-1 w-full rounded-lg border bg-transparent p-2 text-sm"
							/></label
						>
						<p class="text-[11px] leading-5 text-gray-500">
							Reuse a key for the same module across tasks and chat turns. Different keys keep
							private dialogue separate. Project bindings supply the framework, provider, model and
							preset.
						</p>
					{/if}
					<details class="rounded-xl border border-gray-100 p-3 text-xs dark:border-gray-800">
						<summary class="cursor-pointer font-medium">Types & environment</summary>
						<p class="my-3 leading-5 text-gray-500">
							Source types are inferred automatically. An explicit contract rejects incompatible
							connections and invalid values before calling an Agent.
						</p>
						<textarea
							aria-label="Node input type contract"
							disabled={locked || node.op === 'workflow'}
							rows="5"
							class="w-full rounded-lg border bg-transparent p-2 font-mono text-xs"
							value={JSON.stringify(node.input_schema ?? {}, null, 2)}
							on:change={(e) => setInputContract(e.currentTarget.value)}
						></textarea>
						<button
							type="button"
							disabled={locked || node.op === 'workflow'}
							class="my-2 text-blue-600"
							on:click={() => {
								if (node) {
									delete node.input_schema;
									delete node.input_schema_inferred;
									touch();
								}
							}}>Use inferred input types</button
						>
						{#if node.binding}<div class="mt-3 grid grid-cols-2 gap-3">
								<label
									>Tools<select
										aria-label="Module tools policy"
										disabled={locked}
										class="mt-1 w-full rounded-lg border bg-transparent p-2"
										value={node.environment?.tools ?? 'inherit'}
										on:change={(e) => {
											if (node)
												node.environment = {
													...node.environment,
													tools: e.currentTarget.value as 'inherit' | 'none'
												};
											touch();
										}}
										><option value="inherit">Inherit adapter policy</option><option value="none"
											>No tools</option
										></select
									></label
								>
								<label
									>Isolation<select
										aria-label="Module isolation policy"
										disabled={locked}
										class="mt-1 w-full rounded-lg border bg-transparent p-2"
										value={node.environment?.isolation ?? 'logical'}
										on:change={(e) => {
											if (node)
												node.environment = {
													...node.environment,
													isolation: e.currentTarget.value as 'logical' | 'process' | 'sandbox'
												};
											touch();
										}}
										><option value="logical">Private conversation</option><option value="process"
											>Separate process required</option
										><option value="sandbox">OS sandbox required</option></select
									></label
								>
							</div>
							<p class="mt-3 leading-5 text-gray-500">
								Child modules cannot gain permissions. Unsupported restrictions stop the call.
								Hermes owns its remote environment; a private conversation is not an OS sandbox.
								Actual permissions are recorded per invocation.
							</p>{/if}
					</details>
					{#if ['command', 'evaluate', 'integrate', 'publish'].includes(node.op)}
						<fieldset class="rounded-lg border p-3 space-y-3">
							<legend class="text-xs font-medium px-1">Workspace operation</legend>
							{#if node.op !== 'evaluate'}<label class="block text-xs"
									>Declared workspace instance<input
										aria-label="Workspace instance"
										disabled={locked}
										value={node.workspace?.instance ?? ''}
										placeholder="Module context key or integration instance"
										on:input={(e) => {
											if (node) {
												node.workspace = { ...node.workspace, instance: e.currentTarget.value };
												touch();
											}
										}}
										class="mt-1 block w-full rounded border bg-transparent p-2"
									/></label
								>{/if}
							{#if ['command', 'evaluate'].includes(node.op)}<label class="block text-xs"
									>Approved command IDs<input
										aria-label="Workspace command IDs"
										disabled={locked}
										value={node.workspace?.command_ids?.join(', ') ?? ''}
										placeholder="tests"
										on:input={(e) => {
											if (node) {
												node.workspace = {
													...node.workspace,
													command_ids: e.currentTarget.value
														.split(',')
														.map((v) => v.trim())
														.filter(Boolean)
												};
												touch();
											}
										}}
										class="mt-1 block w-full rounded border bg-transparent p-2"
									/></label
								>{/if}
							{#if node.op === 'integrate'}<label class="block text-xs"
									>Integration strategy<select
										aria-label="Artifact integration strategy"
										disabled={locked}
										value={node.workspace?.strategy ?? 'select'}
										on:change={(e) => {
											if (node) {
												node.workspace = {
													...node.workspace,
													strategy: e.currentTarget.value as 'select' | 'merge'
												};
												touch();
											}
										}}
										class="mt-1 block w-full rounded border bg-transparent p-2"
										><option value="select">Select one alternative</option><option value="merge"
											>Merge explicitly selected changes</option
										></select
									></label
								>{/if}
							<p class="text-xs text-gray-500">
								Choose artifact fields through declared inputs. Paths, permissions and executable
								commands come from the Project configuration.
							</p>
						</fieldset>
					{/if}
					{#if node.op === 'plan'}
						<label class="block text-xs text-gray-500"
							>Publish graph output<input
								aria-label="Planning output port"
								disabled={locked}
								value={node.planning?.output_port ?? 'candidate'}
								on:input={(e) => {
									if (node)
										node.planning = { ...node.planning, output_port: e.currentTarget.value };
									touch();
								}}
								class="mt-1 w-full rounded-lg border bg-transparent p-2 text-sm"
							/></label
						>
						<div class="flex flex-wrap gap-4 text-xs">
							<label class="flex items-center gap-2"
								><input
									type="checkbox"
									aria-label="Plan on start"
									disabled={locked}
									checked={node.planning?.initial ?? true}
									on:change={(e) => {
										if (node)
											node.planning = { ...node.planning, initial: e.currentTarget.checked };
										touch();
									}}
								/> On start</label
							>
							<label class="flex items-center gap-2"
								><input
									type="checkbox"
									aria-label="Plan on incoming results"
									disabled={locked}
									checked={node.planning?.on_events ?? true}
									on:change={(e) => {
										if (node)
											node.planning = { ...node.planning, on_events: e.currentTarget.checked };
										touch();
									}}
								/> On actionable results</label
							>
						</div>
						<p class="text-[11px] leading-5 text-gray-500">
							The host advances dependencies and waits automatically. Controller observations are
							for failures, unresolved or held work, and explicitly observed nodes.
						</p>
					{:else if node.op === 'review'}
						<div class="grid grid-cols-2 gap-3">
							<label class="text-xs text-gray-500"
								>Rejection target<select
									aria-label="Review repair node"
									disabled={locked}
									value={node.review?.repair_node ?? ''}
									on:change={(e) => {
										if (node)
											node.review = e.currentTarget.value
												? {
														...node.review,
														repair_node: e.currentTarget.value,
														max_repairs: node.review?.max_repairs || 1
													}
												: { max_repairs: 0 };
										touch();
									}}
									class="mt-1 w-full rounded-lg border bg-transparent p-2"
									><option value="">Stop and retain reports</option
									>{#each feedbackTargetChoices(value, node.id) as planner}<option
											value={planner.id}>{planner.label ?? planner.id}</option
										>{/each}</select
								></label
							>
							<label class="text-xs text-gray-500"
								>Correction limit<input
									aria-label="Review correction limit"
									disabled={locked || !node.review?.repair_node}
									type="number"
									min="0"
									max="8"
									value={node.review?.max_repairs ?? 0}
									on:input={(e) => {
										if (node)
											node.review = { ...node.review, max_repairs: Number(e.currentTarget.value) };
										touch();
									}}
									class="mt-1 w-full rounded-lg border bg-transparent p-2"
								/></label
							>
						</div>
						<p class="text-xs leading-5 text-gray-500">
							On rejection, return issues to the selected upstream node. Its Action determines
							whether to replay work, select branches or revise the graph. The limit counts extra
							correction attempts after the initial execution. At the limit, retain reports and stop
							without delivering an unapproved answer.
						</p>
					{/if}
					{#if node.op === 'dispatch'}
						<fieldset class="rounded-lg border p-3 text-xs">
							<legend>Execution branches</legend>
							<p class="mb-2 text-gray-500">
								Select downstream work nodes this dispatcher may activate. It returns
								selected_nodes; during correction, unselected completed branches keep their
								assignments and reports.
							</p>
							{#each value.nodes.filter((n) => n.id !== node!.id && ['call', 'route', 'map', 'loop', 'dispatch'].includes(n.op)) as target}
								<label class="mr-4 inline-flex items-center gap-2"
									><input
										type="checkbox"
										aria-label={`Dispatch target ${target.label ?? target.id}`}
										disabled={locked}
										checked={node.dispatch?.targets.includes(target.id)}
										on:change={(e) => {
											if (node) {
												node.dispatch = {
													targets: e.currentTarget.checked
														? [...(node.dispatch?.targets ?? []), target.id]
														: (node.dispatch?.targets ?? []).filter((id) => id !== target.id)
												};
												touch();
											}
										}}
									/>{target.label ?? target.id}</label
								>
							{/each}
						</fieldset>
					{/if}
					{#if node.op === 'checkpoint'}<input
							aria-label="Checkpoint question"
							disabled={locked}
							bind:value={node.question}
							on:input={touch}
							class="w-full rounded-lg border bg-transparent p-2"
						/>{/if}
					<div class="flex items-center justify-between">
						<span class="font-mono text-[10px] text-gray-400">{node.id}</span><button
							type="button"
							disabled={locked || boundaryNode(node) || value.nodes.length === 1}
							on:click={remove}
							class="text-xs text-red-600 disabled:opacity-30">Remove node</button
						>
					</div>
					{#if ['map', 'route', 'loop'].includes(node.op)}<p
							class="text-xs leading-5 text-gray-500"
						>
							Use {view === 'editor' ? 'Advanced' : 'Definition'} to configure item references, case edges,
							output fields and loop conditions.
						</p>{/if}
				</div>{/if}
		</div>
	{/if}
	{#if error}<p role="alert" class="text-xs text-red-600">{error}</p>{/if}
	{#if !raw && value.schema_version === '2.0'}
		<details class="border-t border-gray-100 pt-3 dark:border-gray-800">
			<summary class="cursor-pointer text-xs font-medium">Advanced output aliases</summary>
			<div class="space-y-4 pt-3">
				{#each Object.keys(value.outputs ?? {}) as port}<div>
						<strong class="mb-2 block text-xs"
							>{port}{port === 'answer' ? ' · delivered to chat' : ''}</strong
						><ReferenceEditor
							bind:value={value.outputs![port]}
							graph={value}
							name={`Output ${port}`}
							disabled={readOnly}
							on:change={touch}
						/>
					</div>{/each}
			</div>
		</details>
	{/if}
</div>
