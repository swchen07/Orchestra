<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { GraphRef, WorkflowGraph } from '$lib/apis/execution';
	import { dependencyChoices, referenceNodes } from './nodeProgram';
	import { referenceFields, refForNode, fieldTypes, inputSourceLabel } from './nodePorts';
	export let value: GraphRef;
	export let graph: WorkflowGraph;
	export let name: string;
	export let excludeNode = '';
	export let disabled = false;
	const dispatch = createEventDispatcher<{ change: void }>();
	let structured = value.source === 'literal' && typeof value.value !== 'string';
	$: current = graph.nodes.find((n) => n.id === excludeNode);
	$: sources =
		current?.op === 'input'
			? []
			: excludeNode
				? dependencyChoices(graph, excludeNode)
				: graph.nodes;
	$: choice =
		value.source === 'node'
			? value.node
				? 'node:' + value.node
				: ''
			: value.source === 'output'
				? 'port:' + value.port
				: value.source;
	$: fields = referenceFields(value, graph);
	$: selectedField = fields.find((f) => f.pointer === (value.pointer ?? ''));
	$: ports = [
		...new Set([
			...Object.keys(graph.outputs ?? {}),
			...graph.nodes
				.filter((n) => n.op === 'plan')
				.map((n) => n.planning?.output_port ?? 'candidate')
		])
	].filter(
		(port) =>
			!excludeNode || !referenceNodes({ source: 'output', port }, graph).includes(excludeNode)
	);
	function change() {
		value = { ...value };
		dispatch('change');
	}
	function chooseSource(selected: string) {
		const optional = value.optional;
		if (selected.startsWith('node:')) {
			const node = graph.nodes.find((n) => n.id === selected.slice(5));
			if (!node) return;
			value = refForNode(node);
		} else if (selected.startsWith('port:')) {
			value = { source: 'output', port: selected.slice(5), pointer: '' };
		} else if (selected === 'literal') value = { source: 'literal', value: '' };
		else if (selected === 'report')
			value = { source: 'report', report_ref: '', pointer: '/report' };
		else if (selected === 'invocation') value = { source: 'invocation', pointer: '/instruction' };
		else
			value = {
				source: 'input',
				pointer: referenceFields({ source: 'input' }, graph)[0]?.pointer ?? ''
			};
		if (optional !== undefined) value.optional = optional;
		structured = false;
		change();
	}
</script>

<div class="reference-fields">
	<label
		>From node
		<select
			aria-label={`${name} source`}
			{disabled}
			required
			value={choice}
			on:change={(e) => chooseSource(e.currentTarget.value)}
		>
			<option value="" disabled>Choose a source…</option>
			{#if current?.op === 'input' || !graph.nodes.some((n) => n.op === 'input')}<option
					value="input">Public module input</option
				>{/if}
			{#if current?.op === 'input'}<option value="invocation"
					>Caller invocation · authorized fields</option
				>{/if}
			{#each sources as node}<option value={`node:${node.id}`}>{node.label ?? node.id}</option
				>{/each}
			{#if current?.op !== 'input' && ports.length}<optgroup label="Generated / named results">
					{#each ports as port}<option value={`port:${port}`}>Controller result · {port}</option
						>{/each}
				</optgroup>{/if}
			<optgroup label="Other sources">
				<option value="literal">Fixed value</option>
				{#if current?.op !== 'input'}<option value="report">Saved report</option>{/if}
			</optgroup>
		</select>
	</label>
	{#if value.source !== 'literal'}
		<label
			>Output field
			<select
				aria-label={`${name} field`}
				disabled={disabled || !choice}
				required
				value={value.pointer ?? ''}
				on:change={(e) => {
					value.pointer = e.currentTarget.value;
					change();
				}}
			>
				{#if !choice}<option value="" disabled>Choose a source first</option>{/if}
				{#each fields as field}<option value={field.pointer}
						>{field.name} · {fieldTypes[field.type] ?? field.type}</option
					>{/each}
				{#if choice && !selectedField}<option value={value.pointer ?? ''}>
						{value.pointer
							? 'Custom field · ' + value.pointer.slice(1).replaceAll('/', '.')
							: 'All output fields'}
					</option>{/if}
			</select>
		</label>
		{#if value.source === 'report'}<label class="full-width"
				>Saved report ID
				<input
					aria-label={`${name} report ID`}
					{disabled}
					required
					bind:value={value.report_ref}
					on:change={change}
				/>
			</label>{/if}
		{#if choice}<p class="binding-preview full-width">
				{inputSourceLabel(value, graph)} · {selectedField?.name ?? 'all fields'} → {name.replace(
					/^Input /,
					''
				)}
			</p>{/if}
		{#if selectedField?.description}<p class="field-meaning full-width">
				{selectedField.description}
			</p>{/if}
		{#if choice}<details class="advanced-field full-width">
				<summary>Advanced field path</summary>
				<p>
					Use only for an existing custom reference. Normal bindings use the field selector above.
				</p>
				<input
					aria-label={`${name} advanced path`}
					{disabled}
					value={value.pointer ?? ''}
					on:change={(e) => {
						value.pointer = e.currentTarget.value;
						change();
					}}
				/>
			</details>{/if}
	{:else}
		<div class="literal-value">
			<label
				>Value<input
					aria-label={`${name} value`}
					{disabled}
					value={structured ? JSON.stringify(value.value) : String(value.value ?? '')}
					on:input={(e) => {
						try {
							value.value = structured ? JSON.parse(e.currentTarget.value) : e.currentTarget.value;
							e.currentTarget.setCustomValidity('');
							change();
						} catch {
							e.currentTarget.setCustomValidity('Enter valid JSON.');
						}
					}}
				/>
			</label>
			<label class="literal-toggle"
				><input type="checkbox" {disabled} bind:checked={structured} /> Structured JSON value</label
			>
		</div>
	{/if}
</div>

<style>
	.reference-fields {
		display: grid;
		grid-template-columns: minmax(9rem, 1fr) minmax(9rem, 1fr);
		gap: 0.55rem;
		align-items: start;
	}
	label {
		display: block;
		color: #747078;
		font-size: 0.6875rem;
	}
	input:not([type='checkbox']),
	select {
		margin-top: 0.3rem;
		width: 100%;
		min-width: 0;
		border: 1px solid #80808035;
		border-radius: 0.5rem;
		padding: 0.55rem;
		background: transparent;
		color: inherit;
		font-size: 0.75rem;
	}
	.full-width {
		grid-column: 1/-1;
	}
	.binding-preview {
		margin-top: 0.2rem;
		font-size: 0.6875rem;
		color: #71618f;
	}
	.field-meaning {
		font-size: 0.6875rem;
		line-height: 1.6;
		color: #78717f;
	}
	.advanced-field {
		font-size: 0.625rem;
		color: #8c8495;
	}
	.advanced-field summary {
		cursor: pointer;
	}
	.advanced-field p {
		margin-top: 0.4rem;
		line-height: 1.6;
	}
	.literal-toggle {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		margin-top: 0.5rem;
	}
	@media (max-width: 650px) {
		.reference-fields {
			grid-template-columns: 1fr;
		}
	}
</style>
