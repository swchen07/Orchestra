import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

export const load: PageLoad = ({ url }) => {
	// Keep old bookmarks usable without embedding the model editor in Connections.
	if (url.searchParams.get('view') === 'presets') redirect(307, '/workspace/models');
	return {};
};
