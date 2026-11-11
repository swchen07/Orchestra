<script lang="ts">
	import { onMount, createEventDispatcher, tick } from 'svelte';
	import { getWorkflowRun, type WorkflowRun } from '$lib/apis/execution';
	import Markdown from '$lib/components/chat/Messages/Markdown.svelte';
	import RuntimeMap from './RuntimeMap.svelte';
	import ControlLoop from './ControlLoop.svelte';
	import AgentTrajectory from './AgentTrajectory.svelte';
	import RunUsage from './RunUsage.svelte';
	import WorkspaceReceipts from './WorkspaceReceipts.svelte';
	import { summarizeUsage, usageLabel } from './workflowUsage';
	import XMark from '$lib/components/icons/XMark.svelte';
	import WorkflowIcon from './WorkflowIcon.svelte';
	import { executionCycles, callIdentity, currentCallStatus } from './workflowPresentation';
	import GraphCanvas from './GraphCanvas.svelte';
	import GraphEditor from './GraphEditor.svelte';
	import { hasPlanning, reviewingBinding } from './nodeProgram';
	import { controlWorkflowRun, type WorkflowGraph } from '$lib/apis/execution';
	import { graphChanges } from './workflowView';
	import { roleViews, isLive, statusLabel, duration } from './workflowView';
	export let runId: string;
	export let running = false;
	export let overview = false;
	export let sidebar = false;
	const dispatch = createEventDispatcher<{ close: void }>();
	let panelView = 'map';
	let cycle = '';
	let inspector = false;
	let inspectorElement: HTMLElement;
	$: cycles = run ? executionCycles(run) : [];
	$: selectedStep = Math.max(
		0,
		cycles.findIndex((step) => step.id === cycle)
	);
	$: if (cycles.length && !cycles.some((step) => step.id === cycle)) cycle = cycles[0].id;
	async function inspectCall(id: string) {
		const call = run?.calls.find((c) => c.id === id);
		if (!call) return;
		activeRole = call.participant_id;
		activeCall = id;
		inspector = true;
		view = 'trajectory';
		await tick();
		inspectorElement?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
	}
	export let expanded = overview || running;
	let graphView = 'live';
	let editing = false;
	let draft: WorkflowGraph | null = null;
	let editRevision = 0;
	let editBase: WorkflowGraph | null = null;
	let controlBusy = false;
	let controlError = '';
	let selectedPlanNode = '';
	let workflowScope = '';
	function childScopes(source: WorkflowRun | null): WorkflowRun[] {
		return Object.values(source?.child_workflows ?? {}).flatMap((child) => [
			child,
			...childScopes(child)
		]);
	}
	$: nestedScopes = childScopes(run);
	$: childRun = nestedScopes.find((r) => r.workflow_path === workflowScope);
	async function control(action: string, extra: Record<string, unknown> = {}) {
		clearTimeout(poll);
		const generation = ++requestNumber;
		controlBusy = true;
		controlError = '';
		try {
			const value = await controlWorkflowRun(shownId, {
				action,
				revision: run?.plan?.revision,
				...extra
			});
			if (mounted && generation === requestNumber) run = value;
		} catch (e) {
			controlError = String(e);
		} finally {
			controlBusy = false;
			if (mounted && expanded) poll = setTimeout(load, 1500);
		}
	}
	function editPlan() {
		if (!run?.plan) return;
		draft = structuredClone(run.plan);
		editBase = structuredClone(run.plan);
		editRevision = run.plan.revision ?? 1;
		editing = true;
		graphView = 'plan';
		panelView = 'map';
	}
	async function savePlan() {
		if (!draft || !editBase) return;
		const changes = graphChanges(editBase, draft);
		if (!changes.length) {
			editing = false;
			return;
		}
		await control('patch', { revision: editRevision, changes, reason: 'Edited in current chat' });
		if (!controlError) editing = false;
	}
	function inspectNode(id: string) {
		selectedPlanNode = id;
		const source = childRun ?? run;
		const state = source?.node_states?.[id] as any;
		if (state?.workflow?.path) {
			workflowScope = state.workflow.path;
			return;
		}
		const call = source?.calls.findLast((c) => c.node_id === id);
		if (call) {
			selectRole(call.participant_id);
			void inspectCall(call.id);
		}
	}
	let wasRunning = running;
	let run: WorkflowRun | null = null;
	let shownId = runId;
	let turns: string[] = [];
	let error = '';
	let mounted = false;
	let loading = false;
	let activeRole = 'all';
	let activeCall = '';
	let view = 'trajectory';
	let now = Date.now();
	let poll: ReturnType<typeof setTimeout>;
	let clock: ReturnType<typeof setInterval>;
	let requestNumber = 0;
	$: roles = run ? roleViews(run) : [];
	$: agentUsage = run
		? summarizeUsage(
				run,
				run.calls.filter(
					(c) =>
						(activeRole === 'all' || c.participant_id === activeRole) &&
						(!activeCall || c.id === activeCall)
				)
			)
		: null;
	$: inspectedCall = run?.calls.find((c) => c.id === activeCall);
	$: inspectedIdentity = run && inspectedCall ? callIdentity(run, inspectedCall) : null;
	$: events = (run?.events ?? []).filter(
		(e) =>
			(activeRole === 'all' || e.role_id === activeRole) &&
			(!activeCall || e.call_id === activeCall)
	);
	$: reports = (run?.calls ?? []).filter(
		(c) =>
			(activeRole === 'all' || c.participant_id === activeRole) &&
			(!activeCall || c.id === activeCall) &&
			(c.output || c.error) &&
			c.kind !== 'decision'
	);
	$: compositeReports = (run?.module_reports ?? []).filter(
		(r) => !activeCall && (activeRole === 'all' || r.role_id === activeRole)
	);
	$: reportCount = reports.length + compositeReports.length;
	$: stale =
		!!run &&
		isLive(run.status) &&
		now - (run.heartbeat_at ?? run.updated_at ?? run.created_at * 1000) > 20000;
	async function load() {
		clearTimeout(poll);
		if (controlBusy) return;
		const generation = ++requestNumber;
		loading = !run;
		try {
			const value = await getWorkflowRun(shownId);
			if (!mounted || generation !== requestNumber) return;
			run = value;
			if (workflowScope && !childScopes(value).some((r) => r.workflow_path === workflowScope))
				workflowScope = '';
			if (shownId === runId) turns = [...(value.continuation?.previous_run_ids ?? []), runId];
			error = '';
		} catch (e) {
			if (mounted && generation === requestNumber) error = String(e);
		} finally {
			if (mounted && generation === requestNumber) {
				loading = false;
				if (expanded && (run ? isLive(run.status) : running))
					poll = setTimeout(load, error ? 4000 : 1500);
			}
		}
	}
	async function showTurn(identity: string) {
		editing = false;
		cycle = '';
		inspector = false;
		selectedPlanNode = '';
		shownId = identity;
		run = null;
		activeRole = 'all';
		activeCall = '';
		await load();
	}
	function selectRole(identity: string) {
		activeRole = identity;
		activeCall = '';
	}
	onMount(() => {
		mounted = true;
		void load();
		clock = setInterval(() => (now = Date.now()), 1000);
		return () => {
			mounted = false;
			requestNumber++;
			clearTimeout(poll);
			clearInterval(clock);
		};
	});
	$: if (mounted && wasRunning && !running) {
		wasRunning = false;
		void load();
	}
