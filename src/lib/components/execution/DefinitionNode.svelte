<script lang="ts">
	import { Handle, Position, type NodeProps } from '@xyflow/svelte';
	import { nodeOperations } from './nodeProgram';
	type $$Props = NodeProps;
	export let data: $$Props['data'];
	export let selected: $$Props['selected'] = false;
	const operations: Record<string, string> = nodeOperations;
	const policies: Record<string, string> = {
		all_terminal: 'Keep every outcome',
		all_success: 'Require all successes',
		first_success: 'First success',
		quorum: 'Require a number of successes'
	};
</script>

<div
	class="definition-node"
	class:selected
	class:result={data.result}
	class:input={data.operation === 'input'}
>
	{#if data.operation !== 'input'}<Handle type="target" position={Position.Left} />{/if}
	<div class="node-heading">
		<strong>{data.name}</strong>{#if data.result}<span>Output</span>{/if}
	</div>
	<p>
		{(data.role !== data.name ? data.role : null) ??
			policies[String(data.policy)] ??
			operations[String(data.operation)] ??
			data.operation}
	</p>
	{#if (data.role && data.role !== data.name) || data.policy}<small
			>{operations[String(data.operation)]}</small
		>{/if}
	{#if data.operation !== 'output'}<Handle type="source" position={Position.Right} />{/if}
	{#if data.feedbackIn}<Handle id="feedback-in" type="target" position={Position.Bottom} style="opacity:0" />{/if}
	{#if data.feedbackOut}<Handle id="feedback-out" type="source" position={Position.Bottom} style="opacity:0" />{/if}
</div>

<style>
	.definition-node {
		width: 200px;
		min-height: 88px;
		padding: 12px 14px;
		border: 1px solid #ddd9e8;
		border-radius: 12px;
		background: #fff;
		color: #373044;
		box-shadow: 0 2px 6px #20143805;
	}
	.node-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
	}
	strong {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 13px;
		font-weight: 600;
	}
	p {
		margin-top: 5px;
		color: #71677f;
		font-size: 11px;
	}
	small {
		display: block;
		margin-top: 3px;
		color: #8b8295;
		font-size: 10px;
	}
	.node-heading span {
		flex-shrink: 0;
		white-space: nowrap;
		border-radius: 4px;
		padding: 2px 5px;
		background: #e8f2ee;
		color: #377563;
		font-size: 9px;
	}
	.input {
		background: #f6f4f9;
		border-color: #e4e0ec;
	}
	.result {
		border-color: #bdd6cc;
		background: #f7fbf9;
	}
	.selected {
		outline: 2px solid #9a82cb;
		outline-offset: 2px;
	}
	:global(.dark) .definition-node {
		background: #211e27;
		border-color: #4a405a;
		color: #e3dcef;
	}
	:global(.dark) .definition-node p,
	:global(.dark) .definition-node small {
		color: #b8acc8;
	}
	:global(.dark) .result {
		background: #1d2b25;
		border-color: #416656;
	}
</style>
