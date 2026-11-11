<script lang="ts">
	import { BaseEdge, type EdgeProps } from '@xyflow/svelte';
	type $$Props = EdgeProps;
	export let id: $$Props['id'];
	export let sourceX: $$Props['sourceX'];
	export let sourceY: $$Props['sourceY'];
	export let targetX: $$Props['targetX'];
	export let targetY: $$Props['targetY'];
	export let markerEnd: string | undefined = undefined;
	export let style: string | undefined = undefined;
	export let data: $$Props['data'] = {};
	$: path =
		data?.kind === 'spine'
			? `M${sourceX},${sourceY} H${targetX}`
			: data?.kind === 'trunk'
				? `M${sourceX},${sourceY} V${targetY}`
				: data?.kind === 'return-trunk'
					? `M${targetX},${sourceY} V${targetY}`
					: data?.kind === 'return'
						? `M${sourceX},${sourceY} H${targetX}`
						: `M${sourceX},${targetY} H${targetX}`;
</script>

<BaseEdge {id} {path} {markerEnd} {style} />
