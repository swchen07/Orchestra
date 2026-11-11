"""Versioned, framework-neutral workflow programs and atomic plan revisions.

Legacy collaboration modes are translated only at this import boundary. The
interpreter consumes nodes and dependencies, never workflow names or modes.
"""

import copy
import json
import re
from open_webui.execution.node_actions import BY_OPERATION, normalize_action, feedback_policy

ID = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,63}$')
OPS = set(BY_OPERATION)
TERMINAL = {'completed', 'failed', 'skipped', 'cancelled', 'interrupted'}
ACTIVE = {'queued', 'running'}
DEFAULT_LIMITS = {'max_calls': 24, 'max_seconds': 600, 'max_concurrency': 2, 'max_replans': 5, 'max_tokens': 1000000}


def json_object(text):
    raw = text.strip()
    if raw.startswith('```'):
        lines = raw.splitlines()
        raw = '\n'.join(lines[1:-1])
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError('Return one JSON object matching the controller protocol') from None
    if not isinstance(value, dict):
        raise ValueError('The controller response must be an object')
    return value


def pointer(value, path=''):
    if not isinstance(path, str) or (path and not path.startswith('/')):
        raise ValueError('Use a JSON pointer beginning with /')
    for part in path.split('/')[1:]:
        if re.search(r'~(?![01])', part):
            raise ValueError('Invalid JSON pointer escape')
        key = part.replace('~1', '/').replace('~0', '~')
        try:
            if isinstance(value, list) and (not key.isdecimal() or len(key) > 1 and key.startswith('0')):
                raise ValueError()
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, ValueError, TypeError):
            raise ValueError('A required output field is unavailable: ' + path) from None
    return value


