import { writable } from 'svelte/store';

export const showWorkflowPanel = writable(true);
let initialized = false;
export function initializeWorkflowPanel() {
	if (initialized) return;
	initialized = true;
	try {
		showWorkflowPanel.set(localStorage.getItem('workflowSidebarOpen') !== 'false');
	} catch {
		/* Storage can be unavailable in private browsing. */
	}
}
export function setWorkflowPanel(open: boolean) {
	showWorkflowPanel.set(open);
	try {
		localStorage.setItem('workflowSidebarOpen', String(open));
	} catch {
		/* Keep the local control usable. */
	}
}
