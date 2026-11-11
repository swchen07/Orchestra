<script lang="ts">
	import type { WorkflowRun, NativeUsage } from '$lib/apis/execution';
	import { nativeTotals, tokenNumber } from './workflowUsage';
	import { roleViews, duration } from './workflowView';
	export let run: WorkflowRun;
	const nativeDuration = (value: number | undefined) =>
		value === undefined
			? '—'
			: value < 1000
				? `${Math.round(value)}ms`
				: value < 60000
					? `${(value / 1000).toFixed(1)}s`
					: duration(value);
	function depth(tree: NativeUsage, id: string) {
		let value = 0,
			current = tree.agents.find((a) => a.id === id);
		const seen = new Set<string>();
		while (current?.parent_id && !seen.has(current.id) && value < 8) {
			seen.add(current.id);
			value++;
			current = tree.agents.find((a) => a.id === current?.parent_id);
		}
		return value;
	}
	$: modules = roleViews(run).map((role) => ({ ...role, native: nativeTotals(role.calls) }));
</script>

<section class="native-accounting" aria-label="Native agent accounting">
	<h4>Native agent accounting</h4>
	<div class="overflow-x-auto">
		<table>
			<thead><tr><th>Module</th><th>Self</th><th>Children</th><th>Native agents</th></tr></thead>
			<tbody
				>{#each modules as module}<tr
						><th>{module.name}</th>
						<td title={module.native.self?.toLocaleString('en')}
							>{tokenNumber(module.native.self)}</td
						>
						<td title={module.native.children?.toLocaleString('en')}
							>{module.native.complete || module.native.children === undefined
								? ''
								: '≥ '}{tokenNumber(module.native.children)}</td
						>
						<td
							>{module.native.observed ? module.native.childCount : '—'} children · {module.native
								.complete
								? 'Reconciled'
								: 'Partial / unknown'}</td
						>
					</tr>{/each}</tbody
			>
		</table>
	</div>
	<p>
		Self excludes native children. Total above includes the tree once. Missing lineage is unknown,
		not zero.
	</p>
	{#each modules as module}{#each module.calls.filter((c) => c.execution?.native_usage) as call}
			{@const tree = call.execution!.native_usage!}
			<details>
				<summary
					>{module.name} · {tree.child_count} native children · {tree.complete
						? 'Reconciled'
						: 'Partial'}</summary
				>
				<div class="overflow-x-auto">
					<p>
						Module elapsed {nativeDuration(call.module?.timings?.invocation_ms)} · Self exclusive {nativeDuration(
							call.module?.timings?.self_exclusive_ms
						)} · Child work {nativeDuration(call.module?.timings?.children_exclusive_ms)} · Total work
						{nativeDuration(call.module?.timings?.tree_exclusive_ms)}
					</p>
					<table aria-label={`Native agents for ${module.name}`}>
						<thead
							><tr
								><th>Agent</th><th>Own tokens</th><th>Elapsed</th><th>Exclusive</th><th>Status</th
								></tr
							></thead
						>
						<tbody
							>{#each tree.agents as agent, index}<tr
									><th title={agent.native_session_id ?? agent.native_agent_id ?? agent.id}>
										<span style={`padding-left:${depth(tree, agent.id) * 8}px`}
											>{agent.id === tree.root_id
												? 'Root'
												: `↳ Agent ${String(index).padStart(2, '0')}`}</span
										></th
									>
									<td title={agent.usage.total_tokens?.toLocaleString('en')}
										>{tokenNumber(agent.usage.total_tokens)}</td
									>
									<td>{nativeDuration(agent.timings.wall_ms)}</td>
									<td>{nativeDuration(agent.timings.exclusive_wall_ms)}</td>
									<td>{agent.status}</td></tr
								>{/each}</tbody
						>
					</table>
				</div>
				<p>
					Elapsed includes waiting. Exclusive removes direct-child active intervals as a union.
					Parallel child work can overlap; these times are not model inference time.
				</p>
				{#if tree.warnings.length}<p class="text-amber-600">{tree.warnings.join(' · ')}</p>{/if}
				{#if tree.reconciled_from_receipts}<p>
						Reconciled from retained native completion receipts; no model call was replayed.
					</p>{/if}
				<details>
					<summary>Accounting sources</summary><code
						>{tree.agents.flatMap((a) => a.source_event_ids).join('\n')}</code
					>
				</details>
			</details>
		{/each}{/each}
</section>

<style>
	.native-accounting {
		border-top: 1px solid var(--run-border);
		margin-top: 12px;
		padding-top: 10px;
	}
	h4 {
		font-size: 11px;
		font-weight: 600;
		margin-bottom: 8px;
	}
	table {
		width: 100%;
		font-size: 10px;
		white-space: nowrap;
		text-align: right;
		border-collapse: collapse;
	}
	th,
	td {
		padding: 5px 7px;
	}
	th:first-child {
		text-align: left;
		padding-left: 0;
	}
	thead {
		color: var(--run-muted);
	}
	p {
		color: var(--run-muted);
		font-size: 9px;
		line-height: 1.6;
		margin: 7px 0;
	}
	details {
		margin-top: 8px;
	}
	summary {
		cursor: pointer;
		font-size: 10px;
		color: var(--run-muted);
	}
	code {
		display: block;
		white-space: pre-wrap;
		word-break: break-all;
		font-size: 9px;
		padding: 8px;
	}
</style>
