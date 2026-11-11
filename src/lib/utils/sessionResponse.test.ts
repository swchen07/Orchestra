import { describe, expect, it } from 'vitest';
import {
	isAuthenticationRejection,
	readSessionResponse,
	SessionRequestError
} from './sessionResponse';

describe('session verification', () => {
	it('returns a successful session unchanged', async () => {
		const response = new Response(JSON.stringify({ id: 'user', role: 'user' }));
		expect(await readSessionResponse(response)).toEqual({ id: 'user', role: 'user' });
	});
	it('retains an explicit authentication rejection even with an empty error body', async () => {
		const error = await readSessionResponse(new Response('', { status: 401 })).catch((e) => e);
		expect(isAuthenticationRejection(error)).toBe(true);
	});
	it('does not treat service, network or incomplete responses as a rejected login', async () => {
		const unavailable = await readSessionResponse(new Response('', { status: 503 })).catch(
			(e) => e
		);
		const incomplete = await readSessionResponse(new Response('')).catch((e) => e);
		for (const error of [unavailable, incomplete, new TypeError('network interrupted')]) {
			expect(isAuthenticationRejection(error)).toBe(false);
		}
	});
	it('still rejects forbidden sessions', () => {
		expect(isAuthenticationRejection(new SessionRequestError('Forbidden', 403))).toBe(true);
	});
});
