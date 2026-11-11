<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { GraphNode, WorkflowGraph } from '$lib/apis/execution';
	import { capabilitySchema, boundaryNode, consumers } from './nodeProgram';
	export let node: GraphNode;
	export let graph: WorkflowGraph | undefined = undefined;
	export let disabled = false;
	const dispatch = createEventDispatcher<{ change: void }>();
	$: capability = capabilitySchema(node.op);
	$: boundary = boundaryNode(node);
	$: targets = graph ? consumers(graph, node.id) : [];
	$: schema = (node.output_schema as Record<string, any> | undefined) ?? capability;
	$: properties = Object.entries(schema?.properties ?? {}) as [string, Record<string, any>][];
	function setSchema(value: Record<string, any> | undefined) {
		node = { ...node, output_schema: value };
		dispatch('change');
	}
	function changeField(key: string, name: string, type?: string) {
		if (!name || (key !== name && schema?.properties?.[name])) return;
		const property = type
			? {
					...schema!.properties[key],
					type,
					...(type === 'array' ? { items: { type: 'string' } } : {})
				}
			: schema!.properties[key];
		setSchema({
			...schema,
			type: 'object',
			properties: Object.fromEntries(
				properties.map(([k, v]) => (k === key ? [name, property] : [k, v]))
			),
			required: (schema?.required ?? []).map((k: string) => (k === key ? name : k))
		});
	}
	function addField() {
		let key = 'field',
			index = 2;
		while (schema?.properties?.[key]) key = 'field_' + index++;
		setSchema({
			...schema,
			type: 'object',
			properties: { ...schema?.properties, [key]: { type: 'string' } },
			required: [...(schema?.required ?? []), key]
		});
	}
	function meaning(key: string, description: string) {
		setSchema({
			...schema,
			properties: { ...schema!.properties, [key]: { ...schema!.properties[key], description } }
		});
	}
</script>

