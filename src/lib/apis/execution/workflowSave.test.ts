import { afterEach, expect, it, vi } from 'vitest';

vi.mock('$lib/constants', () => ({ WEBUI_API_BASE_URL: '/api/v1' }));
import { saveWorkflow, type Workflow } from './index';

afterEach(() => { vi.unstubAllGlobals(); });

it('preserves editable example text through the actual save request', async () => {
	vi.stubGlobal('localStorage', { token: 'test-token' });
	const example = { purpose: 'Teach data flow', task: 'Compute two subtotals', expected: 'Separate reports' };
	const fetcher = vi.fn(async (_url: string, request: RequestInit) => ({ ok: true, json: async () => JSON.parse(request.body as string) }));
	vi.stubGlobal('fetch', fetcher);
	const saved = await saveWorkflow({ name: 'Example', roles: [], example } as unknown as Workflow);
	expect(saved.example).toEqual(example);
	expect(JSON.parse(fetcher.mock.calls[0][1].body as string).example).toEqual(example);
});
