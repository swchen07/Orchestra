<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { writable } from 'svelte/store';
	import { SvelteFlow, Background, Controls, type Node, type Edge } from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import { theme } from '$lib/stores';
	import type { WorkflowGraph, WorkflowRun } from '$lib/apis/execution';
	import { planGraph, definitionGraph } from './workflowView';
	import AgentNode from './AgentNode.svelte';
	import DefinitionNode from './DefinitionNode.svelte';
	import FeedbackEdge from './FeedbackEdge.svelte';
	export let plan: WorkflowGraph;
	export let run: WorkflowRun | undefined = undefined;
	export let planned = false;
	export let definition = false;
	export let modules: { id: string; name: string }[] = [];
	export let selected = '';
	const dispatch = createEventDispatcher<{ select: string }>();
	const nodes = writable<Node[]>([]),
		edges = writable<Edge[]>([]);
	const nodeTypes = { agent: AgentNode, definition: DefinitionNode };
	const edgeTypes = { feedback: FeedbackEdge };
	$: graph = definition ? definitionGraph(plan, modules) : planGraph(plan, run, planned);
	$: nodes.set(graph.nodes.map((node) => ({ ...node, selected: node.data.node_id === selected })));
	$: edges.set(graph.edges);
</script>

<div
	class="min-w-0 border-y border-gray-100 bg-gray-50/40 dark:border-gray-800 dark:bg-gray-950/20"
	style:height={definition ? `${Math.min(480, Math.max(310, graph.nodes.length * 46))}px` : '280px'}
	aria-label={definition ? 'Workflow structure' : 'Workflow plan overview'}
>
	{#key graph.layoutKey}<SvelteFlow
			{nodes}
			{edges}
			{nodeTypes}
			{edgeTypes}
			fitView
			fitViewOptions={{ padding: 0.07 }}
			minZoom={0.45}
			maxZoom={1.6}
			nodesDraggable={false}
			nodesConnectable={false}
			zoomOnScroll={false}
			panOnScroll={false}
			colorMode={$theme.includes('dark') ? 'dark' : 'light'}
			on:nodeclick={(e) => dispatch('select', String(e.detail.node.data.node_id ?? ''))}
			><Background /><Controls showLock={false} /></SvelteFlow
		>{/key}
</div>
