<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { WorkflowRun } from '$lib/apis/execution';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import { trajectoryRecords, traceText, type TraceRecord } from './trajectoryView';
	import { callUsage, tokenNumber } from './workflowUsage';
	import { duration, statusLabel } from './workflowView';
	export let run: WorkflowRun;
	export let role = 'all';
	export let callId = '';
	const dispatch = createEventDispatcher<{ report: { role: string; call: string } }>();
	let filter = 'all',
		limit = 60;
	let tabs: Record<string, string> = {};
	const categories = [
		{ id: 'all', label: 'All' },
		{ id: 'reply', label: 'Replies' },
		{ id: 'tool', label: 'Tools' },
		{ id: 'session', label: 'Sessions' },
		{ id: 'lifecycle', label: 'Execution' },
		{ id: 'review', label: 'Reviews' }
	];
	const labels = {
		task: 'TASK',
		session: 'SESSION',
		reply: 'REPLY',
		tool: 'TOOL',
		lifecycle: 'CALL',
		report: 'REPORT',
		control: 'RUN',
		review: 'REVIEW'
	};
	const glyphs = {
		task: '↳',
		session: '↻',
		reply: '✦',
		tool: '⌘',
		lifecycle: '◷',
		report: '▤',
		control: '◇',
		review: '✓'
	};
	$: records = trajectoryRecords(run).filter(
		(r) =>
			(!callId || r.callId === callId) &&
			(role === 'all' || run.calls.find((c) => c.id === r.callId)?.participant_id === role)
	);
	$: filtered = records.filter((r) => filter === 'all' || r.category === filter);
	function tabFor(record: TraceRecord, selected: Record<string, string>) {
		return selected[record.id] ?? (record.category === 'task' ? 'input' : 'output');
	}
	function rowTokens(record: TraceRecord, source: WorkflowRun) {
		const call = source.calls.find((c) => c.id === record.callId);
		return record.category === 'reply' && call ? callUsage(source, call).total : undefined;
	}
	function sessionDetails(record: TraceRecord) {
		return (record.output ?? {}) as {
			method?: string;
			context_mode?: string;
			actual_model?: string;
			history_messages?: number;
			source_run_id?: string;
		};
	}
</script>

<p class="trace-explanation">
	Activities belong to this agent’s calls. Assignment and execution rows are not extra model calls.