def validate_graph(value, bindings=None):
    from jsonschema import Draft202012Validator
    from open_webui.execution.graph_schema import SCHEMA, SCHEMA_V2
    from open_webui.execution.node_program import planner_node, referenced_nodes, output_ref, capability_schema

    modern = isinstance(value, dict) and value.get('schema_version') == '2.0'

    try:
        if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 256000:
            raise ValueError('Workflow graph exceeds 250 KiB')
    except (TypeError, OverflowError, RecursionError):
        raise ValueError('A graph must contain JSON values') from None
    error = next(Draft202012Validator(SCHEMA_V2 if modern else SCHEMA).iter_errors(value), None)
    if error:
        path = '.'.join(str(p) for p in error.absolute_path) or 'graph'
        raise ValueError('Invalid ' + path + ': ' + error.message[:300])
    if not isinstance(value, dict) or set(value) - {
        'schema_version',
        'revision',
        'nodes',
        'edges',
        'result_node',
        'input_policy',
        'controller',
        'reviewer',
        'limits',
        'outputs',
        'input_schema', 'output_schema', 'output_schema_inferred', 'delivery_port',
    }:
        raise ValueError('A graph defines schema_version, nodes, edges, result_node, controller and limits')
    graph = copy.deepcopy(value)
    if graph.get('schema_version', '1.0') not in {'1.0', '2.0'}:
        raise ValueError('Unsupported workflow graph version')
    nodes, edges = graph.get('nodes'), graph.get('edges', [])
    if modern:
        edges = [e for e in edges if not e.get('generated')]
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 80 or not isinstance(edges, list) or len(edges) > 320:
        raise ValueError('Define 1–80 nodes and up to 320 dependency edges')
    choices = set(bindings) if bindings is not None else None
    ids = set()
    from jsonschema import Draft202012Validator

    for node in nodes:
        if not isinstance(node, dict) or set(node) - {
            'id',
            'label',
            'op',
            'enabled',
            'binding',
            'instruction',
            'inputs',
            'context_key',
            'output_schema',
            'cases',
            'policy',
            'quorum',
            'items',
            'max_iterations',
            'stop_when',
            'question',
            'acceptance',
            'planning',
            'review',
            'action',
            'report_description',
            'dispatch',
            'input_schema', 'input_schema_inferred', 'environment', 'workspace',
        }:
            raise ValueError('Invalid workflow node fields')
        if node.get('workspace') is not None and node.get('op') not in {'command', 'evaluate', 'integrate', 'publish'} and node.get('action') not in {'workspace.command', 'artifact.evaluate', 'artifact.integrate', 'workspace.publish'}:
            raise ValueError('Workspace Action settings require a registered workspace host Action')
        if modern:
            normalize_action(node)
        identity, op = node.get('id'), node.get('op')
        if not isinstance(identity, str) or not ID.fullmatch(identity) or identity in ids or op not in OPS:
            raise ValueError('Each node needs a unique stable ID and a supported operation')
        if identity in {'_controller', '_reviewer'}:
            raise ValueError('Controller and reviewer node IDs are reserved')
        ids.add(identity)
        if op in {'plan', 'review', 'input', 'output', 'dispatch'} and not modern:
            raise ValueError('Composable planning and review nodes use graph schema 2.0')
        if ('planning' in node and op != 'plan') or ('review' in node and op != 'review') or ('dispatch' in node and op != 'dispatch'):
            raise ValueError('Node capability settings must match its operation')
        node.setdefault('label', identity)
        node.setdefault('inputs', {})
        if not isinstance(node['label'], str) or not 1 <= len(node['label']) <= 150:
            raise ValueError('Use a short node label')
        if not isinstance(node['inputs'], dict) or len(node['inputs']) > 40:
            raise ValueError('Node inputs must be named references')
        for key, ref in node['inputs'].items():
            if not isinstance(key, str) or not ID.fullmatch(key):
                raise ValueError('Input ports need stable names')
            validate_ref(ref)
        if op in {'call', 'route', 'map', 'loop', 'plan', 'review', 'dispatch', 'workflow'}:
            if (
                not isinstance(node.get('binding'), str)
                or not node['binding']
                or (choices is not None and node['binding'] not in choices)
            ):
                raise ValueError('Choose an available Project module for node ' + identity)
            node.setdefault('context_key', identity if modern else node['binding'] if ID.fullmatch(node['binding']) else identity)
            if not isinstance(node.get('instruction', ''), str) or len(node.get('instruction', '').encode()) > 16000:
                raise ValueError('Keep node instructions within 16 KiB')
        elif node.get('binding') or node.get('context_key'):
            raise ValueError('Host Actions cannot use an Agent binding or private context')
        if node.get('context_key') is not None and (
            not isinstance(node['context_key'], str) or not ID.fullmatch(node['context_key'])
        ):
            raise ValueError('Use a stable context key')
        if op in {'plan', 'review', 'dispatch'} or (modern and op in {'join', 'finish', 'checkpoint'}):
            node.setdefault('output_schema', capability_schema(op))
        if op == 'dispatch':
            schema=node['output_schema']
            if not isinstance(schema,dict) or schema.get('type')!='object':
                raise ValueError('Dispatch requires structured assignment fields and selected_nodes')
            control=capability_schema(op)['properties']['selected_nodes']
            control['description']=schema.get('properties',{}).get('selected_nodes',{}).get('description',control.get('description',''))
            control['items']['enum']=node.get('dispatch',{}).get('targets',[])
            schema.setdefault('properties',{})['selected_nodes']=control
            schema['required']=list(dict.fromkeys([*schema.get('required',[]),'selected_nodes']))
        if node.get('output_schema') is not None:
            try:
                encoded = json.dumps(node['output_schema'])
                if len(encoded) > 16000 or '$ref' in encoded or '$dynamicRef' in encoded:
                    raise ValueError('Use a local output schema without external references')
                Draft202012Validator.check_schema(node['output_schema'])
            except Exception:
                raise ValueError('Invalid node output schema') from None
        if op == 'route' and (
            not isinstance(node.get('cases'), list)
            or not 2 <= len(node['cases']) <= 12
            or any(not isinstance(c, str) or not ID.fullmatch(c) for c in node['cases'])
            or len(set(node['cases'])) != len(node['cases'])
        ):
            raise ValueError('A route needs 2–12 distinct case names')
        if op == 'route':
            node.setdefault('output_schema', {'type': 'object', 'properties': {}})
            if not isinstance(node['output_schema'], dict) or node['output_schema'].get('type') != 'object':
                raise ValueError('A route requires an object output contract with a case field')
            node['output_schema'].setdefault('properties', {})['case'] = {'type': 'string', 'enum': node['cases'],
                'description': 'The declared branch chosen by this invocation.'}
            node['output_schema']['required'] = list(dict.fromkeys([*node['output_schema'].get('required', []), 'case']))
        if op == 'join':
            node.setdefault('policy', 'all_terminal')
            if node['policy'] not in {'all_success', 'all_terminal', 'first_success', 'quorum'}:
                raise ValueError('Unknown join policy')
            if node['policy'] == 'quorum' and (type(node.get('quorum')) is not int or node['quorum'] < 1):
                raise ValueError('A quorum needs a positive success count')
        if op == 'map':
            validate_ref(node.get('items'))
        if op == 'loop':
            if type(node.get('max_iterations')) is not int or not 1 <= node['max_iterations'] <= 8:
                raise ValueError('A loop requires a limit of 1–8 iterations')
            if not isinstance(node.get('stop_when'), dict) or set(node['stop_when']) != {'pointer', 'equals'}:
                raise ValueError('A loop needs a structured stop condition')
        if op == 'checkpoint' and not isinstance(node.get('question'), str):
            raise ValueError('A checkpoint needs a question')
    if modern:
        declared_instances = {n.get('context_key', n['id']) for n in nodes if n.get('binding')} | {n.get('workspace', {}).get('instance', n['id']) for n in nodes if n['op'] == 'integrate'}
        for node in nodes:
            if node['op'] in {'command', 'publish'} and node.get('workspace', {}).get('instance', node['id']) not in declared_instances:
                raise ValueError('Workspace host Actions must select a declared module or integration instance')
        outputs = graph['outputs']
        if not outputs or any(not ID.fullmatch(k) for k in outputs):
            raise ValueError('Define named public Workflow outputs')
        delivery = graph.get('delivery_port') or ('answer' if 'answer' in outputs else None)
        if graph.get('delivery_port') and delivery not in outputs:
            raise ValueError('delivery_port must name an existing public output')
        planners = [n for n in nodes if n['op'] == 'plan']
        if len(planners) > 1:
            raise ValueError('A graph has one serialized graph-mutating planner')
        planned_ports = {(n.get('planning') or {}).get('output_port', 'candidate') for n in planners}
        if delivery in planned_ports:
            raise ValueError('A planning node publishes an intermediate port, not the final answer port')
        for ref in outputs.values():
            validate_ref(ref)
            if ref['source'] == 'output' and ref['port'] not in outputs and ref['port'] not in planned_ports:
                raise ValueError('Graph outputs must resolve to existing or planner-owned ports')
            if any(i not in ids for i in referenced_nodes(ref, graph)):
                raise ValueError('Graph outputs must reference existing nodes')
        for node in nodes:
            if node['op'] == 'review':
                repair = node.get('review', {}).get('repair_node')
                if repair and repair not in {n['id'] for n in nodes if feedback_policy(n)}:
                    raise ValueError('Choose an existing feedback-capable node for review repairs')
            for ref in [*node['inputs'].values(), *([node['items']] if node['op'] == 'map' else [])]:
                if ref['source'] == 'output' and ref['port'] not in outputs and ref['port'] not in planned_ports:
                    raise ValueError('Choose an existing or planner-owned graph output port')
        selected = outputs[delivery] if delivery else next(iter(outputs.values()))
        answer_nodes = referenced_nodes(selected, graph)
        graph['result_node'] = answer_nodes[0] if answer_nodes else (planners[0]['id'] if planners else nodes[-1]['id'])
        if len(answer_nodes) > 1:
            raise ValueError('The delivery port must resolve to an existing node')
        reserved = {n['binding']: n for n in nodes if n['op'] in {'plan', 'review'}}
        contexts = {n.get('context_key', n['id']): n for n in reserved.values()}
        reserved_keys = set(contexts) | set(reserved)
        if len(reserved) != len([n for n in nodes if n['op'] in {'plan', 'review'}]):
            raise ValueError('Planning and review require distinct logical module instances')
        if len(contexts) != len(reserved):
            raise ValueError('Planning and review must keep separate private contexts')
        for node in nodes:
            if node['op'] not in {'plan', 'review'} and (node.get('binding') in reserved or node.get('context_key') in reserved_keys):
                raise ValueError('Work nodes cannot borrow planning or review contexts')
    elif graph.get('result_node') not in ids:
        raise ValueError('Choose an existing result node')
    # A named data reference is already an explicit dependency. Compile its edge
    # instead of requiring the planner/user to duplicate it in two locations.
    for node in nodes:
        refs = [*node['inputs'].values(), *([node['items']] if node['op'] == 'map' else [])]
        for ref in refs:
            for dependency in referenced_nodes(ref, graph):
                if dependency not in ids:
                    raise ValueError('A data reference names an unknown node')
                if not any(
                    e.get('from') == dependency and e.get('to') == node['id'] for e in edges if isinstance(e, dict)
                ):
                    edges.append(
                        {'from': dependency, 'to': node['id'], 'on': 'terminal' if ref.get('optional') else 'success',
                         **({'generated': True} if modern else {})}
                    )
    if len(edges) > 320:
        raise ValueError('Compiled dependency graph exceeds 320 edges')
    parents = {identity: set() for identity in ids}
    seen = set()
    routes = {n['id']: n for n in nodes if n['op'] == 'route'}
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or set(edge) - {'from', 'to', 'on', 'case', *(['generated'] if modern else [])}
            or edge.get('from') not in ids
            or edge.get('to') not in ids
        ):
            raise ValueError('Dependency edges must reference existing nodes')
        edge.setdefault('on', 'success')
        key = (edge['from'], edge['to'])
        if key in seen or edge['on'] not in {'success', 'terminal', 'case', *(['revise'] if modern else [])}:
            raise ValueError('Duplicate edge or unsupported dependency condition')
        seen.add(key)
        if edge['on'] == 'revise':
            source = next(n for n in nodes if n['id'] == edge['from'])
            if source['op'] != 'review' or source.get('review', {}).get('repair_node') != edge['to']:
                raise ValueError('A revise edge must match its review node’s rejection target')
            continue
        if edge['on'] == 'case' and (
            edge['from'] not in routes or edge.get('case') not in routes[edge['from']]['cases']
        ):
            raise ValueError('A case edge must reference a declared route case')
        parents[edge['to']].add(edge['from'])
    remaining = set(ids)
    ancestors = {i: set() for i in ids}
    while remaining:
        ready = [i for i in remaining if not parents[i] & remaining]
        if not ready:
            raise ValueError('Dependency cycles need an explicit bounded loop node')
        for i in ready:
            ancestors[i] = parents[i] | set().union(*(ancestors[p] for p in parents[i]))
        remaining.difference_update(ready)
    for node in nodes:
        if node['op'] == 'dispatch':
            targets = (node.get('dispatch') or {}).get('targets', [])
            if not targets or any(t not in ids or node['id'] not in ancestors[t] or t == graph['result_node'] for t in targets):
                raise ValueError('Dispatch targets must be declared downstream work nodes')
            if any(next(n for n in nodes if n['id'] == t)['op'] not in {'call', 'route', 'map', 'loop', 'dispatch', 'workflow'} for t in targets):
                raise ValueError('Dispatch targets must execute module work')
        if node['op'] == 'review':
            repair = node.get('review', {}).get('repair_node')
            if repair:
                target = next(n for n in nodes if n['id']==repair)
                if feedback_policy(target)!='replan' and repair not in ancestors[node['id']]:
                    raise ValueError('Review feedback must return to an upstream node')
            if repair and feedback_policy(target) == 'select':
                if repair not in ancestors[node['id']]:
                    raise ValueError('Review feedback must return to an upstream dispatcher')
                if not set(target['dispatch']['targets']) <= ancestors[node['id']]:
                    raise ValueError('The Reviewer must depend on all declared dispatch targets')
            if repair and not any(e['from']==node['id'] and e['to']==repair and e['on']=='revise' for e in edges):
                edges.append({'from':node['id'], 'to':repair, 'on':'revise', 'generated':True})
        refs = [*node['inputs'].values(), *([node['items']] if node['op'] == 'map' else [])]
        for ref in refs:
            if any(i not in ancestors[node['id']] for i in referenced_nodes(ref, graph)):
                raise ValueError('Every node input must follow an explicit dependency path')
        if node['op'] == 'join' and node['policy'] == 'quorum' and node['quorum'] > len(parents[node['id']]):
            raise ValueError('Quorum exceeds its upstream task count')
    controller = graph.get('controller')
    if controller is not None:
        if not isinstance(controller, dict) or set(controller) - {'binding', 'initial', 'on_events', 'instructions', 'catalog_scope'}:
            raise ValueError('Invalid graph controller')
        if not isinstance(controller.get('binding'), str) or (
            choices is not None and controller['binding'] not in choices
        ):
            raise ValueError('Choose an available controller module')
        for flag in ('initial', 'on_events'):
            if flag in controller and type(controller[flag]) is not bool:
                raise ValueError('Controller switches must be boolean')
        if (
            not isinstance(controller.get('instructions', ''), str)
            or len(controller.get('instructions', '').encode()) > 16000
        ):
            raise ValueError('Keep controller instructions within 16 KiB')
    limits = {**DEFAULT_LIMITS, **graph.get('limits', {})} if isinstance(graph.get('limits', {}), dict) else {}
    if (controller or {}).get('catalog_scope') == 'roles':
        for node in nodes:
            if (node.get('context_key') in (choices or set()) and node.get('binding')
                    and node['context_key'] != node['binding']):
                raise ValueError('A bound role cannot borrow another role\'s private context')
    if (controller or {}).get('catalog_scope') == 'project':
        for node in nodes:
            if node.get('op') in {'call', 'route', 'map', 'loop'} and node.get('binding') == controller['binding']:
                raise ValueError('Controller plans work; choose an execution participant from the Project catalog')
    reviewer = graph.get('reviewer')
    if reviewer is not None:
        if not isinstance(reviewer.get('binding'), str) or (choices is not None and reviewer['binding'] not in choices):
            raise ValueError('Choose an available independent Reviewer module')
        manager = (controller or {}).get('binding')
        if reviewer['binding'] == manager:
            raise ValueError('Controller and Reviewer must use distinct logical instances')
        for node in nodes:
            if (node.get('binding') == reviewer['binding'] or node.get('context_key') == reviewer['binding']
                    or (manager and node.get('context_key') == manager and node.get('binding') != manager)):
                raise ValueError('Work nodes cannot borrow the Reviewer or Controller context')
    if set(limits) != set(DEFAULT_LIMITS) or any(
        type(v) is not int or not 1 <= v <= DEFAULT_LIMITS[k] for k, v in limits.items()
    ):
        raise ValueError('Graph limits must stay within the shared execution budget')
    graph.update(schema_version='2.0' if modern else '1.0', revision=graph.get('revision', 1), edges=edges, limits=limits)
    if modern:
        from open_webui.execution.graph_boundaries import validate_boundaries, validate_reference_fields
        validate_boundaries(graph)
        validate_reference_fields(graph)
    from open_webui.execution.port_types import compile_ports
    compile_ports(graph)
    if type(graph['revision']) is not int or graph['revision'] < 1:
        raise ValueError('Invalid plan revision')
    if len(json.dumps(graph, ensure_ascii=False).encode()) > 256000:
        raise ValueError('Workflow graph exceeds 250 KiB')
    return graph


