<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { CallableModule } from '$lib/apis/execution';
	import ComposerSelect from './ComposerSelect.svelte';
	export let modules: CallableModule[] = [];
	export let value = '';
	export let label = 'Module';
	export let disabled = false;
	export let showContract = true;
	const dispatch = createEventDispatcher<{ change: string }>();
	let picker: ComposerSelect;
	$: selected = modules.find((module) => module.id === value);
	const fields = (schema: unknown) =>
		Object.entries((schema as any)?.properties ?? {})
			.map(([name, field]) => name + ': ' + ((field as any)?.type ?? 'any'))
			.join(', ') || 'No required fields';
	export const open = () => picker?.open();
</script>

<div class="min-w-0 space-y-1">
	<ComposerSelect
		bind:this={picker}
		{label}
		{value}
		{disabled}
		placeholder="Choose a module"
		options={modules.map((module) => ({
			value: module.id,
			label: module.name,
			description:
				module.module_type === 'workflow'
					? 'Workflow · saved public interface'
					: [
							module.framework_id,
							module.provider_name,
							module.profile_id ? 'Preset' : '',
							module.effort
						]
							.filter(Boolean)
							.join(' · ')
		}))}
		on:change={(event) => {
			value = event.detail;
			dispatch('change', value);
		}}
	/>
	{#if showContract && selected?.module_type === 'workflow'}
		<details class="text-xs text-gray-500">
			<summary class="cursor-pointer">Public interface · {selected.name}</summary>
			<p class="mt-2">Inputs: {fields(selected.input_schema)}</p>
			<p class="mt-1">Outputs: {fields(selected.output_schema)}</p>
			{#each selected.configuration?.roles ?? [] as role}
				<p class="mt-1">
					{role.name}: {modules.find((m) => m.id === selected?.configuration?.bindings[role.id])
						?.name ??
						selected.configuration?.bindings[role.id] ??
						'Project binding'}
				</p>
			{/each}
			<a class="mt-2 inline-block text-blue-600" href="/workspace/workflows"
				>Open workflow configuration</a
			>
		</details>
	{/if}
</div>
