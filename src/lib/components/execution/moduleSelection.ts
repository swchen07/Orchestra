import type { CallableModule, GraphNode, WorkflowGraph } from '$lib/apis/execution';
import { workflowInputRef } from './nodeProgram';

/** Bind an implementation while retaining the node's Action and logical instance. */
export function bindModuleContract(
	node: GraphNode,
	module: CallableModule | undefined,
	graph: WorkflowGraph
): GraphNode {
	const next = structuredClone(node);
	if (module?.module_type !== 'workflow') return next;
	const schema = structuredClone(module.input_schema) as Record<string, any> | undefined;
	const injected = next.op === 'map' ? 'item' : next.op === 'loop' ? 'previous' : null;
	if (schema && injected) {
		delete schema.properties?.[injected];
		schema.required = (schema.required ?? []).filter((name: string) => name !== injected);
	}
	if (schema) {
		next.input_schema = schema;
		next.input_schema_inferred = false;
		const names = Object.keys(schema.properties ?? {});
		next.inputs = {
			...next.inputs,
			...Object.fromEntries(
				names.map((name) => [
					name,
					next.inputs[name] ??
						(name === 'task' ? workflowInputRef(graph) : { source: 'node', node: '', pointer: '' })
				])
			)
		};
	}
	if (next.output_schema === undefined && module.output_schema !== undefined)
		next.output_schema = structuredClone(module.output_schema);
	return next;
}

export function bindingIssues(node: GraphNode, module?: CallableModule): string[] {
	if (module?.module_type !== 'workflow') return [];
	const expected = node.output_schema as Record<string, any> | undefined;
	const actual = module.output_schema as Record<string, any> | undefined;
	const input = module.input_schema as Record<string, any> | undefined;
	const unexpected =
		input?.additionalProperties === false
			? Object.keys(node.inputs).filter((name) => !(name in (input.properties ?? {})))
			: [];
	const missing = (expected?.required ?? []).filter(
		(name: string) => !(name in (actual?.properties ?? {}))
	);
	const mismatched = Object.entries(expected?.properties ?? {})
		.filter(([name, field]) => {
			const producer = actual?.properties?.[name];
			const type = (field as any)?.type;
			return (
				type &&
				producer?.type &&
				type !== producer.type &&
				!(type === 'number' && producer.type === 'integer')
			);
		})
		.map(([name]) => name);
	return [
		unexpected.length ? 'Input fields not accepted by this module: ' + unexpected.join(', ') : '',
		missing.length ? 'Missing required output fields: ' + missing.join(', ') : '',
		mismatched.length ? 'Incompatible output types: ' + mismatched.join(', ') : '',
		Object.entries(node.inputs).some(([, ref]) => ref.source === 'node' && !ref.node)
			? 'Map each public input before saving.'
			: ''
	].filter(Boolean);
}
