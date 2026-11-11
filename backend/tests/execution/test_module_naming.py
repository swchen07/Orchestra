import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from open_webui.execution.module_naming import ModuleNames, record_module_names, legacy_record_names
from open_webui.execution.workflow_templates import template_view
from open_webui.execution.workflow_memory import context_signature


def template():
    return {'id': 'workflow', 'lead_id': 'manager', 'roles': [
        {'id': 'manager', 'name': 'Manager'}, {'id': 'worker_b', 'name': 'Worker B'},
        {'id': 'worker_c', 'name': 'Worker C'}, {'id': 'reviewer', 'name': 'Reviewer'}],
        'graph': {'controller': {'binding': 'manager'}, 'reviewer': {'binding': 'reviewer'}}}


class ModuleNamingTests(unittest.TestCase):
    def test_legacy_projection_is_cached_scoped_and_does_not_rewrite_journals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            own, foreign, current = (str(uuid.uuid4()) for _ in range(3))
            scope = {'user_id': 'owner', 'chat_id': 'chat', 'project_id': 'project', 'template': template()}
            rows = {
                own: {**scope, 'participants': [{'id': 'child', 'role_id': 'worker_b'}]},
                foreign: {**scope, 'user_id': 'other', 'participants': [{'id': 'foreign-private-instance'}]},
                current: {**scope, 'continuation': {'previous_run_ids': [own, foreign]}, 'participants': []},
            }
            for identity, row in rows.items():
                (root / (identity + '.json')).write_text(json.dumps(row))
            path = root / (current + '.json'); original = path.read_bytes()
            legacy_record_names.cache_clear()
            args = (str(path), path.stat().st_mtime_ns)
            view = dict(legacy_record_names(*args))
            self.assertEqual(view['child'], 'Executor 01.02')
            self.assertNotIn('foreign-private-instance', view)
            view['child'] = 'caller mutation'
            self.assertEqual(dict(legacy_record_names(*args))['child'], 'Executor 01.02')
            self.assertGreaterEqual(legacy_record_names.cache_info().hits, 1)
            self.assertEqual(path.read_bytes(), original)
            legacy_record_names.cache_clear()

    def test_controller_reviewer_and_execution_slots_are_distinct(self):
        names = ModuleNames(template())
        self.assertEqual(names.labels, {'manager': 'Controller', 'reviewer': 'Reviewer',
                                       'worker_b': 'Executor 01', 'worker_c': 'Executor 02'})

    def test_dynamic_task_labels_never_become_module_identities(self):
        source = {'template': template(), 'participants': [
            {'id': 'collector', 'role_id': 'worker_b', 'name': 'Join transformed value and audit'},
            {'id': 'reconciler', 'role_id': 'worker_b', 'name': 'Final JSON reconciliation'}]}
        before = copy.deepcopy(source)
        names = record_module_names(source)
        self.assertEqual(names['collector'], 'Executor 01.02')
        self.assertEqual(names['reconciler'], 'Executor 01.03')
        self.assertEqual(source, before)

    def test_numbering_survives_unused_instances_and_later_turns(self):
        first = {'template': template(), 'participants': [{'id': 'collector', 'role_id': 'worker_b'}]}
        second = {'template': template(), 'participants': [{'id': 'audit', 'role_id': 'worker_c'}]}
        names = ModuleNames(template(), previous=[first, second])
        self.assertEqual(names.name('collector'), 'Executor 01.02')
        self.assertEqual(names.name('another', slot='worker_b'), 'Executor 01.03')
        self.assertEqual(names.name('audit'), 'Executor 02.02')
        self.assertEqual(names.name('collector', slot='worker_b'), 'Executor 01.02')

    def test_declared_alias_is_preserved_separately_from_number(self):
        value = template(); value['roles'][1]['name'] = 'Researcher'
        names = ModuleNames(value)
        self.assertEqual(names.name('worker_b'), 'Executor 01 · Researcher')
        self.assertEqual(names.name('clone', slot='worker_b'), 'Executor 01.02 · Researcher')

    def test_work_context_using_controller_backend_does_not_become_another_controller(self):
        names = ModuleNames(template())
        self.assertEqual(names.name('collector', slot='manager'), 'Executor 03')
        self.assertEqual(names.name('collector', slot='manager'), 'Executor 03')
        self.assertEqual(names.name('manager'), 'Controller')

    def test_label_changes_do_not_change_ids_bindings_or_context_signature(self):
        value = template(); before = copy.deepcopy(value)
        binding = {'id': 'worker_b', 'name': 'Worker B', 'framework_id': 'hermes', 'model_id': 'flash'}
        changed = {**binding, 'name': 'Executor 01'}
        self.assertEqual(context_signature(value, binding), context_signature(value, changed))
        ModuleNames(value)
        self.assertEqual(value, before)

    def test_template_projection_keeps_legacy_storage_keys(self):
        value = {**template(), 'name': 'Adaptive'}; before = copy.deepcopy(value)
        view = template_view(value)
        self.assertEqual(view['roles'][0]['name'], 'Controller')
        self.assertEqual(view['roles'][1]['name'], 'Executor 01')
        self.assertEqual([r['id'] for r in view['roles']], [r['id'] for r in value['roles']])
        self.assertEqual(value, before)
