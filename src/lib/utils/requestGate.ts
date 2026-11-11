/** Keep an older response from committing state after navigation or a newer read. */
export function createRequestGate() {
	let generation = 0;
	return {
		begin() {
			const request = ++generation;
			return () => request === generation;
		},
		invalidate() {
			generation++;
		}
	};
}
