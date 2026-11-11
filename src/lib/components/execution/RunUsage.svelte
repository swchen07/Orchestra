<script lang="ts">
	import type { WorkflowRun } from '$lib/apis/execution';
	import { summarizeUsage, usageLabel, tokenNumber } from './workflowUsage';
	import { roleViews, duration } from './workflowView';
	import { callStart, callEnd } from './workflowPresentation';
	import NativeAgentUsage from './NativeAgentUsage.svelte';
	export let run: WorkflowRun;
	$: total = summarizeUsage(run);
	$: elapsed =
		(run.finished_at ? (run.last_event_at ?? run.finished_at * 1000) : Date.now()) -
		run.created_at * 1000;
	$: agents = roleViews(run).map((role) => ({
		...role,
		usage: summarizeUsage(run, role.calls),
		elapsed: role.calls.reduce(
			(sum, call) => sum + Math.max(0, (callEnd(run, call) || Date.now()) - callStart(call)),
			0
		)
	}));
</script>

<details class="run-usage">
	<summary aria-label="Run token usage"
		><span>Tokens <strong>{usageLabel(total)}</strong></span><small
			>{total.reported}/{total.calls} calls reported · {total.metered} complete</small
		><span class="chevron">⌄</span></summary
	>
	<div class="usage-content">
		<div class="usage-table">
			<table>
				<thead
					><tr
						><th>Module</th><th>Usage coverage</th><th>Input</th><th>Output</th><th>Total</th><th
							>Cache read</th
						><th>Time</th></tr
					></thead
				><tbody
					>{#each agents as agent}<tr
							><th>{agent.name}</th><td
								>{agent.usage.reported}/{agent.usage.calls} reported · {agent.usage.metered} complete</td
							><td>{tokenNumber(agent.usage.input)}</td><td>{tokenNumber(agent.usage.output)}</td
							><td title={agent.usage.total?.toLocaleString('en')}>{usageLabel(agent.usage)}</td><td
								>{tokenNumber(agent.usage.cacheRead)}</td
							><td>{duration(agent.elapsed)}</td></tr
						>{/each}</tbody
				><tfoot
					><tr
						><th>Run</th><td>{total.reported}/{total.calls} reported · {total.metered} complete</td
						><td>{tokenNumber(total.input)}</td><td>{tokenNumber(total.output)}</td><td
							title={total.total?.toLocaleString('en')}>{usageLabel(total)}</td
						><td>{tokenNumber(total.cacheRead)}</td><td>{duration(elapsed)}</td></tr
					></tfoot
				>
			</table>
		</div>
		<p>
			Each call is counted once. Module totals include native descendants and exclude peer modules.
			Input includes history and cache reads; cache is not added again. Reading another module's
			report uses input tokens again. Times sum each module's calls; parallel times can overlap.
		</p>
		<NativeAgentUsage {run} />
		{#if run.control?.budget}<p>
				Reported tokens: {run.control.budget.reported_tokens.toLocaleString('en')} / {run.control.budget.max_tokens.toLocaleString(
					'en'
				)}. Calls already running can finish; missing native usage remains unknown.
			</p>{/if}
		{#if total.missing}<p class="missing">
				{total.unreported} calls without full input/output counters; {total.reported -
					total.metered} reported calls have incomplete native totals. “—” means unreported; ≥ shows a
				known subtotal.
			</p>{/if}
	</div>
</details>

<style>
	.run-usage {
		margin: 11px 0 0;
		font-size: 11px;
		border-top: 1px solid var(--run-border);
		padding-top: 9px;
	}
	.run-usage > summary {
		display: flex;
		align-items: center;
		gap: 9px;
		cursor: pointer;
		list-style: none;
		color: var(--run-muted);
	}
	.run-usage > summary::-webkit-details-marker {
		display: none;
	}
	.run-usage strong {
		font-weight: 600;
		color: var(--run-text);
		margin-left: 4px;
	}
	.run-usage small {
		font-size: 10px;
	}
	.chevron {
		margin-left: auto;
	}
	.usage-content {
		padding-top: 11px;
	}
	.usage-table {
		overflow-x: auto;
	}
	table {
		width: 100%;
		font-size: 10px;
		text-align: right;
		border-collapse: collapse;
		white-space: nowrap;
	}
	th,
	td {
		padding: 5px 7px;
	}
	thead th {
		font-weight: 500;
		color: var(--run-muted);
	}
	th:first-child {
		text-align: left;
		padding-left: 0;
	}
	tbody th {
		font-weight: 550;
	}
	tfoot {
		border-top: 1px solid var(--run-border);
		font-weight: 600;
	}
	.usage-content p {
		font-size: 9px;
		color: var(--run-muted);
		line-height: 1.6;
		margin-top: 8px;
	}
	.usage-content .missing {
		color: #9b7637;
	}
</style>
