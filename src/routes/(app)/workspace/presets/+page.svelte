<script lang="ts">
	import { user } from '$lib/stores';
	import PresetCenter from '$lib/components/execution/PresetCenter.svelte';
</script>

{#if $user?.role === 'admin'}<PresetCenter />{/if}
