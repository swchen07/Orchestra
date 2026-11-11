<script lang="ts">
	import { user } from '$lib/stores';
	import ConnectionCenter from '$lib/components/execution/ConnectionCenter.svelte';
</script>

{#if $user?.role === 'admin'}<ConnectionCenter />{:else}<p class="p-6">
		连接配置需要管理员权限。
	</p>{/if}
