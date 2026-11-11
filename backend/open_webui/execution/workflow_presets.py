"""Two active graph templates; retired definitions remain addressable by old chats."""

import asyncio
import copy
import time
import uuid

from open_webui.execution.workflow_templates import compile_template, effective_bindings, template_view
from open_webui.execution.compare_workflow import normalize_compare

_lock = asyncio.Lock()
CATALOG_REVISION = 7
ADAPTIVE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, 'open-webui/workflow/adaptive-modules/dynamic/v2'))
COMPARE_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, 'open-webui/workflow/compare/modules/v1'))



def starter_templates():
    from open_webui.execution.adaptive_workflow import adaptive_definition
    adaptive = adaptive_definition() | {'preset_key': 'adaptive_modules'}
    compare = normalize_compare({'name': 'Compare', 'preset_key': 'compare_modules',
        'description': 'Multiple modules independently solve the same task; answers stay separate.',
        'instructions': '', 'max_rounds': 3, 'execution_policy': 'automatic',
        'roles': [{'id': f'executor_{i:02}', 'name': f'Executor {i:02}'} for i in (1, 2, 3)]})
    from open_webui.execution.node_program import upgrade_graph
    from open_webui.execution.composed_workflow import distribute_review_definition, enable_review_loop
    from open_webui.execution.workflow_examples import document_definition
    from open_webui.execution.graph_spec import validate_graph
    examples = [adaptive, compare, enable_review_loop(distribute_review_definition())]
    for row in examples:
        row['graph'] = upgrade_graph(row['graph'])
        row['graph'] = validate_graph(row['graph'])
    return [document_definition(row) for row in examples]

def reconcile_catalog(rows, archive=()):
    """Seed/migrate once. Custom templates and intentional deletions survive reads."""
    rows, retired = copy.deepcopy(list(rows)), copy.deepcopy(list(archive))
    if any(r.get('catalog_revision', 0) >= CATALOG_REVISION for r in [*rows, *retired]):
        return rows, retired
    if any(r.get('catalog_revision', 0) >= 3 for r in [*rows, *retired]):
        from open_webui.execution.node_program import upgrade_graph
        from open_webui.execution.graph_spec import validate_graph
        from open_webui.execution.workflow_examples import document_definition
        from open_webui.execution.composed_workflow import enable_review_loop
        for row in rows:
            graph = row.get('graph')
            documented=enable_review_loop(row) if graph else row
            upgraded = document_definition({**documented, 'graph': validate_graph(upgrade_graph(documented['graph']))}) if graph else row
            if upgraded != row:
                retired.append({**copy.deepcopy(row), 'archived_at': int(time.time()),
                                'archive_reason': 'Documented registered actions and field meanings'})
                row.update(upgraded)
                row.update(version=row.get('version', 1) + 1, updated_at=int(time.time()), catalog_revision=CATALOG_REVISION)
            row['catalog_revision'] = CATALOG_REVISION
        return rows, retired
    from open_webui.execution.adaptive_workflow import adaptive_definition

    old = next((r for r in rows if r.get('preset_key') == 'adaptive_modules'), None)
    if old is None:
        old = next((r for r in reversed(rows) if r.get('name') in {
            'Adaptive modules · Independent review', 'Adaptive Modules'}
            and (r.get('graph') or {}).get('controller') and (r.get('graph') or {}).get('reviewer')), None)
    adaptive = adaptive_definition() | {'id': ADAPTIVE_ID, 'version': 1,
        'preset_key': 'adaptive_modules', 'builtin_key': 'adaptive_modules', 'catalog_revision': CATALOG_REVISION}
    if old:
        adaptive['binding_migration'] = {'workflow_id': old['id'], 'roles': {
            'controller': old['graph']['controller']['binding'], 'reviewer': old['graph']['reviewer']['binding']}}
    compare = next((r for r in rows if r.get('preset_key') == 'compare_modules'), None)
    if compare is None:
        compare = {'id': COMPARE_ID, 'name': 'Compare', 'version': 1,
            'description': 'Multiple modules independently solve the same task; answers stay separate.',
            'instructions': '', 'max_rounds': 3, 'execution_policy': 'automatic',
            'roles': [{'id': f'executor_{i:02}', 'name': f'Executor {i:02}'} for i in (1, 2, 3)]}
    compare = normalize_compare(compare)
    compare.update(preset_key='compare_modules', builtin_key='compare_modules', catalog_revision=CATALOG_REVISION)
    result = [adaptive, compare]
    known = {(r['id'], r.get('version', 1)) for r in retired}
    for row in rows:
        if row['id'] not in {r['id'] for r in result} and (row['id'], row.get('version', 1)) not in known:
            retired.append({**row, 'archived_at': int(time.time()),
                'archive_reason': 'Superseded definition; retained for existing chats'})
    from open_webui.execution.node_program import upgrade_graph
    from open_webui.execution.graph_spec import validate_graph
    for row in result:
        row['graph'] = validate_graph(upgrade_graph(row['graph']))
    from open_webui.execution.workflow_examples import document_definition
    return [document_definition(row) for row in result], retired