</p>
<div class="trace-filters" aria-label="Activity categories">
	{#each categories as item}<button
			aria-pressed={filter === item.id}
			on:click={() => {
				filter = item.id;
				limit = 60;
			}}
			>{item.label}<small
				>{item.id === 'all'
					? records.length
					: records.filter((r) => r.category === item.id).length}</small
			></button
		>{/each}
</div>
<div class="trace-ledger" aria-label="Categorized execution trajectory">
	<div class="trace-columns" aria-hidden="true">
		<span></span><span class="trace-kind">Type</span><span>Module / task</span><span>Action</span
		><span>Tokens</span><span>Time</span><span>State</span><span></span>
	</div>
	{#if filtered.length > limit}<button class="load-more" on:click={() => (limit += 60)}
			>Load earlier activity</button
		>{/if}
	{#each filtered.slice(-limit) as record (record.id)}
		<details class="trace-card" data-category={record.category} data-status={record.status}>
			<summary title={`${record.role} · ${record.task ?? ''} · ${record.title}`}>
				<span class="trace-glyph" aria-hidden="true"
					>{record.category === 'review' && record.status === 'changes_requested'
						? '↩'
						: glyphs[record.category]}</span
				>
				<span class="trace-kind">{labels[record.category]}</span>
				<span class="trace-subject"
					>{record.role}<small>{record.task ? ' · ' + record.task : ''}</small></span
				>
				<strong class="trace-action">{record.title}</strong>
				<span
					class="trace-tokens"
					title={record.category === 'reply'
						? 'Reported tokens for this module call'
						: 'Tokens are metered per module call; not attributed to individual events'}
					>{tokenNumber(rowTokens(record, run))}</span
				>
				<time class="trace-time">{record.end ? duration(record.end - record.at) : '—'}</time>
				<span
					class="trace-status"
					title={record.status === 'unconfirmed' ? 'No tool completion event was captured' : ''}
					>{record.status === 'unconfirmed' ? 'Unknown' : statusLabel(record.status)}</span
				><span class="chevron" aria-hidden="true"><ChevronDown className="size-3" /></span>
			</summary>
			<div class="trace-detail">
				{#if record.category === 'report'}
					<p class="reference">{record.reportId}</p>
					<button
						class="report-link"
						on:click={() =>
							dispatch('report', {
								role: run.calls.find((c) => c.id === record.callId)?.participant_id ?? 'all',
								call: record.callId ?? ''
							})}>Open intermediate report →</button
					>
				{:else}
					<div class="detail-tabs" aria-label={`${record.title} detail sections`}>
						{#each ['input', 'output', 'timing'] as name}<button
								aria-pressed={tabFor(record, tabs) === name}
								on:click={() => (tabs = { ...tabs, [record.id]: name })}
								>{name === 'input' ? 'Input' : name === 'output' ? 'Output' : 'Timing'}</button
							>{/each}
					</div>
					{#if tabFor(record, tabs) === 'timing'}
						<dl class="timing-data">
							<dt>Started</dt>
							<dd>+{duration(record.at - run.created_at * 1000)}</dd>
							<dt>Finished</dt>
							<dd>
								{record.end ? '+' + duration(record.end - run.created_at * 1000) : 'Not recorded'}
							</dd>
							<dt>Duration</dt>
							<dd>{record.end ? duration(record.end - record.at) : 'Not recorded'}</dd>
						</dl>
					{:else if tabFor(record, tabs) === 'input'}
						{#if record.category === 'task'}{@const call = run.calls.find(
								(c) => c.id === record.callId
							)}
							<p class="input-explanation">
								{call?.kind === 'decision'
									? 'One controller request: task, available modules, graph and observations. It asks for the next planning action.'
									: 'The task assigned to this module, including its referenced input data.'}
							</p>
							{#if call?.request_stats}<dl class="timing-data">
									<dt>App text sent</dt>
									<dd>{call.request_stats.submitted_text_chars.toLocaleString()} chars</dd>
									<dt>Replay removed</dt>
									<dd>
										{call.request_stats.replay_chars_removed.toLocaleString()} chars · {call
											.request_stats.compacted_snapshots} old snapshots
									</dd>
									<dt>Delivery</dt>
									<dd>{call.request_stats.delivery.replaceAll('_', ' ')}</dd>
								</dl>
								<p class="input-explanation">
									Character counts exclude framework instructions. Token usage is reported
									separately.
								</p>{/if}{/if}
						{#if record.input !== undefined}<pre>{traceText(record.input)}</pre>{:else}<p
								class="no-data"
							>
								No input captured for this record.
							</p>{/if}
					{:else if record.category === 'reply' && record.output}
						<div class="prose prose-sm max-w-none break-words dark:prose-invert">
							<Markdown
								id={`trace-${record.id}`}
								content={traceText(record.output)}
								allowHtml={false}
								allowEmbeds={false}
								allowRemoteContent={false}
								editCodeBlock={false}
							/>
						</div>
					{:else if record.category === 'lifecycle'}
						<ol class="lifecycle">
							{#each record.events.filter((e) => e.type !== 'model.usage') as event}<li>
									<time>+{duration(event.at - run.created_at * 1000)}</time><span
										>{event.title}</span
									>
								</li>{/each}
						</ol>
						{#if record.output && Object.keys(record.output).length}<details class="raw">
								<summary>Token usage</summary>
								<pre>{traceText(record.output)}</pre>
							</details>{/if}
						{@const environment = run.calls.find((c) => c.id === record.callId)?.execution
							?.environment}
						{#if environment}<details class="raw">
								<summary>Environment & isolation</summary>
								<dl class="timing-data">
									{#each Object.entries(environment) as [name, value]}<dt>
											{name.replaceAll('_', ' ')}
										</dt>
									<dd>{Array.isArray(value) ? value.length ? value.join(', ') : 'None' : String(value ?? 'None')}</dd>{/each}
								</dl>
								<p class="text-xs text-gray-500">
									Logical context isolation does not grant an OS sandbox. The named authority
									enforces this invocation’s capabilities.
								</p>
							</details>{/if}
					{:else if record.category === 'session'}
						{@const context = sessionDetails(record)}
						<dl class="timing-data">
							<dt>Continuation</dt>
							<dd>
								{context.method === 'native' || context.context_mode === 'native'
									? 'Native session resumed'
									: context.method === 'replay'
										? 'Role dialogue restored'
										: context.context_mode === 'new-native'
											? 'New native session'
											: 'Runtime confirmed'}
							</dd>
							{#if context.actual_model}<dt>Model</dt>
								<dd>{context.actual_model}</dd>{/if}{#if context.history_messages !== undefined}<dt>
									Retained messages
								</dt>
								<dd>{context.history_messages}</dd>{/if}{#if context.source_run_id}<dt>
									Source run
								</dt>
								<dd>{context.source_run_id}</dd>{/if}
						</dl>
					{:else if record.output !== undefined}<pre>{traceText(record.output)}</pre>
					{:else}<p class="no-data">
							{record.status === 'running'
								? 'Waiting for the tool result…'
								: record.status === 'unconfirmed'
									? 'The parent call ended without a captured tool completion. Success is unconfirmed.'
									: 'No output captured for this record.'}
						</p>{/if}
				{/if}
				<details class="raw">
					<summary>Raw events · historical payloads ({record.events.length})</summary>
					<p>
						Each payload records its own event time. A start event is not the current invocation
						state.
					</p>
					<pre>{traceText(record.events)}</pre>
				</details>
			</div>
		</details>
	{:else}<p class="no-data">No activity in this category for the selected agent.</p>{/each}
</div>

<style>
	.trace-filters {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		margin-bottom: 16px;
	}
	.trace-filters button {
		padding: 5px 9px;
		font-size: 11px;
		border: 1px solid transparent;
		border-radius: 6px;
		color: var(--run-muted);
	}
	.trace-filters button[aria-pressed='true'] {
		background: #eee9f8;
		color: #6b51aa;
		border-color: #e1d6f0;
	}
	.trace-filters small {
		margin-left: 5px;
		font-size: 9px;
		opacity: 0.7;
	}
	.trace-ledger {
		display: flex;
		flex-direction: column;
		gap: 0;
	}
	.trace-explanation {
		font-size: 10px;
		color: var(--run-muted);
		line-height: 1.6;
		margin-bottom: 9px;
	}
	.trace-ledger {
		container-type: inline-size;
	}
	.trace-card {
		border-bottom: 1px solid var(--run-border);
		background: var(--run-canvas);
	}
	.trace-columns,
	.trace-card > summary {
		display: grid;
		grid-template-columns: 18px 48px minmax(55px, 1fr) minmax(68px, 1.1fr) 46px 30px 72px 20px;
		align-items: center;
		gap: 6px;
		padding: 7px 4px;
		min-height: 34px;
		cursor: pointer;
		list-style: none;
		font-size: 10px;
	}
	.trace-columns {
		color: var(--run-muted);
		font-size: 8px !important;
		padding-top: 4px !important;
		padding-bottom: 4px !important;
		min-height: 24px !important;
	}
	.trace-columns span:nth-last-child(-n + 4) {
		text-align: right;
	}
	.trace-columns span:nth-last-child(2) {
		text-align: center;
	}
	.trace-columns .trace-kind {
		background: transparent;
		padding: 0;
		height: auto;
		justify-content: flex-start;
	}
	.trace-card > summary::-webkit-details-marker {
		display: none;
	}
	.trace-glyph {
		display: grid;
		place-items: center;
		width: 18px;
		height: 18px;
		border-radius: 5px;
		color: #8063b0;
		font-size: 13px;
		text-align: center;
	}
	.trace-kind {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		height: 20px;
		padding: 0 5px;
		border-radius: 5px;
		background: #f1ecf8;
		white-space: nowrap;
		color: #8063b0;
		font-size: 8px;
		letter-spacing: 0.04em;
		font-weight: 600;
	}
	.trace-subject,
	.trace-action {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 10px;
		font-weight: 500;
	}
	.trace-subject {
		color: var(--run-muted);
	}
	.trace-subject small {
		font-size: 10px;
	}
	.trace-action {
		color: var(--run-text);
	}
	.trace-tokens,
	.trace-time {
		font-size: 9px;
		color: var(--run-muted);
		text-align: right;
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
	}
	.trace-status {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 20px;
		padding: 2px 6px;
		border: 1px solid #cee8dd;
		border-radius: 6px;
		background: #eaf5ef;
		font-size: 9px;
		color: #16826e;
		text-align: center;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.chevron {
		display: grid;
		place-items: center;
		justify-self: center;
		width: 20px;
		height: 20px;
		border-radius: 5px;
		color: var(--run-muted);
		transform-origin: center;
	}
	.trace-card > summary:hover .chevron {
		background: var(--run-surface);
	}
	.trace-card[open] .chevron {
		transform: rotate(180deg);
	}
	@container (max-width:480px) {
		.trace-columns,
		.trace-card > summary {
			grid-template-columns: 17px minmax(40px, 1fr) minmax(48px, 1fr) 40px 26px 66px 20px;
			gap: 5px;
		}
		.trace-kind {
			display: none;
		}
	}

	[data-category='tool'] .trace-glyph {
		background: #e8f3ef;
		color: #318774;
	}
	[data-category='tool'] .trace-kind {
		background: #e8f3ef;
		color: #388672;
	}
	[data-category='session'] .trace-glyph {
		background: #eaf0fc;
		color: #5479ad;
	}
	[data-category='session'] .trace-kind {
		background: #eaf0fc;
		color: #5e7ba6;
	}
	[data-category='lifecycle'] .trace-glyph,
	[data-category='task'] .trace-glyph {
		background: #f0f2f5;
		color: #738091;
	}
	[data-category='lifecycle'] .trace-kind,
	[data-category='task'] .trace-kind {
		background: #f0f2f5;
		color: #778291;
	}
	[data-category='report'] .trace-glyph {
		background: #fff2dc;
		color: #aa7c2e;
	}
	[data-category='report'] .trace-kind {
		background: #fff2dc;
		color: #a5824a;
	}
	[data-status='running'] .trace-status,
	[data-status='queued'] .trace-status,
	[data-status='waiting'] .trace-status,
	[data-status='changes_requested'] .trace-status {
		background: #fff4df;
		border-color: #efdbb0;
		color: #a27828;
	}
	[data-status='failed'] .trace-status,
	[data-status='interrupted'] .trace-status {
		background: #fceef2;
		border-color: #efd3dc;
		color: #bb4964;
	}
	[data-status='unconfirmed'] .trace-status {
		background: #fff5e4;
		border-color: #eadbbd;
		color: #9a7c43;
	}
	[data-status='cancelled'] .trace-status {
		background: #f0f2f5;
		border-color: #dce1e8;
		color: #738091;
	}
	.input-explanation {
		font-size: 10px;
		line-height: 1.6;
		color: var(--run-muted);
		margin-bottom: 10px;
	}
	.trace-detail {
		border-top: 1px solid var(--run-border);
		padding: 12px 14px;
		font-size: 11px;
	}
	.detail-tabs {
		display: flex;
		gap: 14px;
		margin-bottom: 13px;
	}
	.detail-tabs button {
		font-size: 10px;
		padding: 2px 0 5px;
		color: var(--run-muted);
		border-bottom: 2px solid transparent;
	}
	.detail-tabs button[aria-pressed='true'] {
		color: var(--run-accent);
		border-color: var(--run-accent);
	}
	pre {
		font-size: 10px;
		line-height: 1.7;
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		max-height: 360px;
		overflow: auto;
		color: var(--run-text);
		background: var(--run-surface);
		border-radius: 6px;
		padding: 10px;
	}
	.timing-data {
		display: grid;
		grid-template-columns: 105px minmax(0, 1fr);
		gap: 9px;
		font-size: 11px;
	}
	.timing-data dt {
		color: var(--run-muted);
	}
	.timing-data dd {
		overflow-wrap: anywhere;
	}
	.raw {
		margin-top: 13px;
		color: var(--run-muted);
		font-size: 10px;
	}
	.raw summary {
		cursor: pointer;
	}
	.raw p {
		margin: 9px 0;
		line-height: 1.7;
	}
	.reference {
		font-size: 10px;
		color: var(--run-muted);
		overflow-wrap: anywhere;
	}
	.report-link,
	.load-more {
		color: var(--run-accent);
		font-size: 11px;
		margin-top: 9px;
	}
	.no-data {
		font-size: 11px;
		line-height: 1.7;
		color: var(--run-muted);
		padding: 8px 0;
	}
	.lifecycle li {
		display: flex;
		gap: 12px;
		font-size: 11px;
		padding: 5px 0;
	}
	.lifecycle time {
		color: var(--run-muted);
		min-width: 40px;
	}
	:global(.dark) .trace-glyph {
		background: #332b42;
	}
	:global(.dark) .trace-kind {
		background: #332b42;
		color: #c6afe7;
	}
	:global(.dark) .trace-columns .trace-kind {
		background: transparent;
		color: var(--run-muted);
	}
	:global(.dark) .trace-status {
		background: #223c33;
		border-color: #355b4d;
		color: #9bd3ba;
	}
	:global(.dark) [data-status='running'] .trace-status,
	:global(.dark) [data-status='queued'] .trace-status,
	:global(.dark) [data-status='waiting'] .trace-status,
	:global(.dark) [data-status='unconfirmed'] .trace-status,
	:global(.dark) [data-status='changes_requested'] .trace-status {
		background: #403525;
		border-color: #675333;
		color: #e2c08a;
	}
	:global(.dark) [data-status='failed'] .trace-status,
	:global(.dark) [data-status='interrupted'] .trace-status {
		background: #402a33;
		border-color: #68414f;
		color: #e7a9bb;
	}
	:global(.dark) [data-status='cancelled'] .trace-status {
		background: #2b3038;
		border-color: #424b58;
		color: #b2bdcc;
	}
	:global(.dark) .trace-filters button[aria-pressed='true'] {
		background: #332b42;
		color: #c4afe4;
		border-color: #544166;
	}
	:global(.dark) [data-category='tool'] .trace-glyph,
	:global(.dark) [data-category='tool'] .trace-kind {
		background: #263b34;
		color: #9bc9b6;
	}
	:global(.dark) [data-category='session'] .trace-glyph,
	:global(.dark) [data-category='session'] .trace-kind {
		background: #29364b;
		color: #a7c3e8;
	}
</style>
