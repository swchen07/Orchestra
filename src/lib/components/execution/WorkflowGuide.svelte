<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { Workflow } from '$lib/apis/execution';
	import { actionFor, hasPlanning } from './nodeProgram';
	import { outputFields, referenceFields, inputSourceLabel } from './nodePorts';
	export let template: Workflow;
	const dispatch = createEventDispatcher<{ select: string }>();
	let copiedTask = '';
	$: graph = template.graph;
	async function copyTask() {
		if (template.example) {
			await navigator.clipboard.writeText(template.example.task);
			copiedTask = template.example.task;
		}
	}
</script>

<details class="workflow-guide">
	<summary>Example &amp; build guide</summary>
	<div class="guide-content">
		{#if template.example}
			<p class="purpose">{template.example.purpose}</p>
			<div class="sample-heading">
				<strong>Sample task</strong><button type="button" on:click={copyTask}
					>{copiedTask === template.example.task ? 'Copied' : 'Copy task'}</button
				>
			</div>
			<p class="sample-task">{template.example.task}</p>
			<p class="expected"><strong>Expected behavior:</strong> {template.example.expected}</p>
			<p class="hint">
				This is a learning example. Paste the sample task into a Project chat to try it; editing
				this text does not start a run.
			</p>
		{/if}
		{#if !template.example}<button
				type="button"
				on:click={() => (template.example = { purpose: '', task: '', expected: '' })}
				>+ Add example text</button
			>{/if}
		{#if graph}
			<strong class="section-label">How this definition connects</strong>
			{#if hasPlanning(graph)}<p class="hint">
					The table shows the saved input, planning and review steps. Executor steps and their
					connections are generated at runtime from the Project module pool.
				</p>{/if}
			<div class="table-scroll">
				<table>
					<thead><tr><th>Node</th><th>Action / module</th><th>Receives → returns</th></tr></thead>
					<tbody
						>{#each graph.nodes as node}<tr>
								<td
									><button
										type="button"
										aria-label={`Configure example node ${node.label ?? node.id}`}
										on:click={() => dispatch('select', node.id)}>{node.label ?? node.id}</button
									></td
								>
								<td
									>{actionFor(node)?.label}<span class="module-name"
										>{template.roles?.find((r) => r.id === node.binding)?.name ??
											(node.binding ? 'Project module' : 'Host')}</span
									></td
								>
								<td
									>{#each Object.entries(node.inputs) as [name, ref]}<div>
											{inputSourceLabel(ref, graph)} · {referenceFields(ref, graph).find(
												(f) => f.pointer === (ref.pointer ?? '')
											)?.name ?? 'result'} → <code>{name}</code>
										</div>{/each}
									<div class="returns">
										Returns: {outputFields(node)
											.map((f) => f.name)
											.join(', ') || 'declared result'}
									</div>
								</td>
							</tr>{/each}</tbody
					>
				</table>
			</div>
		{/if}
		<strong class="section-label">Make your own</strong>
		<ol>
			<li>Duplicate this template, or choose one of the three examples in New template.</li>
			<li>
				Name the workflow and its module binding slots. Bind actual Agent Teams in your Project.
			</li>
			<li>
				Select a node and choose its Action. Read the action’s behavior and private-context rule.
			</li>
			<li>
				Connect inputs by selecting a source node and an output field. Add an order-only dependency
				only when needed.
			</li>
			<li>
				Describe the expected output fields and the node’s responsibilities. Select the value that
				Workflow output delivers.
			</li>
			<li>Save, bind Project participants, and select this workflow in a Project chat.</li>
		</ol>
		{#if template.example}<details class="edit-example">
				<summary>Edit this example text</summary>
				<label
					>Purpose<textarea
						aria-label="Example purpose"
						bind:value={template.example.purpose}
						rows="2"
						maxlength="1000"
					></textarea></label
				>
				<label
					>Sample task<textarea
						aria-label="Example task"
						bind:value={template.example.task}
						rows="4"
						maxlength="12000"
					></textarea></label
				>
				<label
					>Expected behavior<textarea
						aria-label="Example expected behavior"
						bind:value={template.example.expected}
						rows="3"
						maxlength="4000"
					></textarea></label
				>
			</details>{/if}
	</div>
</details>

<style>
	.workflow-guide {
		margin-top: 1rem;
		border: 1px solid #8b77af2b;
		border-radius: 0.75rem;
		background: #8b77af04;
	}
	.workflow-guide > summary {
		padding: 0.8rem 1rem;
		font-size: 0.8125rem;
		font-weight: 500;
		cursor: pointer;
	}
	.guide-content {
		padding: 0 1rem 1rem;
		font-size: 0.75rem;
		line-height: 1.7;
	}
	.purpose {
		color: #61516f;
	}
	.sample-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-top: 0.9rem;
	}
	button {
		color: #75608e;
		font-size: 0.75rem;
		text-align: left;
	}
	.sample-task {
		white-space: pre-wrap;
		padding: 0.8rem;
		background: #8b77af0b;
		border-radius: 0.5rem;
		margin: 0.4rem 0;
	}
	.expected {
		margin-top: 0.6rem;
	}
	.hint {
		font-size: 0.6875rem;
		color: #827888;
		margin-top: 0.4rem;
	}
	.section-label {
		display: block;
		margin-top: 1rem;
		font-size: 0.75rem;
		font-weight: 500;
	}
	.table-scroll {
		overflow-x: auto;
		margin-top: 0.5rem;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		min-width: 34rem;
		text-align: left;
		font-size: 0.6875rem;
	}
	th,
	td {
		padding: 0.6rem;
		border-bottom: 1px solid #80808020;
		vertical-align: top;
	}
	th {
		color: #877d8e;
		font-weight: 500;
	}
	.module-name {
		display: block;
		color: #8a828e;
	}
	.returns {
		margin-top: 0.3rem;
		color: #756486;
	}
	ol {
		list-style: decimal;
		padding-left: 1.2rem;
		margin-top: 0.5rem;
	}
	li {
		margin: 0.3rem 0;
	}
	.edit-example {
		margin-top: 1rem;
		color: #827888;
		font-size: 0.6875rem;
	}
	.edit-example summary {
		cursor: pointer;
	}
	label {
		display: block;
		margin-top: 0.7rem;
	}
	textarea {
		display: block;
		width: 100%;
		border: 1px solid #80808035;
		border-radius: 0.5rem;
		padding: 0.5rem;
		background: transparent;
		margin-top: 0.3rem;
		font-size: 0.75rem;
		color: inherit;
	}
	:global(.dark) .purpose {
		color: #c6b8d2;
	}
</style>
