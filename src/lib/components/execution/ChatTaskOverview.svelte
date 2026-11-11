<script lang="ts">
	import { onMount } from 'svelte';
	import { showControls } from '$lib/stores';
	import {
		initializeWorkflowPanel,
		setWorkflowPanel,
		showWorkflowPanel
	} from '$lib/stores/workflowPanel';
	import { branchWorkflow } from './workflowView';
	import WorkflowActivity from './WorkflowActivity.svelte';
	import Drawer from '$lib/components/common/Drawer.svelte';
	import ResizableSidePanel from '$lib/components/common/ResizableSidePanel.svelte';
	export let history;
	let selected = '',
		previous = '';
	let mounted = false,
		wide = false,
		width = 560;
	$: current = branchWorkflow(history);
	$: if (current?.runId && current.runId !== previous) {
		previous = current.runId;
		selected = current.runId;
	}
	$: if (!current) {
		previous = '';
		selected = '';
	}
	function close() {
		setWorkflowPanel(false);
	}
	onMount(() => {
		initializeWorkflowPanel();
		if (current && $showWorkflowPanel) {
			showControls.set(false);
			try {
				localStorage.setItem('showControls', 'false');
			} catch {
				/* Optional preference. */
			}
		}
		try {
			if (!localStorage.getItem('workflowSidebarWidth'))
				localStorage.setItem('workflowSidebarWidth', '560');
		} catch {
			/* Optional preference. */
		}
		const resize = () => {
			wide = window.innerWidth >= 1180;
		};
		resize();
		mounted = true;
		const focus = (event: Event) => {
			selected = (event as CustomEvent<string>).detail;
			showControls.set(false);
			setWorkflowPanel(true);
		};
		const controls = showControls.subscribe((value) => {
			if (value) setWorkflowPanel(false);
		});
		window.addEventListener('workflow:focus', focus);
		window.addEventListener('resize', resize);
		return () => {
			controls();
			window.removeEventListener('workflow:focus', focus);
			window.removeEventListener('resize', resize);
		};
	});
</script>

{#if mounted && selected}
	{#if wide}
		<ResizableSidePanel
			open={$showWorkflowPanel}
			bind:width
			minWidth={440}
			maxWidth={null}
			minSiblingWidth={400}
			storageKey="workflowSidebarWidth"
			resizerId="workflow-resizer"
			className="workflow-side-panel h-full min-h-0 z-20"
			onClose={close}
		>
			<aside class="h-full min-h-0" aria-label="Workflow run sidebar" id="workflow-run-sidebar">
				{#key selected}<WorkflowActivity
						runId={selected}
						running={selected === current?.runId && current.running}
						overview
						sidebar
						on:close={close}
					/>{/key}
			</aside>
		</ResizableSidePanel>
	{:else}
		<Drawer
			show={$showWorkflowPanel}
			onClose={close}
			onRequestClose={close}
			panelStyle="margin-left:auto;width:min(100vw,600px);height:100dvh;"
			className="!bg-white dark:!bg-gray-900"
		>
			<aside class="h-[100dvh] min-h-0" aria-label="Workflow run sidebar" id="workflow-run-sidebar">
				{#key selected}<WorkflowActivity
						runId={selected}
						running={selected === current?.runId && current.running}
						overview
						sidebar
						on:close={close}
					/>{/key}
			</aside>
		</Drawer>
	{/if}
{/if}
