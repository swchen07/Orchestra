<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import {
		WEBUI_NAME,
		config,
		showSidebar,
		user,
		mobile,
		workspaceActions,
		workspaceCounts
	} from '$lib/stores';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { getModelItems } from '$lib/apis/models';
	import { searchKnowledgeBases } from '$lib/apis/knowledge';
	import { getPromptItems } from '$lib/apis/prompts';
	import { getSkillItems } from '$lib/apis/skills';
	import { getToolList } from '$lib/apis/tools';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import SplitCreateButton from '$lib/components/common/SplitCreateButton.svelte';
	import { Popover } from 'bits-ui';
	import XMark from '$lib/components/icons/XMark.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import { formatNumber } from '$lib/utils';
	import { getNativeResources } from '$lib/apis/execution';
	let nativeCounts = { skills: 0, prompts: 0, tools: 0 };

	const i18n = getContext<Writable<i18nType>>('i18n');

	let loaded = false;
	let lastPath = '';
	let activeWorkspaceSection = '';
	let visibleActions = [];
	let showMore = false;

	$: if ($page.url.pathname !== lastPath) {
		lastPath = $page.url.pathname;
		showMore = false;
		workspaceActions.set([]);
	}

	$: if (loaded && $page.url.pathname.startsWith('/workspace')) {
		loadWorkspaceCounts();
	}

	$: activeWorkspaceSection = $page.url.pathname.split('/')[2] ?? '';
	// Resource pages publish shared actions; never show a prior page's toolbar here.
	$: visibleActions =
		$page.url.pathname.replace(/\/+$/, '') === `/workspace/${activeWorkspaceSection}`
			? $workspaceActions.filter(
					(action) => action.id.startsWith(`${activeWorkspaceSection}-`) && (action.visible ?? true)
				)
			: [];

	$: isAdmin = $user?.role === 'admin';
	$: navigation = [
		{
			id: 'connections',
			label: 'Connections',
			href: '/workspace/connections',
			visible: isAdmin,
			count: null
		},
		{ id: 'presets', label: 'Presets', href: '/workspace/presets', visible: isAdmin, count: null },
		{
			id: 'workflows',
			label: 'Workflows',
			href: '/workspace/workflows',
			visible: isAdmin,
			count: null
		},
		{
			id: 'models',
			label: 'Model presets',
			href: '/workspace/models',
			visible: !isAdmin && $user?.permissions?.workspace?.models,
			count: $workspaceCounts.models
		},
		{
			id: 'knowledge',
			label: 'Knowledge',
			href: '/workspace/knowledge',
			visible: !isAdmin && $user?.permissions?.workspace?.knowledge,
			count: $workspaceCounts.knowledge
		},
		{
			id: 'prompts',
			label: 'Prompts',
			href: '/workspace/prompts',
			visible: isAdmin || $user?.permissions?.workspace?.prompts,
			count: ($workspaceCounts.prompts ?? 0) + nativeCounts.prompts
		},
		{
			id: 'skills',
			label: 'Skills',
			href: '/workspace/skills',
			visible: isAdmin || $user?.permissions?.workspace?.skills,
			count: ($workspaceCounts.skills ?? 0) + nativeCounts.skills
		},
		{
			id: 'tools',
			label: 'MCP',
			href: '/workspace/tools',
			visible: isAdmin || ($config?.features?.enable_tools && $user?.permissions?.workspace?.tools),
			count: ($workspaceCounts.tools ?? 0) + nativeCounts.tools
		}
	].filter((item) => item.visible);
	$: additionalPages = [
		{
			id: 'models',
			label: 'Model editor',
			description: 'Open WebUI model instructions and parameters',
			href: '/workspace/models',
			visible: isAdmin
		},
		{
			id: 'knowledge',
			label: 'Knowledge',
			description: 'Documents used as reference material in chats',
			href: '/workspace/knowledge',
			visible: isAdmin
		},
		{
			id: 'playground',
			label: 'Playground',
			description: 'Test model API requests and parameters',
			href: '/playground',
			visible: isAdmin
		}
	].filter((item) => item.visible);
	$: additionalPageActive = additionalPages.some((item) => item.id === activeWorkspaceSection);

	const getCount = (res: unknown): number | null => {
		if (Array.isArray(res)) return res.length;
		return res && typeof res === 'object' && 'total' in res && typeof res.total === 'number'
			? res.total
			: null;
	};
	const formatCount = (count: number | null) => formatNumber(count ?? 0);

	const loadWorkspaceCounts = async () => {
		const canViewModels = $user?.role === 'admin' || $user?.permissions?.workspace?.models;
		const canViewKnowledge = $user?.role === 'admin' || $user?.permissions?.workspace?.knowledge;
		const canViewPrompts = $user?.role === 'admin' || $user?.permissions?.workspace?.prompts;
		const canViewSkills = $user?.role === 'admin' || $user?.permissions?.workspace?.skills;
		const canViewTools =
			$config?.features?.enable_tools &&
			($user?.role === 'admin' || $user?.permissions?.workspace?.tools);

		const [modelRes, knowledgeRes, promptRes, skillRes, toolRes, nativeRes] = await Promise.all([
			canViewModels
				? getModelItems(localStorage.token, null, null, null, null, null, 1).catch(() => null)
				: null,
			canViewKnowledge
				? searchKnowledgeBases(localStorage.token, null, null, 1, null).catch(() => null)
				: null,
			canViewPrompts
				? getPromptItems(localStorage.token, null, null, null, null, null, 1).catch(() => null)
				: null,
			canViewSkills ? getSkillItems(localStorage.token, null, null, 1).catch(() => null) : null,
			canViewTools ? getToolList(localStorage.token).catch(() => null) : null,
			$user?.role === 'admin' ? getNativeResources().catch(() => null) : null
		]);
		nativeCounts = {
			skills: nativeRes?.resources.skills.length ?? 0,
			prompts: nativeRes?.resources.prompts.length ?? 0,
			tools: nativeRes?.resources.mcp.length ?? 0
		};

		workspaceCounts.set({
			models: getCount(modelRes),
			knowledge: getCount(knowledgeRes),
			prompts: getCount(promptRes),
			skills: getCount(skillRes),
			tools: getCount(toolRes)
		});
	};

	onMount(async () => {
		if ($user?.role !== 'admin') {
			if ($page.url.pathname.includes('/models') && !$user?.permissions?.workspace?.models) {
				goto('/', { replaceState: true });
			} else if (
				$page.url.pathname.includes('/knowledge') &&
				!$user?.permissions?.workspace?.knowledge
			) {
				goto('/', { replaceState: true });
			} else if (
				$page.url.pathname.includes('/prompts') &&
				!$user?.permissions?.workspace?.prompts
			) {
				goto('/', { replaceState: true });
			} else if (
				$page.url.pathname.includes('/tools') &&
				(!$config?.features?.enable_tools || !$user?.permissions?.workspace?.tools)
			) {
				goto('/', { replaceState: true });
			} else if ($page.url.pathname.includes('/skills') && !$user?.permissions?.workspace?.skills) {
				goto('/', { replaceState: true });
			}
		}

		loaded = true;
	});
