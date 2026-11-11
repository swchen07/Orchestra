// Per-process bridge to the installed DSH preset registry, before the first turn.
export const name = 'open-webui-preset-bridge';
export const inject = ['agents', 'agentPresets', 'sessionProjections'];

export function apply(ctx, config) {
	ctx.on('agent/created', async ({ agent, source }) => {
		const saved = ctx.sessionProjections.stateOf(agent.session, 'agentPreset');
		if (source === 'resume') {
			if (saved !== config.preset) throw new Error('The resumed DSH preset differs from the selected preset');
			await ctx.agentPresets.mount(agent.ctx, saved);
		} else {
			await ctx.agentPresets.select(agent, config.preset);
		}
	});
}
