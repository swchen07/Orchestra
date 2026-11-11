<script lang="ts">
	export let className = 'size-4';
	export let panel = false;
</script>

<svg
	class={className}
	viewBox="0 0 24 24"
	fill="none"
	stroke="currentColor"
	stroke-width="1.6"
	stroke-linecap="round"
	stroke-linejoin="round"
	aria-hidden="true"
>
	{#if panel}<rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M15 4v16" />{:else}<rect
			x="9"
			y="2.5"
			width="6"
			height="5"
			rx="1.5"
		/><rect x="2" y="16.5" width="6" height="5" rx="1.5" /><rect
			x="16"
			y="16.5"
			width="6"
			height="5"
			rx="1.5"
		/><path d="M12 7.5v5M5 16.5v-4h14v4" />{/if}
</svg>