</script>

<section class="run-workspace" class:in-sidebar={sidebar} aria-label="Workflow run">
	<header class="run-header">
		<div class="run-heading">
			<div class="run-wordmark">
				<WorkflowIcon className="size-5" />
				<h2>Run</h2>
			</div>
			<div class="header-actions">
				<button aria-label="Refresh agent activity" title="Refresh" on:click={load}>↻</button
				>{#if sidebar}<button
						aria-label="Hide run sidebar"
						title="Hide sidebar"
						on:click={() => dispatch('close')}><XMark className="size-4" /></button
					>{/if}
			</div>
		</div>
		<div class="run-title">
			<h3>{run?.template?.name ?? 'Workflow'}</h3>
			<span class="status-badge" data-status={run?.status ?? 'queued'}
				><span aria-hidden="true"
					>{run?.status === 'completed' ? '✓' : run?.status === 'failed' ? '!' : '●'}</span
				>{run ? statusLabel(run.status) : 'Loading'}</span
			>
		</div>
		<div class="run-meta">
			{#if turns.length > 1}<select
					aria-label="Run history"
					value={shownId}
					on:change={(e) => showTurn(e.currentTarget.value)}
					>{#each turns as id, index}<option value={id}
							>Run {String(index + 1).padStart(2, '0')}{id === runId ? ' · Current' : ''}</option
						>{/each}</select
				>{:else}<span>Run 01</span>{/if}
			<span>·</span><span>{roles.length} nodes</span><span>·</span><span
				>{run?.calls.length ?? 0} calls</span
			>
		</div>
		{#if run}<RunUsage {run} />{/if}
		{#if reviewingBinding(run?.plan)}
			<div class="review-summary" role="status">
				<span>Independent review</span>
				<button
					disabled={!run?.review?.call_id}
					title={run?.review?.summary ?? 'Required before delivery'}
					on:click={() => run?.review?.call_id && inspectCall(run.review.call_id)}
					>{(run?.review?.status ?? 'Pending').replaceAll('_', ' ')}</button
				>
			</div>
		{/if}
	</header>
	<nav class="run-tabs" aria-label="Run views">
		{#each [{ id: 'map', label: 'Graph' }, { id: 'trajectory', label: 'Activity' }, { id: 'reports', label: 'Reports' }] as tab}<button
				class:chosen={panelView === tab.id}
				aria-pressed={panelView === tab.id}
				on:click={() => {
					panelView = tab.id;
					if (tab.id !== 'map') view = tab.id;
				}}
				>{tab.label}{#if tab.id === 'reports' && reportCount}<span>{reportCount}</span>{/if}</button
			>{/each}
	</nav>
	<div class="run-scroll">
		{#if error}<p class="notice error" role="alert">Could not refresh activity. {error}</p>{/if}
		{#if stale}<p class="notice waiting" role="status">
				No recent server heartbeat. Activity may be stale.
			</p>{/if}
		{#if loading}<p class="empty-state">Loading the run…</p>{/if}
		{#if run}
			{#if run.error}<p class="notice error" role="alert">{run.error}</p>{/if}
			{#if isLive(run.status) && run.control?.can_edit}
				<div class="run-controls">
					<button disabled={controlBusy} on:click={editPlan}>Edit plan</button>
					{#if run.control.paused}<button
							class="primary"
							disabled={controlBusy || run.control.controller_running || editing}
							on:click={() => control('approve')}
							>{run.execution_policy === 'step' ? 'Run next task' : 'Start / continue'}</button
						>{:else}<button disabled={controlBusy} on:click={() => control('pause')}
							>Pause dispatch</button
						>{/if}
					<select
						aria-label="Run execution policy"
						value={run.execution_policy}
						disabled={controlBusy}
						on:change={(e) => control('policy', { policy: e.currentTarget.value })}
						><option value="automatic">Automatic</option><option value="review"
							>Review changes</option
						><option value="step">Step</option></select
					>
					{#if hasPlanning(run.plan)}<button
							disabled={controlBusy || run.control.controller_running}
							on:click={() => control('replan')}>Ask AI to adjust</button
						>{/if}<button class="danger" disabled={controlBusy} on:click={() => control('stop')}
						>Stop</button
					>
				</div>
			{/if}
			{#if run.control?.paused && isLive(run.status)}<p class="notice waiting" role="status">
					{run.control.controller_running
						? 'AI is preparing the plan.'
						: 'Ready for review. Edit pending tasks, then continue.'} Running branches continue independently.
				</p>{/if}
			{#if controlError}<p class="notice error" role="alert">{controlError}</p>{/if}
			{#if run.control?.errors.length}
				{#if run.status === 'completed'}
					<details class="notice waiting">
						<summary>Recovered planning issue</summary>
						<p class="mt-1">{run.control.errors.at(-1)}</p>
					</details>
				{:else}
					<p class="notice waiting">{run.control.errors.at(-1)}</p>
				{/if}
			{/if}
			{#if panelView === 'map'}
				{#if hasPlanning(run.plan)}<details class="logic-reference">
						<summary>How the controller works</summary><ControlLoop {run} />
					</details>{/if}
				<section class="map-section" aria-label="Execution map">
					<div class="section-heading">
						<h3>Execution map</h3>
						{#if nestedScopes.length}<select
								aria-label="Workflow scope"
								class="max-w-52 rounded-lg border bg-transparent px-2 py-1 text-xs"
								bind:value={workflowScope}
								><option value="">Main Workflow</option>{#each nestedScopes as child}<option
										value={child.workflow_path}
										>{child.workflow_path} · {child.template?.name ?? 'Workflow'}</option
									>{/each}</select
							>{/if}
						<div class="view-toggle">
							<button aria-pressed={graphView === 'live'} on:click={() => (graphView = 'live')}
								>Execution</button
							><button aria-pressed={graphView === 'plan'} on:click={() => (graphView = 'plan')}
								>Definition</button
							>
						</div>
					</div>
					{#if childRun?.plan}
						<p class="map-caption">
							Nested Workflow · {statusLabel(childRun.status)}. Totals summarize the same leaf calls
							already included in the main Workflow.
						</p>
						{#if childRun.control?.paused}<button
								class="primary"
								on:click={() =>
									control('approve', {
										workflow_path: workflowScope,
										revision: childRun?.plan?.revision
									})}>Continue child Workflow</button
							>{/if}
						{#if mounted}<GraphCanvas
								plan={childRun.plan}
								run={childRun}
								planned={graphView === 'plan'}
								on:select={(e) => inspectNode(e.detail)}
							/>{/if}
						{#each childRun.plan.nodes.filter((n) => n.op === 'checkpoint' && childRun?.node_states?.[n.id]?.status === 'waiting') as checkpoint}<form
								on:submit|preventDefault={(e) =>
									control('answer', {
										workflow_path: workflowScope,
										node_id: checkpoint.id,
										value: new FormData(e.currentTarget).get('answer')
									})}
							>
								<label class="text-sm"
									>{checkpoint.question}<input
										name="answer"
										required
										class="my-2 w-full rounded-lg border bg-transparent p-2"
									/></label
								><button type="submit">Send answer</button>
							</form>{/each}
					{:else if graphView === 'live' && run.plan && !hasPlanning(run.plan)}
						<p class="map-caption">
							Execution follows graph dependencies. Select a module to inspect its activity and
							report.
						</p>
						{#if mounted}<GraphCanvas
								plan={run.plan}
								{run}
								on:select={(e) => inspectNode(e.detail)}
							/>{/if}
					{:else if graphView === 'live'}
						<div class="controller-rail" aria-label="Controller decision timeline">
							{#each cycles as step}<button
									aria-pressed={cycle === step.id}
									aria-label={`Controller step ${step.index}: ${step.label}`}
									on:click={() => (cycle = step.id)}
									><span>{step.index}</span><strong>{step.label}</strong><small
										>{step.calls.length} tasks</small
									></button
								>{/each}
						</div>
						<div class="step-navigation">
							<button
								aria-label="Previous controller step"
								disabled={selectedStep === 0}
								on:click={() => (cycle = cycles[selectedStep - 1].id)}>←</button
							>
							<div>
								<strong
									>Step {selectedStep + 1} of {cycles.length} · {cycles[selectedStep]
										?.label}</strong
								><small>Controller decisions advance independently of task completion.</small>
							</div>
							<button
								aria-label="Next controller step"
								disabled={selectedStep === cycles.length - 1}
								on:click={() => (cycle = cycles[selectedStep + 1].id)}>→</button
							>
						</div>
						<p class="map-caption">
							{isLive(run.status) ? 'Live task states' : 'Final task states'} · Selecting a step focuses
							its branches, without rewinding status.
						</p>
						{#if mounted}<RuntimeMap
								{run}
								{cycle}
								{now}
								on:select={(e) => inspectCall(e.detail)}
							/>{/if}
					{:else}
						<p class="map-caption">
							Version {run.plan?.revision ?? 1} · The reusable task structure. Execution shows calls that
							actually happened.
						</p>
						{#if editing && draft}<div class="plan-editor">
								<GraphEditor
									bind:value={draft}
									modules={run.modules ?? []}
									states={run.node_states ?? {}}
								/>
								<div class="editor-actions">
									<button on:click={() => (editing = false)}>Cancel</button><button
										class="primary"
										disabled={controlBusy}
										on:click={savePlan}>Apply to this chat</button
									>
								</div>
							</div>{/if}
						{#if run.plan && mounted}<GraphCanvas
								plan={editing && draft ? draft : run.plan}
								{run}
								planned
								on:select={(e) => inspectNode(e.detail)}
							/>{:else}<p class="empty-state">
								This earlier run recorded calls only. Its execution is available in Execution.
							</p>{/if}
						{#if selectedPlanNode && run.plan}{@const node = run.plan.nodes.find(
								(n) => n.id === selectedPlanNode
							)}{#if node}<div class="task-detail">
									<strong>{node.label ?? node.id}</strong>
									<p>{node.instruction ?? node.question ?? node.op}</p>
									{#if node.op === 'checkpoint' && run.node_states?.[node.id]?.status === 'waiting'}<form
											on:submit|preventDefault={(e) =>
												control('answer', {
													node_id: node.id,
													value: new FormData(e.currentTarget).get('answer')
												})}
										>
											<input name="answer" required aria-label="Checkpoint answer" /><button
												class="primary">Continue</button
											>
										</form>{/if}
								</div>{/if}{/if}
					{/if}
					{#if (run.plan_revisions?.length ?? 0) > 1}<details class="plan-history">
							<summary>Plan history · {run.plan_revisions?.length} versions</summary
							>{#each run.plan_revisions ?? [] as revision}<p>
									<b>v{revision.revision}</b> · {revision.source}<br />{revision.reason}
								</p>{/each}
						</details>{/if}
				</section>
				{#if !inspector}<div class="inspect-placeholder">
						<h3>Module details</h3>
						<p>Select an agent to inspect its trace and report.</p>
					</div>{/if}
			{/if}
			{#if panelView !== 'map' || inspector}
				<section class="inspector" bind:this={inspectorElement} aria-label="Module details">
					<div class="section-heading">
						<h3>
							{panelView === 'map'
								? 'Module details'
								: panelView === 'reports'
									? 'Intermediate reports'
									: 'Module activity'}
						</h3>
						{#if inspector}<button
								class="quiet"
								aria-label="Clear agent selection"
								on:click={() => {
									inspector = false;
									selectRole('all');
								}}>All modules</button
							>{/if}
					</div>
					<div class="role-filters" aria-label="Filter agent activity">
						<button aria-pressed={activeRole === 'all'} on:click={() => selectRole('all')}
							>All</button
						>{#each roles as role}<button
								aria-pressed={activeRole === role.id}
								on:click={() => selectRole(role.id)}
								><span class="state-dot" data-status={role.status}></span>{role.name}</button
							>{/each}
					</div>
					{#if activeRole !== 'all'}{@const role = roles.find(
							(r) => r.id === activeRole
						)}{#if role}<div class="agent-identity">
								<div>
									<strong>{role.name}</strong><span
										>{inspectedIdentity?.framework ?? role.framework} · {inspectedIdentity?.model ??
											role.model}</span
									>
								</div>
								{#if agentUsage}<span class="agent-tokens">{usageLabel(agentUsage)} tokens</span
									>{/if}
								<span
									class="status-badge"
									data-status={run && inspectedCall
										? currentCallStatus(run, inspectedCall)
										: role.status}
									>{statusLabel(
										run && inspectedCall ? currentCallStatus(run, inspectedCall) : role.status
									)}</span
								>{#if role.calls.length > 1}<select aria-label="Module call" bind:value={activeCall}
										><option value="">All calls</option>{#each role.calls as call, index}<option
												value={call.id}
												>Call {index + 1} · {statusLabel(currentCallStatus(run, call))}</option
											>{/each}</select
									>{/if}
							</div>{/if}{/if}
					{#if panelView === 'map'}<div
							class="inspection-tabs"
							role="tablist"
							aria-label="Module inspection"
						>
							<button
								role="tab"
								aria-selected={view === 'trajectory'}
								on:click={() => (view = 'trajectory')}
								>Trajectory <span>{events.length}</span></button
							><button
								role="tab"
								aria-selected={view === 'reports'}
								on:click={() => (view = 'reports')}>Reports <span>{reportCount}</span></button
							>
						</div>{/if}
					{#if view === 'trajectory'}
						{#if run.legacy_trace}<p class="map-caption">
								Only call records were captured for this earlier run.
							</p>{/if}
						{#if run.events_omitted}<p class="notice waiting">
								{run.events_omitted} events exceeded the trace limit. Reports are retained.
							</p>{/if}
						<AgentTrajectory
							{run}
							role={activeRole}
							callId={activeCall}
							on:report={(e) => {
								activeRole = e.detail.role;
								activeCall = e.detail.call;
								view = 'reports';
								if (panelView !== 'map') panelView = 'reports';
							}}
						/>
					{:else}
						<WorkspaceReceipts
							workspaces={run.workspace_states ?? {}}
							operations={run.host_operations ?? []}
						/>
						<div class="reports" role="tabpanel" aria-label="Intermediate reports">
							{#each compositeReports as report (report.id)}
								<details class="report">
									<summary
										><strong>{report.name}</strong><span>Workflow output</span><span
											>Composite · no additional model charge</span
										></summary
									>
									<p class="report-ref">{report.id}</p>
									<pre>{report.output}</pre>
								</details>
							{/each}
							{#each reports as call (call.id)}<details class="report" open={activeRole !== 'all'}>
									<summary
										><strong>{callIdentity(run, call).name}</strong><span
											>{callIdentity(run, call).task}</span
										><span
											>Call {run.calls.findIndex((c) => c.id === call.id) + 1} · {statusLabel(
												call.status
											)}</span
										></summary
									>
									<p class="report-ref">{call.report_id ?? `${run.id}/${call.id}`}</p>
									<details class="assigned-task">
										<summary>Assigned task</summary>
										<pre>{call.input}</pre>
									</details>
									<div class="prose prose-sm min-w-0 max-w-none break-words dark:prose-invert">
										<Markdown
											id={`workflow-${call.id}`}
											content={call.output ?? call.error ?? ''}
											allowHtml={false}
											allowEmbeds={false}
											allowRemoteContent={false}
											editCodeBlock={false}
										/>
									</div>
									<WorkspaceReceipts
										workspaces={call.execution?.__workspace
											? { [call.execution.__workspace.id]: call.execution.__workspace }
											: {}}
										artifacts={call.execution?.__artifacts ?? []}
									/>
									{#if call.usage}<p class="report-ref">
											Input: {call.usage.prompt_tokens ?? 'Unknown'} · Output: {call.usage
												.completion_tokens ?? 'Unknown'}
										</p>{/if}
								</details>{:else}{#if !compositeReports.length}<p class="empty-state">
										{isLive(run.status)
											? 'Reports appear as calls finish.'
											: 'No work report for this selection. Decisions are available in Activity.'}
									</p>{/if}{/each}
						</div>
					{/if}
				</section>
			{/if}
		{/if}
	</div>
	{#if run && isLive(run.status)}<footer class="run-footer">
			<span class="state-dot" data-status="running"></span>Live activity · Last update {duration(
				now - (run.last_event_at ?? run.updated_at ?? now)
			)} ago
		</footer>{/if}
</section>

<style>
	.review-summary {
		display: flex;
		align-items: center;
		gap: 8px;
		margin-top: 9px;
		font-size: 10px;
		color: var(--run-muted);
	}
	.review-summary button {
		border-radius: 6px;
		background: var(--run-border);
		color: var(--run-text);
		padding: 3px 7px;
		text-transform: capitalize;
	}
	.run-workspace {
		--run-text: #1c2b43;
		--run-muted: #6d7b90;
		--run-border: #e1e6ef;
		--run-surface: #f7f9fc;
		--run-canvas: #fff;
		--run-accent: #6951bd;
		display: flex;
		flex-direction: column;
		height: 100%;
		min-height: 0;
		min-width: 0;
		background: var(--run-surface);
		color: var(--run-text);
		font-size: 12px;
		line-height: 1.5;
		border-left: 1px solid var(--run-border);
	}
	.run-header {
		padding: 19px 20px 14px;
		flex-shrink: 0;
		background: var(--run-canvas);
	}
	.run-heading {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		margin-bottom: 16px;
	}
	.run-wordmark {
		display: flex;
		align-items: center;
		gap: 10px;
		color: #294d7d;
	}
	.run-wordmark h2 {
		font-size: 18px;
		font-weight: 650;
		letter-spacing: -0.025em;
		color: var(--run-text);
	}
	.header-actions {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.header-actions button {
		display: grid;
		place-items: center;
		width: 28px;
		height: 28px;
		font-size: 20px;
		color: var(--run-muted);
		border-radius: 7px;
	}
	.header-actions button:hover {
		background: var(--run-surface);
		color: var(--run-text);
	}
	.run-title {
		display: flex;
		align-items: center;
		gap: 10px;
		flex-wrap: wrap;
	}
	.run-title h3 {
		font-size: 15px;
		font-weight: 650;
		letter-spacing: -0.015em;
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.status-badge {
		display: inline-flex;
		align-items: center;
		gap: 5px;
		border-radius: 6px;
		padding: 3px 7px;
		font-size: 10px;
		font-weight: 600;
		white-space: nowrap;
		background: #e8edf4;
		color: #69798f;
	}
	.status-badge[data-status='completed'] {
		background: #e0f2eb;
		color: #137663;
	}
	.status-badge[data-status='running'],
	.status-badge[data-status='waiting'],
	.status-badge[data-status='queued'] {
		background: #fff0d6;
		color: #986419;
	}
	.status-badge[data-status='failed'],
	.status-badge[data-status='interrupted'],
	.status-badge[data-status='completed_with_errors'] {
		background: #fae8ed;
		color: #b03959;
	}
	.run-meta {
		display: flex;
		align-items: center;
		gap: 7px;
		flex-wrap: wrap;
		margin-top: 7px;
		color: var(--run-muted);
		font-size: 11px;
	}
	.run-meta select {
		border: 0;
		background: transparent;
		max-width: 160px;
		padding: 0 15px 0 0;
		font-size: 11px;
		color: inherit;
	}
	.run-tabs {
		display: flex;
		gap: 22px;
		padding: 0 20px;
		border-bottom: 1px solid var(--run-border);
		flex-shrink: 0;
		background: var(--run-canvas);
	}
	.run-tabs button {
		display: flex;
		align-items: center;
		gap: 5px;
		padding: 11px 3px 10px;
		font-size: 12px;
		font-weight: 550;
		border-bottom: 2px solid transparent;
		color: var(--run-muted);
	}
	.run-tabs button.chosen {
		color: var(--run-accent);
		border-color: var(--run-accent);
	}
	.run-tabs button span {
		font-size: 10px;
		opacity: 0.7;
	}
	.run-scroll {
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		overscroll-behavior: contain;
		scrollbar-width: thin;
	}
	.map-section {
		padding: 18px 20px;
		background: var(--run-surface);
	}
	.section-heading {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 10px;
		margin-bottom: 12px;
	}
	.section-heading h3,
	.inspect-placeholder h3 {
		font-size: 13px;
		font-weight: 650;
	}
	.view-toggle {
		display: flex;
		gap: 2px;
		padding: 2px;
		background: var(--run-canvas);
		border: 1px solid var(--run-border);
		border-radius: 7px;
	}
	.view-toggle button {
		padding: 4px 7px;
		font-size: 10px;
		border-radius: 4px;
		color: var(--run-muted);
	}
	.view-toggle button[aria-pressed='true'] {
		background: #ede8fa;
		color: var(--run-accent);
	}
	.logic-reference {
		font-size: 11px;
		color: var(--run-muted);
		border-bottom: 1px solid var(--run-border);
	}
	.logic-reference > summary {
		padding: 12px 20px;
		cursor: pointer;
	}
	.controller-rail {
		display: flex;
		overflow-x: auto;
		gap: 0;
		padding: 5px 0 15px;
		scrollbar-width: thin;
	}
	.controller-rail button {
		display: flex;
		flex-direction: column;
		gap: 5px;
		align-items: center;
		min-width: 70px;
		flex: 1;
		position: relative;
		padding: 0 10px;
		font-size: 10px;
		color: var(--run-muted);
	}
	.controller-rail button:after {
		content: '';
		position: absolute;
		height: 1px;
		background: var(--run-border);
		top: 13px;
		left: calc(50% + 17px);
		width: calc(100% - 34px);
	}
	.controller-rail button:last-child:after {
		display: none;
	}
	.controller-rail button > span {
		display: grid;
		place-items: center;
		position: relative;
		z-index: 1;
		border: 1px solid var(--run-border);
		background: var(--run-canvas);
		width: 27px;
		height: 27px;
		border-radius: 50%;
		font-size: 10px;
	}
	.controller-rail button[aria-pressed='true'] > span {
		background: #765ab1;
		color: white;
		border-color: #765ab1;
	}
	.controller-rail strong {
		font-size: 10px;
		font-weight: 550;
	}
	.controller-rail small {
		font-size: 9px;
		opacity: 0.8;
	}
	.step-navigation {
		display: flex;
		align-items: center;
		gap: 12px;
		justify-content: space-between;
		margin: 7px 0 10px;
	}
	.step-navigation > button {
		border: 1px solid var(--run-border);
		border-radius: 7px;
		width: 30px;
		height: 30px;
		color: var(--run-text);
		background: var(--run-canvas);
		flex-shrink: 0;
	}
	.step-navigation > button:disabled {
		opacity: 0.3;
		cursor: default;
	}
	.step-navigation > div {
		display: flex;
		flex-direction: column;
		gap: 3px;
		text-align: center;
	}
	.step-navigation strong {
		font-size: 12px;
		font-weight: 600;
	}
	.step-navigation small {
		font-size: 10px;
		color: var(--run-muted);
	}

	.map-caption {
		font-size: 10px;
		line-height: 1.65;
		color: var(--run-muted);
		margin: 8px 0 12px;
	}
	.plan-history {
		font-size: 11px;
		margin-top: 14px;
		color: var(--run-muted);
	}
	.plan-history summary {
		cursor: pointer;
	}
	.plan-history p {
		padding: 7px 0 7px 12px;
		line-height: 1.7;
		border-left: 2px solid #d7c9ed;
		margin-top: 8px;
	}
	.plan-history b {
		color: var(--run-accent);
		font-weight: 600;
	}
	.inspect-placeholder {
		padding: 16px 20px 22px;
		border-top: 1px solid var(--run-border);
		background: var(--run-canvas);
	}
	.inspect-placeholder p {
		margin-top: 5px;
		font-size: 11px;
		color: var(--run-muted);
	}
	.inspector {
		padding: 18px 20px;
		background: var(--run-canvas);
		border-top: 1px solid var(--run-border);
	}
	.quiet {
		font-size: 10px;
		color: var(--run-accent);
	}
	.role-filters {
		display: flex;
		flex-wrap: wrap;
		gap: 5px;
		margin-bottom: 13px;
	}
	.role-filters button {
		display: flex;
		align-items: center;
		gap: 5px;
		border: 1px solid transparent;
		border-radius: 6px;
		padding: 5px 7px;
		font-size: 11px;
		color: var(--run-muted);
	}
	.role-filters button[aria-pressed='true'] {
		background: #eee9fa;
		color: var(--run-accent);
		border-color: #e0d7f1;
	}
	.state-dot {
		display: inline-block;
		flex-shrink: 0;
		width: 6px;
		height: 6px;
		border-radius: 50%;
		background: #9aa8b7;
	}
	.state-dot[data-status='running'],
	.state-dot[data-status='waiting'] {
		background: #c0892b;
	}
	.state-dot[data-status='running'] {
		animation: breathe 1.7s infinite;
	}
	.state-dot[data-status='completed'] {
		background: #278d75;
	}
	.state-dot[data-status='failed'],
	.state-dot[data-status='interrupted'] {
		background: #c75470;
	}
	.agent-tokens {
		font-size: 10px;
		color: var(--run-muted);
		white-space: nowrap;
	}
	.agent-identity {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 9px;
		padding: 11px 12px;
		border: 1px solid var(--run-border);
		border-radius: 8px;
		margin-bottom: 12px;
		background: var(--run-surface);
	}
	.agent-identity > div {
		display: flex;
		flex-direction: column;
		gap: 3px;
		min-width: 0;
		flex: 1;
	}
	.agent-identity strong {
		font-weight: 650;
	}
	.agent-identity > div > span {
		font-size: 10px;
		overflow-wrap: anywhere;
		color: var(--run-muted);
	}
	.agent-identity select {
		width: 100%;
		border: 0;
		background: transparent;
		font-size: 11px;
		color: var(--run-muted);
	}
	.inspection-tabs {
		display: flex;
		gap: 18px;
		border-bottom: 1px solid var(--run-border);
		margin-bottom: 12px;
	}
	.inspection-tabs button {
		font-size: 11px;
		padding: 7px 0;
		border-bottom: 2px solid transparent;
		color: var(--run-muted);
	}
	.inspection-tabs button[aria-selected='true'] {
		color: var(--run-accent);
		border-color: var(--run-accent);
	}
	.inspection-tabs span {
		opacity: 0.65;
		margin-left: 3px;
	}
	.report-ref {
		font-size: 9px;
		color: var(--run-muted);
		margin-bottom: 8px;
		overflow-wrap: anywhere;
	}
	.assigned-task pre {
		white-space: pre-wrap;
		overflow-wrap: anywhere;
		font-size: 11px;
		line-height: 1.7;
		background: var(--run-surface);
		padding: 10px;
		border-radius: 7px;
	}
	.report {
		padding: 13px 0;
		border-bottom: 1px solid var(--run-border);
	}
	.report > summary {
		display: flex;
		gap: 7px;
		flex-wrap: wrap;
		cursor: pointer;
		align-items: center;
		font-size: 12px;
	}
	.report > summary > span {
		font-size: 10px;
		color: var(--run-muted);
	}
	.report-ref {
		margin-top: 10px;
	}
	.assigned-task {
		font-size: 10px;
		color: var(--run-muted);
		margin: 10px 0;
	}
	.assigned-task summary {
		cursor: pointer;
		margin-bottom: 7px;
	}
	.report :global(.prose) {
		font-size: 12px;
		line-height: 1.8;
		color: var(--run-text);
	}
	.report :global(.prose pre) {
		overflow-wrap: anywhere;
		max-width: 100%;
	}
	.empty-state {
		font-size: 12px;
		color: var(--run-muted);
		padding: 22px 20px;
	}
	.notice {
		font-size: 11px;
		line-height: 1.7;
		padding: 10px 12px;
		border: 1px solid;
		border-radius: 7px;
		margin: 12px 16px;
		overflow-wrap: anywhere;
	}
	.notice.error {
		background: #fff0f3;
		border-color: #f4d6e0;
		color: #a03c5b;
	}
	.notice.waiting {
		background: #fff8e8;
		border-color: #f0dfbc;
		color: #986921;
	}
	.run-controls {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
		padding: 14px 16px;
		border-bottom: 1px solid var(--run-border);
	}
	.run-controls button,
	.run-controls select,
	.editor-actions button,
	.task-detail form button {
		font-size: 10px;
		padding: 5px 7px;
		border: 1px solid var(--run-border);
		border-radius: 6px;
		background: var(--run-canvas);
		color: var(--run-text);
	}
	.run-controls .danger {
		color: #b74965;
		border-color: transparent;
	}
	.primary {
		background: var(--run-accent) !important;
		color: white !important;
		border-color: var(--run-accent) !important;
	}
	.run-controls button:disabled,
	.editor-actions button:disabled {
		opacity: 0.5;
	}
	.plan-editor {
		padding: 12px 0;
		margin-bottom: 14px;
		border-top: 1px solid var(--run-border);
		border-bottom: 1px solid var(--run-border);
	}
	.editor-actions {
		display: flex;
		justify-content: flex-end;
		gap: 8px;
		margin-top: 12px;
	}
	.task-detail {
		padding: 12px;
		background: var(--run-canvas);
		border: 1px solid var(--run-border);
		border-radius: 8px;
		margin-top: 12px;
	}
	.task-detail p {
		white-space: pre-wrap;
		font-size: 11px;
		color: var(--run-muted);
		margin-top: 5px;
	}
	.task-detail form {
		display: flex;
		gap: 8px;
		margin-top: 10px;
	}
	.task-detail input {
		min-width: 0;
		flex: 1;
		border: 1px solid var(--run-border);
		border-radius: 6px;
		background: transparent;
		padding: 6px;
	}
	.run-footer {
		display: flex;
		align-items: center;
		gap: 6px;
		flex-shrink: 0;
		padding: 9px 20px;
		border-top: 1px solid var(--run-border);
		background: var(--run-canvas);
		color: var(--run-muted);
		font-size: 10px;
	}
	button:focus-visible,
	select:focus-visible,
	summary:focus-visible {
		outline: 2px solid var(--run-accent);
		outline-offset: 3px;
	}
	:global(.dark) .run-workspace {
		--run-text: #dfe5f0;
		--run-muted: #98a5bc;
		--run-border: #30394b;
		--run-surface: #151d2a;
		--run-canvas: #1b2433;
		--run-accent: #baa4ee;
	}
	.run-workspace:not(.in-sidebar) {
		height: auto;
		min-height: 500px;
		max-height: 85vh;
		border: 1px solid var(--run-border);
		border-radius: 12px;
		overflow: hidden;
	}
	:global(.dark) .status-badge[data-status='completed'] {
		background: #193d34;
		color: #9dd5c4;
	}
	:global(.dark) .status-badge[data-status='running'],
	:global(.dark) .status-badge[data-status='waiting'] {
		background: #493720;
		color: #ebc47c;
	}
	:global(.dark) .status-badge[data-status='failed'],
	:global(.dark) .status-badge[data-status='completed_with_errors'] {
		background: #472b37;
		color: #eca6b9;
	}
	:global(.dark) .view-toggle button[aria-pressed='true'],
	:global(.dark) .role-filters button[aria-pressed='true'] {
		background: #322846;
		border-color: #51406d;
	}
	:global(.dark) .notice.error {
		background: #38242e;
		border-color: #613749;
		color: #ecadbf;
	}
	:global(.dark) .notice.waiting {
		background: #382e20;
		border-color: #5d4b30;
		color: #e2bd85;
	}
	:global(.dark) .run-wordmark {
		color: #a7bede;
	}
	@keyframes breathe {
		50% {
			opacity: 0.35;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.state-dot[data-status='running'] {
			animation: none;
		}
	}
	@media (max-width: 420px) {
		.run-header {
			padding: 16px 14px 12px;
		}
		.map-section,
		.inspector {
			padding: 16px 14px;
		}
		.run-tabs {
			padding: 0 14px;
		}
		.run-title h3 {
			font-size: 14px;
		}
	}
</style>