def validate_ref(ref):
    if not isinstance(ref, dict) or ref.get('source') not in {'input', 'node', 'report', 'literal', 'output', 'invocation'}:
        raise ValueError('Use an input, node, report or literal data reference')
    allowed = {'source', 'pointer', 'optional'} | {
        'input': set(),
        'invocation': set(),
        'node': {'node'},
        'report': {'report_ref'},
        'literal': {'value'},
        'output': {'port'},
    }[ref['source']]
    if set(ref) - allowed:
        raise ValueError('Invalid input reference fields')
    if ref['source'] == 'node' and not isinstance(ref.get('node'), str):
        raise ValueError('A node reference needs its source node ID')
    if ref['source'] == 'report' and not isinstance(ref.get('report_ref'), str):
        raise ValueError('A report reference needs its stable report ID')
    if ref['source'] == 'literal' and 'value' not in ref:
        raise ValueError('A literal needs its value')
    if ref['source'] == 'output' and (not isinstance(ref.get('port'), str) or not ID.fullmatch(ref['port'])):
        raise ValueError('A graph output reference needs its port name')
    if not isinstance(ref.get('pointer', ''), str) or (ref.get('pointer') and not ref['pointer'].startswith('/')):
        raise ValueError('Use a JSON pointer for the selected field')
    if 'optional' in ref and type(ref['optional']) is not bool:
        raise ValueError('Optional must be boolean')


