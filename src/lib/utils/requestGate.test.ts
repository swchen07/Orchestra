import { describe, it, expect } from 'vitest';
import { createRequestGate } from './requestGate';

describe('history navigation races', () => {
	it('a slow prior conversation cannot overwrite the one opened later', async () => {
		const gate = createRequestGate();
		let finishOld!: () => void;
		let visible = '';
		const old = gate.begin();
		const pending = new Promise<void>((resolve) => {
			finishOld = resolve;
		}).then(() => {
			if (old()) visible = 'old';
		});
		const latest = gate.begin();
		if (latest()) visible = 'new';
		finishOld();
		await pending;
		expect(visible).toBe('new');
	});
	it('a new chat invalidates unfinished history and folder reads', () => {
		const gate = createRequestGate();
		const pending = gate.begin();
		gate.invalidate();
		expect(pending()).toBe(false);
		expect(gate.begin()()).toBe(true);
	});
});
