<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	onMount(() => {
		void goto('/', { replaceState: true });
	});
</script>

<p class="p-6 text-sm text-gray-500">
	执行框架已整合到正常聊天，正在进入聊天… <a href="/">进入聊天</a>
</p>
