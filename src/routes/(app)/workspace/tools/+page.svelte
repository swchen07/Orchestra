<script>
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { config, user } from '$lib/stores';

	import WorkspaceResourceView from '$lib/components/execution/WorkspaceResourceView.svelte';

	onMount(() => {
		if (!$config?.features?.enable_tools && $user?.role !== 'admin') {
			goto('/workspace', { replaceState: true });
		}
	});
</script>

{#if $config?.features?.enable_tools || $user?.role === 'admin'}
	<WorkspaceResourceView kind="mcp" />
{/if}
