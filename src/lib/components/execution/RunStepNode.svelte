<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import { STEP_WIDTH, MODULE_WIDTH, BUS_INSET } from './workflowPresentation';
	type $$Props = NodeProps;
	export let data: $$Props['data'];
	$: usageTitle = [
		data.tokenScope === 'stage'
			? 'Stage total, including the calls below'
			: 'Module call total, including metered native children; excluding peer modules',
		typeof data.tokenTotal === 'number'
			? `${data.tokenTotal.toLocaleString('en')} reported tokens`
			: 'Token usage unreported'
	].join(': ');
</script>

<div
	class="run-node"
	class:work={data.kind === 'work'}
	class:selected={data.selected}
	data-status={data.status}
	style:width={`${data.kind === 'work' ? MODULE_WIDTH : STEP_WIDTH}px`}
	title={[data.task || data.phase, data.returnNote, usageTitle].filter(Boolean).join(' · ')}
>
	<Handle id="previous" type="target" position={Position.Left} />
	<Handle id="next" type="source" position={Position.Right} />
	{#if data.kind === 'work'}
		<Handle id="dispatch-in" type="target" position={Position.Left} />
	{:else}
		<Handle id="dispatch" type="source" position={Position.Bottom} style={`left:${BUS_INSET}px`} />
		<Handle
			id="result"
			type="target"
			position={Position.Bottom}
			style={`left:calc(100% - ${BUS_INSET}px)`}
		/>
	{/if}
	<div class="node-heading">
		<span class="glyph" aria-hidden="true">{data.kind === 'work' ? '↳' : '◇'}</span><strong
			>{data.name}</strong
		><span class="state"
			>{data.status === 'completed'
				? '✓'
				: data.status === 'running'
					? '●'
					: data.status === 'failed'
						? '!'
						: '·'}</span
		>
	</div>
	{#if data.kind === 'work'}
		<p class="task">{data.task}</p>
		<p class="model">{[data.framework, data.model].filter(Boolean).join(' · ')}</p>
		<div class="node-meta">
			<span>{data.phase}</span><span>{data.elapsed} · {data.tokens}</span>
		</div>
	{:else}
		<p class="decision">
			<span class="decision-phase">{data.phase}</span><span class="decision-meta"
				>{data.count} tasks · {data.tokenScope === 'stage' ? 'Stage total' : 'Own'}
				{data.tokens}</span
			>
		</p>
	{/if}
</div>

<style>
	.run-node {
		min-height: 74px;
		border: 1px solid #d7cee9;
		background: #faf8ff;
		color: #3b3154;
		border-radius: 10px;
		padding: 11px 13px;
		font-size: 12px;
		box-shadow: 0 2px 5px #15203a05;
	}
	.run-node.selected {
		border-color: #8b70c6;
		box-shadow: 0 0 0 3px #a88fd520;
	}
	.node-heading {
		display: flex;
		align-items: center;
		gap: 7px;
	}
	.node-heading strong {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-weight: 650;
	}
	.glyph {
		color: #9075bc;
	}
	.state {
		color: #16826e;
	}
	.decision {
		margin-top: 4px;
		color: #705a96;
		display: flex;
		justify-content: space-between;
		gap: 8px;
		font-size: 11px;
	}
	.decision-phase {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.decision-meta {
		flex-shrink: 0;
		color: #7d8190;
		font-size: 9px;
	}
	.work {
		min-height: 88px;
		padding: 8px 9px;
		background: #fff;
		border-color: #d9e2df;
		color: #243b35;
	}
	.task {
		margin-top: 4px;
		font-size: 11px;
		font-weight: 550;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.model {
		font-size: 9px;
		color: #7b8390;
		margin-top: 3px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.node-meta {
		display: flex;
		justify-content: space-between;
		gap: 8px;
		margin-top: 5px;
		font-size: 9px;
		color: #16826e;
	}
	[data-status='running'] {
		border-color: #d1ad64;
		background: #fffaf0;
	}
	[data-status='running'] .node-meta,
	[data-status='running'] .state {
		color: #956413;
	}
	[data-status='failed'],
	[data-status='interrupted'] {
		border-color: #d99aa8;
		background: #fff5f7;
	}
	[data-status='failed'] .node-meta,
	[data-status='interrupted'] .node-meta {
		color: #ac3c58;
	}
	:global(.dark) .run-node {
		background: #292336;
		border-color: #584b70;
		color: #e2d9f0;
	}
	:global(.dark) .work {
		background: #202b2b;
		border-color: #465651;
		color: #dbe9e4;
	}
	:global(.dark) .model {
		color: #a0acb4;
	}
</style>
