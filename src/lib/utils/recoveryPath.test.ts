import { describe, it, expect } from 'vitest';
import { recoveryPath } from './recoveryPath';
describe('backend recovery preserves the current conversation', () => {
	it('retains local chat and project routes with queries', () => {
		expect(recoveryPath('/c/chat-id?branch=leaf')).toBe('/c/chat-id?branch=leaf');
		expect(recoveryPath('/projects/project-id')).toBe('/projects/project-id');
	});
	it('rejects external locations and recursive error routes', () => {
		for (const path of [
			'https://external.test',
			'//external.test',
			'/\\external.test',
			'/error?redirect=/error'
		])
			expect(recoveryPath(path)).toBe('/');
	});
});
