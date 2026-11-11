"""Global role templates, Project-local model bindings, immutable per-run compilation."""

import copy
import re
import uuid

from open_webui.execution.graph_spec import legacy_graph, validate_graph

from open_webui.execution.workflow_config import validate_workflow
from open_webui.execution.module_naming import ModuleNames
from open_webui.execution.compare_workflow import is_comparison_graph


def template_view(value):
    roles = value.get('roles') or [
        {
            'id': f'role_{index + 1}',
            'name': 'Lead' if identity == value.get('lead_id') else f'Worker {index + 1}',
            'instructions': '',
        }
        for index, identity in enumerate(value.get('participant_ids', []))
    ]
    lead = value.get('lead_role_id') or next(
        (r['id'] for r, identity in zip(roles, value.get('participant_ids', [])) if identity == value.get('lead_id')),
        roles[0]['id'] if roles else '',
    )
    view = {
        k: copy.deepcopy(v)
        for k, v in value.items()
        if k in {'id', 'name', 'mode', 'instructions', 'max_rounds', 'version', 'description', 'execution_policy', 'preset_key', 'builtin_key', 'example'}
    }
    view.update(roles=copy.deepcopy(roles), lead_role_id=lead)
    view['graph'] = copy.deepcopy(value['graph']) if value.get('graph') else legacy_graph(view)
    if 'version' in value:
        from open_webui.execution.node_program import legacy_context_keys
        view['graph'] = legacy_context_keys(view['graph'])
    labels = ModuleNames(view).labels
    for role in view['roles']:
        role['name'] = labels[role['id']]
    if view.get('description', '').startswith('Manager-generated graphs,'):
        view['description'] = view['description'].replace('Manager-generated', 'Controller-generated', 1)
    return view


