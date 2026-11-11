<script lang="ts">
	import { BaseEdge, EdgeLabelRenderer, type EdgeProps } from '@xyflow/svelte';
	type $$Props = EdgeProps;
	export let id: string;
	export let sourceX: number;
	export let sourceY: number;
	export let targetX: number;
	export let targetY: number;
	export let markerEnd: string | undefined = undefined;
	export let data: { floor?: number; limit?: number; used?: number } = {};
	$: floor = Math.max(sourceY, targetY, data.floor ?? 0) + 75;
	$: path = `M ${sourceX} ${sourceY} C ${sourceX} ${floor}, ${targetX} ${floor}, ${targetX} ${targetY}`;
</script>

<BaseEdge
	{id}
	{path}
	{markerEnd}
	style="stroke: #b77944; stroke-width: 1.8; stroke-dasharray: 6 4;"
/>
<EdgeLabelRenderer>
	<div
		class="feedback-label nodrag nopan"
		style={`transform: translate(-50%, -50%) translate(${(sourceX + targetX) / 2}px, ${(sourceY + targetY) / 8 + floor * 0.75}px)`}
	>
		Revise · {data.used !== undefined ? `${data.used} / ` : ''}{data.limit ?? 0} corrections
	</div>
</EdgeLabelRenderer>

<style>
	.feedback-label {
		position: absolute;
		pointer-events: none;
		color: #8c572d;
		background: #fff8ee;
		border: 1px solid #e7cfb5;
		border-radius: 7px;
		padding: 4px 8px;
		font-size: 10px;
		white-space: nowrap;
	}
	:global(.dark) .feedback-label {
		color: #e7bb8d;
		background: #352a20;
		border-color: #785438;
	}
</style>