def archive_template(rows, archive, identity, version):
    row = next((r for r in rows if r['id'] == identity), None)
    if row is None:
        raise ValueError('Workflow template not found')
    if version != row.get('version', 1):
        raise ValueError('This workflow changed. Reload it before deleting.')
    removed = {**copy.deepcopy(row), 'archived_at': int(time.time()), 'deleted_at': int(time.time()),
               'archive_reason': 'Deleted in Configuration', 'catalog_revision': CATALOG_REVISION}
    return [r for r in rows if r['id'] != identity], [*copy.deepcopy(archive), removed]


def restore_template(rows, archive, identity):
    if any(r['id'] == identity for r in rows):
        raise ValueError('This workflow is already active')
    candidates = [r for r in archive if r['id'] == identity and r.get('deleted_at')]
    if not candidates:
        raise ValueError('Deleted workflow template not found')
    restored = copy.deepcopy(max(candidates, key=lambda r: r.get('version', 1)))
    for key in ('archived_at', 'archive_reason', 'deleted_at'):
        restored.pop(key, None)
    restored.update(version=restored.get('version', 1) + 1, updated_at=int(time.time()))
    return [*copy.deepcopy(rows), restored], [r for r in archive if r['id'] != identity or not r.get('deleted_at')]


def complete_presets(rows):
    return reconcile_catalog(rows)[0]


async def ensure_presets():
    from open_webui.models.config import Config

    async with _lock:
        rows = await Config.get('execution.workflows') or []
        archive = await Config.get('execution.workflow_archive') or []
        completed, retired = reconcile_catalog(rows, archive)
        if completed != rows or retired != archive:
            # A single Config transaction persists active and archived entries.
            await Config.upsert({'execution.workflows': completed, 'execution.workflow_archive': retired})
        return completed


async def resolve_workflow(identity):
    """Continue a saved legacy chat without offering its template for new chats."""
    from open_webui.models.config import Config

    active = await ensure_presets()
    found = next((r for r in active if r['id'] == identity), None)
    if found:
        return found
    archived = await Config.get('execution.workflow_archive') or []
    candidates = [r for r in archived if r['id'] == identity]
    return max(candidates, key=lambda r: r.get('version', 1)) if candidates else None


def match_participants(definition, available, existing, default_model=None, replace=False):
    if not available:
        raise ValueError('Enable at least one participant in this Project first')
    roles = template_view(definition)['roles']
    choices = {p['id']: p for p in available}
    bindings = {r['id']: existing[r['id']] for r in roles if not replace and existing.get(r['id']) in choices}
    used = [choices[p] for p in bindings.values()]
    for role in roles:
        if role['id'] in bindings:
            continue
        hint = (role['name'] + ' ' + role.get('instructions', '')).lower()

        def score(p):
            identity = (p['model_id'], p.get('profile_id'))
            distinct = not any((v['model_id'], v.get('profile_id')) == identity for v in used)
            family = not any(v.get('framework_id') == p.get('framework_id') for v in used)
            role_fit = sum(
                word in hint and word in (p['name'] + ' ' + (p.get('profile_id') or '')).lower()
                for word in ('reviewer', 'planner', 'worker', 'research')
            )
            return (distinct, family, role_fit, p['model_id'] == default_model, not bool(p.get('profile_id')))

        chosen = sorted(available, key=lambda p: p['id'])
        chosen = max(chosen, key=score)
        bindings[role['id']] = chosen['id']
        used.append(chosen)
    compile_template(definition, bindings, available)
    return bindings
