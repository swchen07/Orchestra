<script lang="ts">
	import { createEventDispatcher, afterUpdate } from 'svelte';
	import { writable } from 'svelte/store';
	import { SvelteFlow, Background, type Node, type Edge } from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import { theme } from '$lib/stores';
	import type { WorkflowRun } from '$lib/apis/execution';
	import { executionMap, GRAPH_COLUMN } from './workflowPresentation';
	import RunStepNode from './RunStepNode.svelte';
	import ExecutionEdge from './ExecutionEdge.svelte';
	export let run: WorkflowRun;
	export let cycle = '';
	export let now = Date.now();
	const dispatch = createEventDispatcher<{ select: string }>();
	const nodes = writable<Node[]>([]),
		edges = writable<Edge[]>([]);
	const nodeTypes = { runStep: RunStepNode },
		edgeTypes = { execution: ExecutionEdge };
	let scroller: HTMLDivElement;
	let focused = '';
	let dataLinks = false;
	$: graph = executionMap(run, cycle, now, dataLinks);
	$: nodes.set(graph.nodes);
	$: edges.set(graph.edges);
	afterUpdate(() => {
		const key = run.id + ':' + cycle;
		if (scroller && focused !== key) {
			focused = key;
			scroller.scrollTo({
				left: Math.max(0, graph.focusIndex * GRAPH_COLUMN - 48),
				behavior: 'smooth'
			});
		}
	});
</script>

<div class="map-scroller" bind:this={scroller} aria-label="Controller and task branches">
	<div class="runtime-map" style:height={`${graph.height}px`} style:width={`${graph.width}px`}>
		<SvelteFlow
			{nodes}
			{edges}
			{nodeTypes}
			{edgeTypes}
			nodesDraggable={false}
			nodesConnectable={false}
			elementsSelectable={false}
			panOnDrag={false}
			zoomOnScroll={false}
			zoomOnPinch={false}
			zoomOnDoubleClick={false}
			colorMode={$theme.includes('dark') ? 'dark' : 'light'}
			on:nodeclick={(e) => {
				if (e.detail.node.data.call_id) dispatch('select', String(e.detail.node.data.call_id));
			}}
		>
			<Background gap={20} size={0.7} patternColor="#e0e5ee" />
		</SvelteFlow>
	</div>
</div>
<div class="map-legend">
	<span>↓ Task input</span><span>↑ Result</span><label
		><input type="checkbox" bind:checked={dataLinks} /> Data links</label
	><small>Tasks return independently · Scroll horizontally to explore</small>
</div>

<style>
	.map-scroller {
		width: 100%;
		min-width: 0;
		overflow-x: auto;
		border: 1px solid var(--run-border, #e0e6ed);
		border-radius: 11px;
		background: var(--run-canvas, #fff);
		scrollbar-width: thin;
		overscroll-behavior-x: contain;
	}
	.runtime-map {
		min-width: 100%;
		background: var(--run-canvas, #fff);
	}
	.map-legend {
		display: flex;
		gap: 12px;
		flex-wrap: wrap;
		font-size: 10px;
		color: var(--run-muted, #6d7b90);
		margin-top: 10px;
	}
	.map-legend span:nth-child(2) {
		color: #16826e;
	}
	.map-legend span:nth-child(3) {
		color: #16826e;
	}
	.map-legend label {
		display: flex;
		align-items: center;
		gap: 5px;
		margin-left: auto;
		cursor: pointer;
	}
	.map-legend input {
		width: 11px;
		height: 11px;
		accent-color: #8671b0;
	}
	.map-legend small {
		flex-basis: 100%;
		font-size: 9px;
	}
</style>