def legacy_graph(template):
    """One-time compatibility translation, not an execution policy dispatcher."""
    ids = template.get('participant_ids') or [r['id'] for r in template.get('roles', [])]
    lead = template.get('lead_id') or template.get('lead_role_id') or ids[0]
    mode = template.get('mode', 'single')
    labels = {r['id']: r['name'] for r in template.get('roles', [])}
    task = {'source': 'input', 'pointer': '/task'}
    nodes, edges = [], []
    if mode == 'delegate':
        return validate_graph(
            {
                'schema_version': '1.0',
                'nodes': [
                    {
                        'id': 'answer',
                        'op': 'finish',
                        'label': 'Answer',
                        'inputs': {'answer': {'source': 'literal', 'value': ''}},
                    }
                ],
                'edges': [],
                'result_node': 'answer',
                'controller': {
                    'binding': lead,
                    'initial': True,
                    'on_events': True,
                    'instructions': template.get('instructions', ''),
                },
                'limits': {**DEFAULT_LIMITS, 'max_replans': min(5, template.get('max_rounds', 3) + 1)},
            },
            ids,
        )
    workers = [lead] if mode == 'single' else [i for i in ids if mode == 'parallel' or i != lead]
    for i in workers:
        nodes.append(
            {
                'id': i,
                'op': 'call',
                'binding': i,
                'label': labels.get(i, i),
                'instruction': template.get('instructions', ''),
                'inputs': {'task': task},
            }
        )
    if mode == 'summarize':
        nodes.append(
            {
                'id': lead,
                'op': 'call',
                'binding': lead,
                'label': labels.get(lead, lead),
                'instruction': 'Integrate the attributed results, disclose failed participants and missing evidence. '
                + template.get('instructions', ''),
                'inputs': {
                    'task': task,
                    **{i: {'source': 'node', 'node': i, 'pointer': '/report', 'optional': True} for i in workers},
                },
            }
        )
        edges.extend({'from': i, 'to': lead, 'on': 'terminal'} for i in workers)
        result = lead
    elif mode == 'single':
        result = lead
    else:
        nodes.append(
            {
                'id': 'answers',
                'op': 'join',
                'label': 'Separate answers',
                'policy': 'all_terminal',
                'inputs': {i: {'source': 'node', 'node': i, 'pointer': '/report', 'optional': True} for i in workers},
            }
        )
        edges.extend({'from': i, 'to': 'answers', 'on': 'terminal'} for i in workers)
        result = 'answers'
    return validate_graph({'schema_version': '1.0', 'nodes': nodes, 'edges': edges, 'result_node': result}, ids)


