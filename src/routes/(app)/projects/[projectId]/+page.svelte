<script lang="ts">
	import { page } from '$app/stores';
	import ProjectDashboard from '$lib/components/execution/ProjectDashboard.svelte';
</script>

{#if $page.params.projectId}{#key $page.params.projectId}<ProjectDashboard
			projectId={$page.params.projectId}
		/>{/key}{/if}
