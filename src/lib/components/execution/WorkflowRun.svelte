<script lang="ts">
	export let runId: string;
	export let running = false;
	function focus() {
		window.dispatchEvent(new CustomEvent('workflow:focus', { detail: runId }));
	}
</script>

<button
	class="my-2 flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs text-gray-500 dark:border-gray-700"
	on:click={focus}
	><span class="size-1.5 rounded-full bg-emerald-600" class:animate-pulse={running}></span>{running
		? 'Task running'
		: 'Task activity'}<span class="text-violet-600">Open run →</span></button
>