def template_graph(template):
    return validate_graph(template['graph']) if template.get('graph') else legacy_graph(template)


def apply_patch(graph, changes, states, bindings=None):
    """Apply an entire revision atomically; started nodes/edges remain immutable."""
    candidate = copy.deepcopy(graph)
    if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
        raise ValueError('Submit 1–100 typed plan changes')
    frozen = {i for i, state in states.items() if state.get('status') not in {'pending', 'ready'}}
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError('Invalid plan change')
        op = change.get('op')
        if op in {'add_node', 'update_node'}:
            if set(change) != {'op', 'node'} or not isinstance(change['node'], dict):
                raise ValueError('A node change needs its complete node definition')
            node = change['node']
            existing = next((n for n in candidate['nodes'] if n['id'] == node.get('id')), None)
            if node.get('id') in frozen or (op == 'add_node') == bool(existing):
                raise ValueError('Only unstarted nodes can be changed; node ID conflicts')
            candidate['nodes'] = [n for n in candidate['nodes'] if n['id'] != node.get('id')] + [copy.deepcopy(node)]
        elif op == 'remove_node':
            if (
                set(change) != {'op', 'node_id'}
                or change['node_id'] in frozen
                or not any(n['id'] == change['node_id'] for n in candidate['nodes'])
            ):
                raise ValueError('Only existing unstarted nodes can be removed')
            candidate['nodes'] = [n for n in candidate['nodes'] if n['id'] != change['node_id']]
            candidate['edges'] = [e for e in candidate['edges'] if change['node_id'] not in (e['from'], e['to'])]
        elif op in {'add_edge', 'remove_edge'}:
            edge = change.get('edge')
            if set(change) != {'op', 'edge'} or not isinstance(edge, dict) or edge.get('to') in frozen:
                raise ValueError('Dependencies of started nodes cannot be changed')
            if op == 'add_edge':
                candidate['edges'].append(copy.deepcopy(edge))
            else:
                candidate['edges'] = [
                    e for e in candidate['edges'] if (e['from'], e['to']) != (edge.get('from'), edge.get('to'))
                ]
        elif op == 'set_result':
            if set(change) != {'op', 'node_id'}:
                raise ValueError('Choose a result node')
            if graph.get('schema_version') == '2.0':
                candidate['outputs']['answer'] = {'source': 'node', 'node': change['node_id'], 'pointer': '/report'}
            else:
                candidate['result_node'] = change['node_id']
        elif op == 'set_output' and graph.get('schema_version') == '2.0':
            if set(change) != {'op', 'port', 'ref'} or not isinstance(change['port'], str) or not ID.fullmatch(change['port']):
                raise ValueError('An output change needs a port name and data reference')
            validate_ref(change['ref'])
            candidate['outputs'][change['port']] = copy.deepcopy(change['ref'])
        else:
            raise ValueError('Unsupported plan change')
    candidate['revision'] = graph['revision'] + 1
    candidate = validate_graph(candidate, bindings)
    for identity in frozen:
        before = [(e['from'], e.get('on'), e.get('case')) for e in graph['edges'] if e['to'] == identity]
        after = [(e['from'], e.get('on'), e.get('case')) for e in candidate['edges'] if e['to'] == identity]
        if sorted(before) != sorted(after):
            raise ValueError('Output changes cannot retarget the inputs of a started node')
    return candidate