<div class="output-contract">
	<label
		>3. Outputs · what this node returns<select
			aria-label="Node output contract"
			disabled={disabled || boundary || Boolean(capability)}
			value={schema ? 'json' : 'text'}
			on:change={(e) =>
				setSchema(
					e.currentTarget.value === 'text'
						? undefined
						: { type: 'object', properties: { report: { type: 'string' } }, required: ['report'] }
				)}
		>
			<option value="text">Text report</option><option value="json"
				>{node.op === 'plan'
					? 'Graph action'
					: node.op === 'review'
						? 'Review decision and answer'
						: 'Structured fields'}</option
			>
		</select></label
	>
	{#if schema}<div class="output-fields">
			{#each properties as [key, property]}
				{@const fixed = Boolean(capability?.properties?.[key])}
				<div class="field-row">
					<input
						aria-label={`Output field ${key}`}
						disabled={disabled || fixed || boundary}
						required
						pattern="[A-Za-z_][A-Za-z0-9_]*"
						value={key}
						on:change={(e) => changeField(key, e.currentTarget.value)}
					/>
					<select
						aria-label={`Output type ${key}`}
						disabled={disabled || fixed}
						value={property.type ?? 'any'}
						on:change={(e) => changeField(key, key, e.currentTarget.value)}
						>{#each ['string', 'integer', 'number', 'boolean', 'object', 'array'] as type}<option
								value={type}>{type}</option
							>{/each}{#if !property.type}<option value="any">any value</option>{/if}</select
					>
					<label class="required"
						><input
							type="checkbox"
							aria-label={`Required output ${key}`}
							disabled={disabled || fixed || boundary}
							checked={(schema.required ?? []).includes(key)}
							on:change={(e) =>
								setSchema({
									...schema,
									required: e.currentTarget.checked
										? [...(schema.required ?? []), key]
										: (schema.required ?? []).filter((v: string) => v !== key)
								})}
						/> Required</label
					>
					<button
						type="button"
						aria-label={`Remove output ${key}`}
						disabled={disabled || fixed || boundary}
						on:click={() =>
							setSchema({
								...schema,
								properties: Object.fromEntries(properties.filter(([k]) => k !== key)),
								required: (schema.required ?? []).filter((v: string) => v !== key)
							})}>×</button
					>
				</div>
				<label class="field-meaning"
					>Meaning of {key}
					<textarea
						aria-label={`Meaning of output ${key}`}
						{disabled}
						rows="2"
						maxlength="2000"
						placeholder="Explain what this field must contain, including units or constraints."
						value={property.description ?? ''}
						on:input={(e) => meaning(key, e.currentTarget.value)}
					></textarea>
				</label>
			{/each}
			{#if !boundary && !['join', 'finish', 'checkpoint'].includes(node.op)}<button
					type="button"
					class="add-field"
					{disabled}
					on:click={addField}>+ Add output field</button
				>{/if}
		</div>{/if}
	{#if !schema}<label class="field-meaning"
			>Expected reply · report
			<textarea
				aria-label="Report meaning"
				{disabled}
				rows="2"
				maxlength="4000"
				placeholder="Describe the report the Agent should return."
				value={node.report_description ?? ''}
				on:input={(e) => {
					node = { ...node, report_description: e.currentTarget.value };
					dispatch('change');
				}}
			></textarea>
		</label>{/if}
	<p>
		{boundary || ['join', 'finish', 'checkpoint'].includes(node.op)
			? 'This host action transfers data. Field meanings accompany the data when an Agent reads it.'
			: 'Field meanings are sent to the Agent with its output instructions. Required names and types are checked after it replies.'}
	</p>
	{#if boundary}<p>
			These fields pass through the input bindings above. Add or rename fields there. This node runs
			in the host without a model call.
		</p>{/if}
	<p>
		{node.op === 'plan'
			? 'The host validates graph actions and protects started work.'
			: node.op === 'review'
				? 'The host validates the verdict and report references. Only an approved answer is delivered.'
				: 'Downstream nodes can reference these fields directly. Invalid structured responses fail the node.'}
	</p>
	<div class="destinations">
		<strong>Used by</strong>
		{#each targets as target}<div>
				<span class="field-name">{target.pointer.replace(/^\//, '') || 'whole output'}</span> → {target.label}.{target.input}
			</div>
		{:else}<div>
				{node.op === 'output' ? 'answer → Chat response' : 'No downstream input is bound yet.'}
			</div>{/each}
	</div>
</div>

<style>
	.output-contract label {
		display: block;
		color: #808080;
		font-size: 0.75rem;
	}
	input:not([type='checkbox']),
	select,
	textarea {
		min-width: 0;
		width: 100%;
		border: 1px solid #80808035;
		border-radius: 0.5rem;
		padding: 0.5rem;
		background: transparent;
		font-size: 0.75rem;
	}
	.field-meaning {
		margin-top: 0.4rem;
	}
	textarea {
		margin-top: 0.35rem;
		resize: vertical;
		line-height: 1.6;
		color: inherit;
	}
	.output-contract > label > select {
		margin-top: 0.4rem;
	}
	.output-fields {
		margin-top: 0.75rem;
		display: grid;
		gap: 0.5rem;
	}
	.field-row {
		display: grid;
		grid-template-columns: minmax(5rem, 1fr) minmax(5rem, 0.8fr) auto 1.5rem;
		align-items: center;
		gap: 0.5rem;
	}
	.field-row .required {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.6875rem;
	}
	button {
		font-size: 0.75rem;
		color: #808080;
	}
	button:disabled {
		opacity: 0.4;
	}
	.add-field {
		justify-self: start;
		margin-top: 0.25rem;
	}
	p {
		color: #808080;
		font-size: 0.6875rem;
		line-height: 1.6;
		margin-top: 0.5rem;
	}
	.destinations {
		margin-top: 0.75rem;
		padding: 0.65rem 0.8rem;
		border-radius: 0.5rem;
		background: #8d75b90a;
		font-size: 0.6875rem;
		line-height: 1.8;
		color: #766a85;
	}
	.destinations strong {
		display: block;
		font-weight: 500;
	}
	.field-name {
		font-family: ui-monospace, monospace;
	}
</style>
