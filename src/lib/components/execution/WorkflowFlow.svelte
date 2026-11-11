<script lang="ts">
	import type { Participant, Workflow } from '$lib/apis/execution';
	export let mode: NonNullable<Workflow['mode']>;
	export let participants: Participant[] = [];
	export let leadId = '';
	$: lead = participants.find((p) => p.id === leadId);
	$: workers =
		mode === 'single'
			? participants.slice(0, 1)
			: participants.filter((p) => mode === 'parallel' || p.id !== leadId);
	$: result = {
		single: { name: 'Answer', detail: 'One role’s result' },
		parallel: { name: 'Separate answers', detail: 'Every role’s result' },
		summarize: { name: lead?.name || 'Lead', detail: 'Combines the answers' },
		delegate: { name: lead?.name || 'Lead', detail: 'Reviews results and decides' }
	}[mode];
</script>

<div class="workflow-flow rounded-lg border p-4 dark:border-gray-700">
	<p class="mb-3 text-sm font-medium">Task flow</p>
	{#if !participants.length}<p class="text-sm text-gray-500">Add roles to preview the workflow.</p>
	{:else}
		<div class="flow-route" role="group" aria-label="Workflow task flow">
			<div class="flow-node bg-gray-50 dark:bg-gray-850">
				<span class="text-xs font-medium text-gray-800 dark:text-gray-100"
					>{mode === 'delegate' ? lead?.name || 'Lead' : 'Task'}</span
				>
				<span class="mt-1 block text-[11px] leading-4 text-gray-500"
					>{mode === 'delegate' ? 'Assigns tasks' : 'Shared input'}</span
				>
			</div>
			<span class="flow-arrow text-gray-400" aria-hidden="true">→</span>
			<div class="space-y-2">
				{#each workers as participant}<div class="flow-node bg-white dark:bg-gray-900">
						<span class="block break-words text-xs font-medium text-gray-800 dark:text-gray-100"
							>{participant.name}</span
						>
						<span class="mt-1 block text-[11px] leading-4 text-gray-500">Independent context</span>
					</div>{/each}
			</div>
			<span class="flow-arrow text-gray-400" aria-hidden="true">→</span>
			<div class="flow-node bg-blue-50/60 dark:bg-gray-850">
				<span class="block break-words text-xs font-medium text-gray-800 dark:text-gray-100"
					>{result.name}</span
				>
				<span class="mt-1 block text-[11px] leading-4 text-gray-500">{result.detail}</span>
			</div>
		</div>
		{#if mode === 'delegate'}<p
				class="mt-3 border-t border-dashed pt-3 text-xs leading-5 text-gray-500 dark:border-gray-700"
			>
				Results return to {lead?.name || 'the lead'}, who either assigns follow-up tasks or delivers
				the final answer.
			</p>{/if}
	{/if}
</div>

<style>
	.workflow-flow {
		container-type: inline-size;
	}
	.flow-route {
		display: grid;
		grid-template-columns: minmax(0, 1fr);
		align-items: center;
		gap: 8px;
	}
	.flow-node {
		min-width: 0;
		border: 1px solid rgb(156 163 175 / 20%);
		border-radius: 8px;
		padding: 10px;
	}
	.flow-arrow {
		text-align: center;
		transform: rotate(90deg);
		line-height: 16px;
	}
	@container (min-width: 400px) {
		.flow-route {
			grid-template-columns: minmax(0, 0.8fr) 12px minmax(0, 1.2fr) 12px minmax(0, 0.9fr);
		}
		.flow-arrow {
			transform: none;
		}
	}
</style>
