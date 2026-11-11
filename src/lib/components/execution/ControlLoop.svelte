<script lang="ts">
	import type { WorkflowRun } from '$lib/apis/execution';
	import { hasPlanning } from './nodeProgram';
	import { executionCycles } from './workflowPresentation';
	export let run: WorkflowRun;
	$: adaptive = hasPlanning(run.plan);
	$: cycles = executionCycles(run);
	$: stages = adaptive
		? ['Plan', 'Dispatch', 'Observe', 'Decide']
		: ['Inputs', 'Dispatch', 'Collect', 'Reply'];
</script>

<section class="control-loop" aria-label="Workflow control loop">
	<div class="loop-heading">
		<h3>{adaptive ? 'Control loop' : 'Execution logic'}</h3>
		<span>{adaptive ? `${cycles.length} decisions` : 'Defined graph'}</span>
	</div>
	<p>Ready branches progress independently.</p>
	<div class="loop-stages">
		{#each stages as stage, i}<div class={`stage stage-${i}`}>
				<span aria-hidden="true">{['◇', '↗', '◎', '≡'][i]}</span>{stage}
			</div>
			{#if i < 3}<span class="forward" aria-hidden="true">→</span>{/if}{/each}
		{#if adaptive}<svg
				class="return-arrow"
				viewBox="0 0 400 25"
				preserveAspectRatio="none"
				aria-label="Results feed the next plan"
				><path
					d="M355 0v9q0 8-8 8H51q-8 0-8-8V3m-4 4 4-4 4 4"
					fill="none"
					stroke="currentColor"
					stroke-width="1.2"
				/></svg
			>{/if}
	</div>
</section>

<style>
	.control-loop {
		padding: 18px 20px 16px;
		border-bottom: 1px solid var(--run-border);
	}
	.loop-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
	}
	h3 {
		font-size: 13px;
		font-weight: 650;
		color: var(--run-text);
	}
	.loop-heading > span,
	p {
		font-size: 11px;
		color: var(--run-muted);
	}
	p {
		margin-top: 5px;
		line-height: 1.6;
	}
	.loop-stages {
		display: grid;
		grid-template-columns: 1fr 12px 1fr 12px 1fr 12px 1fr;
		align-items: center;
		position: relative;
		margin-top: 14px;
		padding-bottom: 22px;
	}
	.stage {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 6px;
		padding: 10px 4px;
		border: 1px solid;
		border-radius: 8px;
		font-size: 11px;
		font-weight: 600;
		white-space: nowrap;
	}
	.stage span {
		font-size: 15px;
		line-height: 1;
	}
	.stage-0 {
		color: #7050bb;
		background: #f5f0ff;
		border-color: #d9c9f3;
	}
	.stage-1 {
		color: #246bc1;
		background: #eff6ff;
		border-color: #c4dbfa;
	}
	.stage-2 {
		color: #167969;
		background: #edf9f5;
		border-color: #b6dfd2;
	}
	.stage-3 {
		color: #9c631d;
		background: #fff6e9;
		border-color: #efd6aa;
	}
	.forward {
		text-align: center;
		color: var(--run-muted);
		font-size: 13px;
	}
	.return-arrow {
		position: absolute;
		bottom: 0;
		left: 0;
		width: 100%;
		height: 22px;
		color: var(--run-muted);
	}
	:global(.dark) .stage-0 {
		background: #28203d;
		color: #d5bcff;
		border-color: #544073;
	}
	:global(.dark) .stage-1 {
		background: #182d44;
		color: #9fc8ff;
		border-color: #305475;
	}
	:global(.dark) .stage-2 {
		background: #16382e;
		color: #8cddc5;
		border-color: #326658;
	}
	:global(.dark) .stage-3 {
		background: #3a2a19;
		color: #edc184;
		border-color: #715331;
	}
	@media (max-width: 420px) {
		.control-loop {
			padding-left: 14px;
			padding-right: 14px;
		}
		.stage {
			font-size: 10px;
			gap: 4px;
		}
		.stage span {
			font-size: 12px;
		}
	}
</style>