def validate_template(value):
    if not isinstance(value, dict) or set(value) - {
        'id',
        'name',
        'mode',
        'roles',
        'lead_role_id',
        'instructions',
        'max_rounds',
        'version',
        'description',
        'graph',
        'execution_policy',
        'preset_key',
        'builtin_key',
        'example',
    }:
        raise ValueError('A workflow template defines roles and rules; bind models in a Project')
    if value.get('preset_key') not in {None, 'adaptive_modules', 'compare_modules'}:
        raise ValueError('Choose Adaptive or Compare as the template starting point')
    roles = value.get('roles')
    if not isinstance(roles, list) or not 1 <= len(roles) <= 8:
        raise ValueError('Define 1–8 workflow roles')
    seen = set()
    for role in roles:
        if (
            not isinstance(role, dict)
            or set(role) - {'id', 'name', 'instructions'}
            or not isinstance(role.get('id'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', role['id'])
            or role['id'] in seen
            or not isinstance(role.get('name'), str)
            or not 1 <= len(role['name'].strip()) <= 100
            or not isinstance(role.get('instructions', ''), str)
            or len(role.get('instructions', '').encode()) > 8000
        ):
            raise ValueError('Each role needs a unique ID, name and instructions up to 8 KiB')
        seen.add(role['id'])
    description = value.get('description', '')
    example = value.get('example')
    if example is not None and (not isinstance(example, dict) or set(example) != {'purpose', 'task', 'expected'}
            or any(not isinstance(example[k], str) or len(example[k]) > limit
                   for k, limit in (('purpose', 1000), ('task', 12000), ('expected', 4000)))):
        raise ValueError('An example needs purpose, task and expected result text')
    if not isinstance(description, str) or len(description) > 500:
        raise ValueError('Keep the workflow description within 500 characters')
    if value.get('graph'):
        if value.get('id') and not re.fullmatch(r'[0-9a-f-]{36}', value['id']):
            raise ValueError('Invalid workflow ID')
        if not isinstance(value.get('name'), str) or not 1 <= len(value['name'].strip()) <= 100:
            raise ValueError('Enter a workflow name')
        instructions = value.get('instructions', '')
        if not isinstance(instructions, str) or len(instructions.encode()) > 16000:
            raise ValueError('Keep shared instructions within 16 KiB')
        lead = value.get('lead_role_id') or roles[0]['id']
        if lead not in seen:
            raise ValueError('Choose an existing lead role')
        policy = value.get('execution_policy', 'automatic')
        if policy not in {'automatic', 'review', 'step'}:
            raise ValueError('Choose Automatic, Review plan or Step')
        graph = validate_graph(value['graph'])
        return {
            'id': value.get('id') or str(uuid.uuid4()),
            'name': value['name'].strip(),
            'roles': copy.deepcopy(roles),
            'lead_role_id': lead,
            'instructions': instructions,
            'description': description,
            'graph': graph,
            'execution_policy': policy,
            'max_rounds': 3,
            **({'preset_key': value['preset_key']} if value.get('preset_key') else {}),
            **({'example': copy.deepcopy(example)} if example is not None else {}),
        }
    check = validate_workflow(
        {
            k: v
            for k, v in value.items()
            if k not in {'roles', 'lead_role_id', 'description', 'graph', 'execution_policy'}
        }
        | {'participant_ids': [r['id'] for r in roles], 'lead_id': value.get('lead_role_id')},
        roles,
    )
    return {k: v for k, v in check.items() if k not in {'participant_ids', 'lead_id'}} | {
        'roles': copy.deepcopy(roles),
        'lead_role_id': check['lead_id'],
        'description': description,
    }


def effective_bindings(definition, project_bindings):
    if definition['id'] in project_bindings:
        return project_bindings[definition['id']]
    migration = definition.get('binding_migration') or {}
    if migration.get('workflow_id') in project_bindings:
        previous = project_bindings[migration['workflow_id']]
        return {current: previous[old] for current, old in migration['roles'].items() if old in previous}
    if definition.get('legacy_bindings'):
        return definition['legacy_bindings']
    # Preserve old saved examples until the Project explicitly rebinds them.
    return {f'role_{index + 1}': identity for index, identity in enumerate(definition.get('participant_ids', []))}


def compile_template(definition, bindings, available):
    view = template_view(definition)
    from open_webui.execution.node_program import upgrade_graph
    view['graph'] = upgrade_graph(view['graph'])
    template = validate_template({k: v for k, v in view.items() if k not in {'version', 'preset_key'}})
    if not isinstance(bindings, dict) or set(bindings) != {r['id'] for r in template['roles']}:
        raise ValueError('Bind every workflow role in this Project first')
    choices = {p['id']: p for p in available}
    resolved = []
    for role in template['roles']:
        selected = bindings.get(role['id'])
        if not isinstance(selected, str) or selected not in choices:
            raise ValueError('A role references a participant not enabled in this Project')
        resolved.append(
            {
                **copy.deepcopy(choices[selected]),
                'id': role['id'],
                'binding_id': selected,
                'name': role['name'] + ' · ' + choices[selected]['name'],
                'role_instructions': role.get('instructions', ''),
            }
        )
    compiled = {k: v for k, v in template.items() if k not in {'roles', 'lead_role_id'}} | {
        'participant_ids': [r['id'] for r in resolved],
        'lead_id': template['lead_role_id'],
    }
    compiled['graph'] = validate_graph(template['graph'], set(choices) | {r['id'] for r in resolved})
    return compiled | {'roles': template['roles'], 'preset_key': definition.get('preset_key')}, resolved


def module_system_prompt(template, binding):
    """A Compare slot never inherits the lead's special final-answer behavior."""
    if (template.get('graph') or {}).get('schema_version') == '2.0':
        return ('You are an isolated callable module in a workflow graph. Execute the current node responsibilities '
                'using declared inputs and your own retained dialogue. Your role name does not grant access to peers\' '
                'private contexts or bypass the declared output contract.\n'
                + binding.get('role_instructions', '') + '\nShared constraints:\n' + template.get('instructions', ''))
    if is_comparison_graph(template):
        return ('You are an independent module in Compare. Solve the complete user task and return '
                'your own full answer in the requested format. Other modules receive the same task; '
                'their private conversations are not available to you. '
                + binding.get('role_instructions', '')
                + '\nShared workflow constraints:\n' + template.get('instructions', ''))
    role = next((r for r in template.get('roles', []) if r['id'] == binding['id']), {})
    if not (template.get('graph') or {}).get('controller') and not template.get('mode'):
        return ('You are a module in a predefined execution graph. Complete the assigned work package '
                'using the supplied inputs and your own private history. Follow the node output contract; '
                'do not assume other modules receive the same task.\n'
                + binding.get('role_instructions', '') + '\nShared workflow constraints:\n'
                + template.get('instructions', ''))
    if not role and (template.get('graph') or {}).get('controller'):
        return ('You are an isolated execution module. Complete only the assigned work package using explicit inputs '
                'and your own retained history. Return an attributed report with evidence and uncertainty. '
                'Do not assume access to other modules or approve your own result.\n'
                + template.get('instructions', ''))
    return (
        f'You are the {role.get("name", binding["name"])} role (role ID: {binding["id"]}) '
        f'in the {template["name"]} workflow. Follow instructions addressed to your role. '
        + ('You produce the final user-facing answer. ' if binding['id'] == template['lead_id']
           else 'Return your own attributed report to the lead; final-answer formatting addressed only to the lead does not apply to your report. ')
        + binding.get('role_instructions', '')
        + '\nShared workflow constraints:\n' + template.get('instructions', '')
    )


def compile_chat_template(definition, project_bindings, overrides, available, models):
    """Resolve chat-local choices without writing to the template or Project defaults."""
    bindings = copy.deepcopy(effective_bindings(definition, project_bindings))
    edits = {}
    if overrides is not None:
        if (
            not isinstance(overrides, dict)
            or set(overrides) - {'workflow_id', 'roles', 'executor_pool'}
            or overrides['workflow_id'] != definition['id']
            or not isinstance(overrides['roles'], dict)
            or set(overrides['roles']) - {r['id'] for r in template_view(definition)['roles']}
        ):
            raise ValueError('Chat role choices must belong to the selected workflow')
        edits = overrides['roles']
        choices = {p['id']: p for p in available}
        model_choices = {m['id']: m for m in models}
        for role_id, value in edits.items():
            if (
                not isinstance(value, dict)
                or set(value) - {'participant_id', 'effort'}
                or not isinstance(value.get('participant_id'), str)
                or value['participant_id'] not in choices
            ):
                raise ValueError('Choose a participant enabled in this Project for each chat role')
            participant = choices[value['participant_id']]
            model = model_choices.get(participant.get('model_id'), {})
            effort = value.get('effort')
            if effort is not None and (
                not isinstance(effort, str) or effort not in model.get('execution', {}).get('effort_options', [])
            ):
                raise ValueError('This role model does not advertise the selected effort')
            bindings[role_id] = participant['id']
    template, resolved = compile_template(definition, bindings, available)
    pool = (overrides or {}).get('executor_pool')
    if pool is not None:
        if (not isinstance(pool, list) or not 1 <= len(pool) <= 80
                or any(not isinstance(i, str) or i not in {p['id'] for p in available} for i in pool)):
            raise ValueError('Executor pool must use participants enabled in this Project')
        template['executor_pool'] = list(dict.fromkeys(pool))
    for participant in resolved:
        edit = edits.get(participant['id'])
        participant['binding_source'] = 'chat' if edit is not None else 'project'
        if edit is not None and 'effort' in edit:
            participant['effort'] = edit['effort']
    return template, resolved


def normalize_graph_template(value):
    """One template contract: saved graph, or a Controller-generated graph."""
    result = validate_template(value)
    if not result.get('graph'):
        raise ValueError('A workflow template needs a graph definition')
    from open_webui.execution.node_program import upgrade_graph
    upgraded = upgrade_graph(result['graph'])
    workflow_bindings = set()
    for node in upgraded['nodes']:
        if str(node.get('binding', '')).startswith('workflow:'):
            binding = node.get('binding', '')
            try:
                if not binding.startswith('workflow:') or str(uuid.UUID(binding[9:])) != binding[9:]:
                    raise ValueError()
            except (ValueError, AttributeError):
                raise ValueError('Select a saved Workflow module') from None
            workflow_bindings.add(binding)
    # This validates structure, never authorization. compile_template and the
    # runtime resolve these IDs against the current Project-bound catalog.
    result['graph'] = validate_graph(upgraded, {r['id'] for r in result['roles']} | workflow_bindings)
    return result
