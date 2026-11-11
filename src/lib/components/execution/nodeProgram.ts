import type { GraphNode, GraphRef, WorkflowGraph } from '$lib/apis/execution';
import actionRegistry from './nodeActions.generated.json';

export const nodeActions = actionRegistry.actions;
export const nodeOperations = Object.fromEntries(
	nodeActions.map((a) => [a.operation, a.label])
) as Record<GraphNode['op'], string>;
export const actionFor = (node: GraphNode) =>
	nodeActions.find((a) => (node.action ? a.id === node.action : a.operation === node.op))!;
export const actionId = (op: GraphNode['op']) => nodeActions.find((a) => a.operation === op)!.id;

export const inputNode = (graph: WorkflowGraph) => graph.nodes.find((n) => n.op === 'input');
export const boundaryNode = (node: GraphNode) => ['input', 'output'].includes(node.op);
export function workflowInputRef(graph: WorkflowGraph): GraphRef {
	const entry = inputNode(graph);
	return entry
		? { source: 'node', node: entry.id, pointer: '/' + Object.keys(entry.inputs)[0] }
		: { source: 'input', pointer: '/task' };
}
export function synchronizeBoundaryPorts(node: GraphNode): GraphNode {
	if (!boundaryNode(node)) return node;
	const schema = (node.output_schema ?? {}) as Record<string, any>;
	return {
		...node,
		output_schema: {
			type: 'object',
			properties: Object.fromEntries(
				Object.entries(node.inputs).map(([name, ref]) => [
					name,
					schema.properties?.[name] ??
						(ref.source === 'input' && ref.pointer === '/task' ? { type: 'string' } : {})
				])
			),
			required: Object.keys(node.inputs),
			additionalProperties: false
		}
	};
}
export function prerequisites(graph: WorkflowGraph, id: string) {
	return graph.nodes.filter((n) =>
		graph.edges.some((e) => e.on !== 'revise' && e.from === n.id && e.to === id)
	);
}
export function dependencyChoices(graph: WorkflowGraph, id: string) {
	const downstream = new Set([id]);
	for (let i = 0; i < graph.nodes.length; i++)
		for (const edge of graph.edges)
			if (edge.on !== 'revise' && downstream.has(edge.from)) downstream.add(edge.to);
	return graph.nodes.filter((n) => !downstream.has(n.id) && n.op !== 'output');
}

export function feedbackTargetChoices(graph: WorkflowGraph, id: string) {
	const upstream = new Set<string>();
	for (let pass = 0; pass < graph.nodes.length; pass++)
		for (const edge of dataEdges(graph))
			if (edge.on !== 'revise' && (edge.to === id || upstream.has(edge.to)))
				upstream.add(edge.from);
	return graph.nodes.filter(
		(node) =>
			node.id !== id &&
			actionFor(node)?.feedback_policy &&
			(actionFor(node).feedback_policy === 'replan' || upstream.has(node.id))
	);
}
/** An ordering constraint never adds, removes or renames data bindings. */
export function setDependency(
	graph: WorkflowGraph,
	target: string,
	parent: string,
	checked: boolean
) {
	const edges = graph.edges.filter((e) => !(e.from === parent && e.to === target));
	if (checked) edges.push({ from: parent, to: target, on: 'success' });
	return synchronizeGraph({ ...graph, edges });
}
export function consumers(graph: WorkflowGraph, id: string) {
	return graph.nodes.flatMap((node) =>
		Object.entries(node.inputs).flatMap(([name, ref]) =>
			referenceNodes(ref, graph).includes(id)
				? [{ node: node.id, label: node.label ?? node.id, input: name, pointer: ref.pointer ?? '' }]
				: []
		)
	);
}
export const planningNode = (graph?: WorkflowGraph) => graph?.nodes?.find((n) => n.op === 'plan');
export const hasPlanning = (graph?: WorkflowGraph) =>
	Boolean(planningNode(graph) || graph?.controller);
export const planningBinding = (graph?: WorkflowGraph) =>
	planningNode(graph)?.binding ?? graph?.controller?.binding;
export const reviewingBinding = (graph?: WorkflowGraph) =>
	graph?.nodes?.find((n) => n.op === 'review')?.binding ?? graph?.reviewer?.binding;

export function referenceNodes(
	ref: GraphRef,
	graph: WorkflowGraph,
	visited: string[] = []
): string[] {
	if (ref.source === 'node') return ref.node ? [ref.node] : [];
	if (ref.source === 'output' && ref.port && !visited.includes(ref.port)) {
		const target = graph.outputs?.[ref.port];
		return target ? referenceNodes(target, graph, [...visited, ref.port]) : [];
	}
	return [];
}
export const outputNode = (graph: WorkflowGraph) =>
	graph.outputs?.[graph.delivery_port ?? 'answer']
		? (referenceNodes(graph.outputs[graph.delivery_port ?? 'answer'], graph)[0] ??
			graph.result_node)
		: graph.result_node;

