<script lang="ts">
	import { user } from '$lib/stores';
	import Models from '$lib/components/workspace/Models.svelte';
</script>

{#if $user?.role === 'admin'}
	<div class="mx-auto max-w-6xl p-5 md:p-8">
		<h1 class="text-xl font-semibold">Model editor</h1>
		<p class="mb-6 mt-2 text-sm leading-6 text-gray-500">
			Create model configurations with custom instructions and parameters.
		</p>
		<Models />
	</div>
{:else}<Models />{/if}
