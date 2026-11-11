<script lang="ts">
	import { WEBUI_NAME } from '$lib/stores';
	import { getBackendConfig } from '$lib/apis';
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { recoveryPath } from '$lib/utils/recoveryPath';
	import { createRequestGate } from '$lib/utils/requestGate';
	$: target = recoveryPath($page.url.searchParams.get('redirect'));
	const requests = createRequestGate();
	let checking = false;
	let attempts = 0;
	let timer: ReturnType<typeof setInterval>;
	async function reconnect() {
		if (checking) return;
		const current = requests.begin();
		checking = true;
		try {
			const response = await getBackendConfig();
			if (current() && response?.version) window.location.replace(target);
		} catch {
			/* Keep the conversation URL and login while the service restarts. */
		} finally {
			if (current()) checking = false;
		}
	}
	onMount(() => {
		void reconnect();
		timer = setInterval(() => {
			if (++attempts >= 30) clearInterval(timer);
			else void reconnect();
		}, 3000);
		return () => {
			clearInterval(timer);
			requests.invalidate();
		};
	});
</script>

<div class="flex h-screen w-full items-center justify-center bg-white p-6 dark:bg-gray-900">
	<div class="max-w-md text-center">
		<p class="mb-5 text-sm text-gray-500">{$WEBUI_NAME}</p>
		<h1 class="text-2xl font-semibold">Connection interrupted</h1>
		<p class="mt-4 text-sm leading-6 text-gray-500">
			The local service is unavailable. Reconnecting will return you to your conversation.
		</p>
		<button
			disabled={checking}
			on:click={reconnect}
			class="mt-6 rounded-lg bg-gray-900 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900"
			>{checking ? 'Reconnecting…' : 'Reconnect'}</button
		>
	</div>
</div>