export function dataEdges(graph: WorkflowGraph) {
	const edges = graph.edges.filter((e) => !e.generated && e.on !== 'revise').map((e) => ({ ...e }));
	for (const node of graph.nodes)
		for (const ref of [
			...Object.values(node.inputs),
			...(node.op === 'map' && node.items ? [node.items] : [])
		])
			for (const parent of referenceNodes(ref, graph))
				if (!edges.some((e) => e.from === parent && e.to === node.id))
					edges.push({
						from: parent,
						to: node.id,
						on: ref.optional ? 'terminal' : 'success',
						generated: true
					});
	for (const node of graph.nodes)
		if (node.op === 'review' && node.review?.repair_node)
			edges.push({ from: node.id, to: node.review.repair_node, on: 'revise', generated: true });
	return edges;
}

export function synchronizeGraph(graph: WorkflowGraph) {
	const nodes = graph.nodes.map((node) => {
		if (node.op !== 'dispatch') return node;
		const schema = (node.output_schema ?? capabilitySchema('dispatch')) as Record<string, any>;
		const control = capabilitySchema('dispatch')!.properties.selected_nodes;
		return {
			...node,
			output_schema: {
				...schema,
				type: 'object',
				properties: {
					...schema.properties,
					selected_nodes: {
						...control,
						description: schema.properties?.selected_nodes?.description ?? control.description,
						items: { type: 'string', enum: node.dispatch?.targets ?? [] }
					}
				},
				required: [...new Set([...(schema.required ?? []), 'selected_nodes'])]
			}
		};
	});
	const exit = nodes.find((node) => node.op === 'output');
	const outputs = { ...graph.outputs };
	if (exit) {
		for (const [name, ref] of Object.entries(outputs))
			if (ref.source === 'node' && ref.node === exit.id && !(name in exit.inputs))
				delete outputs[name];
		for (const name of Object.keys(exit.inputs))
			outputs[name] = {
				source: 'node',
				node: exit.id,
				pointer: '/' + name.replaceAll('~', '~0').replaceAll('/', '~1')
			};
	}
	const entry = nodes.find((node) => node.op === 'input');
	const schema = structuredClone(
		graph.input_schema ?? {
			type: 'object',
			properties: {},
			required: [],
			additionalProperties: false
		}
	) as Record<string, any>;
	if (entry) {
		for (const [name, ref] of Object.entries(entry.inputs)) {
			if (ref.source !== 'input' || !ref.pointer || ref.pointer.slice(1).includes('/')) continue;
			const field = ref.pointer.slice(1).replaceAll('~1', '/').replaceAll('~0', '~');
			schema.properties ??= {};
			schema.properties[field] =
				(entry.output_schema as any)?.properties?.[name] ?? schema.properties[field] ?? {};
			schema.required = [...new Set([...(schema.required ?? []), field])];
		}
	}
	const result = {
		...graph,
		nodes,
		...(graph.schema_version === '2.0' ? { outputs, input_schema: schema } : {})
	};
	return { ...result, edges: dataEdges(result), result_node: outputNode(result) };
}

function baseCapabilitySchema(op: GraphNode['op']): Record<string, any> | undefined {
	if (op === 'dispatch')
		return {
			type: 'object',
			properties: {
				selected_nodes: { type: 'array', items: { type: 'string' }, minItems: 1, uniqueItems: true }
			},
			required: ['selected_nodes']
		};
	if (['join', 'finish', 'checkpoint'].includes(op)) {
		const properties: Record<string, any> = { report: op === 'finish' ? {} : { type: 'string' } };
		if (op === 'join')
			Object.assign(properties, { results: { type: 'object' }, outcomes: { type: 'object' } });
		if (op === 'checkpoint') properties.answer = {};
		return { type: 'object', properties, required: Object.keys(properties) };
	}
	if (op === 'plan')
		return { type: 'object', properties: { action: { type: 'string' } }, required: ['action'] };
	if (op === 'review')
		return {
			type: 'object',
			properties: {
				verdict: { type: 'string', enum: ['approve', 'revise'] },
				summary: { type: 'string' },
				issues: { type: 'array', items: { type: 'string' } },
				checked_report_refs: { type: 'array', items: { type: 'string' } },
				answer: { type: 'string' }
			},
			required: ['verdict', 'summary', 'issues', 'checked_report_refs', 'answer']
		};
}

export function capabilitySchema(op: GraphNode['op']) {
	if (['command', 'evaluate', 'integrate', 'publish'].includes(op))
		return {
			type: 'object',
			properties: {
				status: { type: 'string' },
				report: { type: 'string' },
				__workspace: { type: 'object' },
				__artifacts: { type: 'array', items: { type: 'object' } },
				commands: { type: 'array' },
				evaluations: { type: 'array' },
				conflicts: { type: 'array' }
			},
			required: ['status', 'report']
		};
	const schema = baseCapabilitySchema(op);
	const meanings = nodeActions.find((a) => a.operation === op)?.output_meanings as
		| Record<string, string>
		| undefined;
	if (schema)
		for (const [name, field] of Object.entries(schema.properties) as [
			string,
			Record<string, any>
		][]) {
			if (meanings?.[name]) field.description ??= meanings[name];
		}
	return schema;
}