</script>

<svelte:head>
	<!-- LICENSE covers this Open WebUI browser-title identifier.
	Do not alter, remove, obscure, or replace it except as LICENSE permits:
	https://docs.openwebui.com/license. -->
	<title>
		{navigation.find((item) => item.id === activeWorkspaceSection)?.label ??
			additionalPages.find((item) => item.id === activeWorkspaceSection)?.label ??
			($user?.role === 'admin' ? 'Configuration' : $i18n.t('Workspace'))} / {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div
		class="flex flex-col flex-1 min-w-0 w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: 'md:max-w-[calc(100%-42px)]'} max-w-full"
	>
		<nav
			class="border-b border-gray-100 px-5 pt-5 pb-2 dark:border-gray-800 drag-region select-none"
		>
			<h1 class="mb-4 text-2xl font-semibold">Configuration</h1>
			<div class="flex items-center gap-0.5 md:gap-1">
				{#if $mobile}
					<div class="{$showSidebar ? 'md:hidden' : ''} self-center flex flex-none items-center">
						<Tooltip
							content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
							interactive={true}
						>
							<button
								id="sidebar-toggle-button"
								class=" cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition cursor-"
								aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
								on:click={() => {
									showSidebar.set(!$showSidebar);
								}}
							>
								<div class=" self-center p-1.5">
									<Sidebar className="size-4" />
								</div>
							</button>
						</Tooltip>
					</div>
				{/if}

				<div class="flex min-w-0 w-full items-center gap-2">
					<div
						aria-label="Configuration sections"
						class="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto scrollbar-none"
					>
						{#each navigation as item}
							<a
								class="configuration-link inline-flex shrink-0 items-center gap-1.5 px-2 py-2 text-sm font-medium text-gray-600 transition hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100"
								href={item.href}
								aria-current={activeWorkspaceSection === item.id ? 'page' : null}
								draggable="false"
								data-sveltekit-keepfocus
								on:click={() => (showMore = false)}
							>
								<span>{item.label}</span>{#if item.count !== null}<span class="text-xs opacity-70"
										>{formatCount(item.count)}</span
									>{/if}
							</a>
						{/each}
					</div>
					<div class="ml-auto flex shrink-0 items-center gap-2">
						{#if additionalPages.length}<Popover.Root bind:open={showMore}>
								<Popover.Trigger
									aria-label="More configuration pages"
									class="inline-flex items-center gap-1 rounded-lg px-2 py-2 text-sm font-medium {additionalPageActive
										? 'text-blue-600 dark:text-blue-400'
										: 'text-gray-600 dark:text-gray-400'} hover:bg-gray-50 dark:hover:bg-gray-800"
									>More<ChevronDown className="size-3.5" /></Popover.Trigger
								>
								<Popover.Portal>
									<Popover.Content
										role="dialog"
										aria-label="Additional tools"
										trapFocus={false}
										side="bottom"
										align="end"
										sideOffset={6}
										class="z-[9999] w-72 max-w-[calc(100vw-2rem)] max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-xl border border-gray-100 bg-white p-1.5 shadow-lg dark:border-gray-800 dark:bg-gray-900"
									>
										<div class="flex items-center justify-between px-2.5 py-1">
											<p class="text-xs font-medium text-gray-500">Additional tools</p>
											<Popover.Close
												aria-label="Close additional tools"
												title="Close"
												class="rounded-md p-1.5 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
												><XMark className="size-3.5" /></Popover.Close
											>
										</div>
										{#each additionalPages as item}<a
												href={item.href}
												aria-current={activeWorkspaceSection === item.id ? 'page' : null}
												class="block rounded-lg px-2.5 py-2.5 text-gray-800 hover:bg-gray-50 focus-visible:bg-gray-100 dark:text-gray-100 dark:hover:bg-gray-800"
												on:click={() => (showMore = false)}
												><span class="text-sm font-medium">{item.label}</span><span
													class="mt-1 block text-xs leading-5 text-gray-500 dark:text-gray-400"
													>{item.description}</span
												></a
											>{/each}
									</Popover.Content>
								</Popover.Portal>
							</Popover.Root>{/if}
						<SplitCreateButton actions={visibleActions} />
					</div>
				</div>

				<!-- <div class="flex items-center text-xl font-normal">{$i18n.t('Workspace')}</div> -->
			</div>
		</nav>

		<div
			class="pb-1 flex-1 min-w-0 max-h-full overflow-y-auto overflow-x-hidden"
			class:px-3={!['/workspace/models/create', '/workspace/models/edit'].includes(
				$page.url.pathname
			)}
			id="workspace-container"
		>
			<slot />
		</div>
	</div>
{/if}

<style>
	a.configuration-link {
		border-bottom: 2px solid transparent;
	}
	:global(.dark) a.configuration-link[aria-current='page'] {
		color: #60a5fa;
	}
	a.configuration-link[aria-current='page'] {
		color: #2563eb;
		border-bottom-color: currentColor;
	}
	:global(html.high-contrast) a[aria-current='page'] {
		text-decoration-line: underline;
		text-decoration-thickness: 2px;
		text-underline-offset: 3px;
	}
</style>
