<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import Check from '$lib/components/icons/Check.svelte';

	export let label: string;
	export let value = '';
	export let options: { value: string; label: string; disabled?: boolean; description?: string }[] =
		[];
	export let placeholder = '';
	export let disabled = false;
	export let prominent = false;
	export let searchable = false;
	let show = false;
	let query = '';
	const dispatch = createEventDispatcher<{ change: string }>();
	$: selected = options.find((option) => option.value === value);
	$: text = (!value && placeholder) || selected?.label || placeholder || label;
	$: matches = options.filter((option) =>
		`${option.label} ${option.description ?? ''}`.toLowerCase().includes(query.toLowerCase())
	);
	export function open() {
		if (!disabled) show = true;
	}
</script>

<Dropdown
	bind:show
	side="top"
	align="end"
	visualViewportAware
	contentClass="rounded-2xl border border-gray-100 bg-white p-1.5 shadow-xl dark:border-gray-800 dark:bg-gray-850"
	maxHeight="min(24rem, calc(100dvh - 2rem))"
	onOpenChange={() => (query = '')}
>
	<button
		type="button"
		aria-label={label}
		title={`${label}: ${selected?.label ?? text}`}
		{disabled}
		class="composer-choice"
		class:prominent
	>
		<span class="choice-value">{text}</span>
		<ChevronDown className="size-3 shrink-0 text-gray-400" strokeWidth="1.5" />
	</button>
	<div slot="content" class="w-64 max-w-[calc(100vw-2rem)]">
		<div class="px-2.5 pb-2 pt-1.5 text-xs font-medium text-gray-500">{label}</div>
		{#if searchable && options.length > 6}
			<input
				aria-label={`Search ${label.toLowerCase()}`}
				placeholder="Search…"
				bind:value={query}
				class="mb-1 w-full rounded-lg border-0 bg-gray-50 px-2.5 py-2 text-sm outline-none focus:ring-1 focus:ring-gray-300 dark:bg-gray-900"
			/>
		{/if}
		{#each matches as option}
			<button
				type="button"
				role="menuitemradio"
				aria-checked={value === option.value}
				disabled={option.disabled}
				title={option.description}
				class="flex w-full items-center gap-3 rounded-xl px-2.5 py-2 text-left text-[13px] leading-5 text-gray-800 outline-none hover:bg-gray-50 focus-visible:bg-gray-100 disabled:opacity-40 dark:text-gray-100 dark:hover:bg-gray-800 dark:focus-visible:bg-gray-800"
				on:click={() => {
					value = option.value;
					dispatch('change', value);
					show = false;
				}}
			>
				<span class="min-w-0 flex-1 break-words">
					{option.label}
					{#if option.description}<span class="mt-0.5 block text-xs leading-4 text-gray-500">
							{option.description}
						</span>{/if}
				</span>
				{#if value === option.value}<Check className="size-3.5 shrink-0" />{/if}
			</button>
		{:else}
			<p class="px-2.5 py-3 text-xs text-gray-500">No matching options</p>
		{/each}
	</div>
</Dropdown>

<style>
	.composer-choice {
		display: inline-flex;
		flex: 0 0 auto;
		align-items: center;
		gap: 4px;
		height: 32px;
		padding: 0 6px;
		border-radius: 8px;
		background: transparent;
		color: #737373;
		font-size: 13px;
		line-height: 20px;
		white-space: nowrap;
		transition:
			background 120ms,
			color 120ms;
	}
	.choice-value {
		max-width: 12rem;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.prominent {
		color: #262626;
		font-weight: 500;
	}
	.prominent .choice-value {
		max-width: 16rem;
	}
	.composer-choice:hover {
		color: #171717;
		background: #f5f5f5;
	}
	.composer-choice:focus-visible {
		outline: 2px solid #a3a3a3;
		outline-offset: -2px;
	}
	.composer-choice:disabled {
		opacity: 0.45;
		cursor: default;
	}
	:global(.dark) .composer-choice {
		color: #a3a3a3;
	}
	:global(.dark) .prominent {
		color: #f5f5f5;
	}
	:global(.dark) .composer-choice:hover {
		color: #fafafa;
		background: #262626;
	}
</style>
