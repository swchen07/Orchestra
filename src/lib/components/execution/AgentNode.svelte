<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import { statusLabel } from './workflowView';
	type $$Props = NodeProps;
	export let data: $$Props['data'];
	type AgentData = {
		name: string;
		status: string;
		phase?: string;
		kind: string;
		framework?: string;
		model?: string;
		task?: string;
		vertical?: boolean;
		tone?: string;
		elapsed?: string;
		carry?: boolean;
		feedbackIn?: boolean;
		feedbackOut?: boolean;
	};
	$: node = data as AgentData;
</script>

{#if node.kind === 'cycle'}
	<div class="cycle-label"><strong>{node.name}</strong><span>{node.phase}</span></div>
{:else}
	<div
		class="agent-node"
		class:running={node.status === 'running'}
		class:failed={['failed', 'interrupted'].includes(node.status)}
		class:boundary={node.kind === 'boundary'}
		class:execution={node.kind === 'execution'}
		class:complete={node.status === 'completed'}
		class:violet={node.tone === 'violet'}
		class:carry={node.carry}
	>
		<Handle type="target" position={node.vertical ? Position.Top : Position.Left} />
		<div class="flex items-center gap-2">
			<span class="node-status" class:pulse={node.status === 'running'} aria-hidden="true"
				>{node.status === 'completed'
					? '✓'
					: ['failed', 'interrupted'].includes(node.status)
						? '!'
						: '·'}</span
			><strong class="truncate">{node.name}</strong>
		</div>
		{#if ['role', 'execution'].includes(node.kind)}<div
				class="mt-1 truncate text-[11px] text-gray-500"
			>
				{[node.framework, node.model].filter(Boolean).join(' · ')}
			</div>{/if}
		<div class="node-phase">
			{node.phase ?? statusLabel(node.status)}{#if node.elapsed}<span>{node.elapsed}</span>{/if}
		</div>
		{#if node.task}<div class="mt-1 line-clamp-2 text-[10px] leading-4 text-gray-500">
				{node.task}
			</div>{/if}
		<Handle type="source" position={node.vertical ? Position.Bottom : Position.Right} />
		{#if node.feedbackIn}<Handle id="feedback-in" type="target" position={Position.Bottom} style="opacity:0" />{/if}
		{#if node.feedbackOut}<Handle id="feedback-out" type="source" position={Position.Bottom} style="opacity:0" />{/if}
	</div>
{/if}

<style>
	.agent-node {
		width: 200px;
		min-height: 98px;
		padding: 11px 13px;
		border: 1px solid #e5e7eb;
		border-radius: 10px;
		background: white;
		color: #374151;
		box-shadow: 0 2px 7px #00000006;
		font-size: 13px;
	}
	.boundary {
		width: 160px;
		min-height: 70px;
	}
	.running {
		border-color: #c59742;
		box-shadow: 0 0 0 3px #d8ae4820;
	}
	.failed {
		border-color: #f87171;
	}
	.node-status {
		width: 17px;
		height: 17px;
		border-radius: 50%;
		background: #e7edf3;
		color: #77899c;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		font-size: 11px;
		flex-shrink: 0;
	}
	.running .node-status {
		background: #c28a26;
		color: white;
	}
	.failed .node-status {
		background: #ef4444;
	}
	.pulse {
		animation: breathe 1.7s infinite;
	}
	@keyframes breathe {
		50% {
			opacity: 0.35;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.pulse {
			animation: none;
		}
	}
	:global(.dark) .agent-node {
		background: #171717;
		color: #e5e7eb;
		border-color: #404040;
	}
	.cycle-label {
		width: 200px;
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 8px;
		color: #7c60b4;
		font-size: 11px;
	}
	.cycle-label strong {
		padding: 4px 11px;
		border-radius: 6px;
		background: #f0eafa;
		font-weight: 600;
	}
	.cycle-label span {
		font-size: 10px;
		color: #8290a4;
	}
	.execution {
		min-height: 100px;
		border-color: #b8dcd0;
		background: #f5fbf8;
	}
	.complete .node-status {
		background: #16826e;
		color: white;
	}
	.execution.violet {
		border-color: #d6c5ee;
		background: #f9f6ff;
	}
	.execution.running { background: #fffbf1; }
	.execution.running .node-phase { color: #98691f; }
	.execution.failed { background: #fff2f5; }
	.execution.failed .node-phase { color: #b24762; }
	.carry {
		border-style: dashed;
	}
	.node-phase {
		display: flex;
		justify-content: space-between;
		gap: 8px;
		margin-top: 7px;
		font-size: 10px;
		color: #6d7f8a;
	}
	.node-phase span {
		font-variant-numeric: tabular-nums;
	}
	.execution .node-phase {
		color: #3d7f6e;
	}
	:global(.dark) .execution {
		background: #1b302c;
		border-color: #355f54;
	}
	:global(.dark) .execution.violet {
		background: #30263e;
		border-color: #5b4773;
	}
	:global(.dark) .cycle-label strong {
		background: #322846;
		color: #c7afec;
	}
	:global(.dark) .node-phase,
	:global(.dark) .execution .node-phase {
		color: #9cbaae;
	}
	:global(.dark) .running {
		border-color: #60a5fa;
	}
</style>
