<script lang="ts">
	import { user } from '$lib/stores';
	import WorkflowCenter from '$lib/components/execution/WorkflowCenter.svelte';
</script>

{#if $user?.role === 'admin'}<WorkflowCenter />{/if}
