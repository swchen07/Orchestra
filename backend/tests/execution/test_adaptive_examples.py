import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from open_webui.execution.adaptive_examples import cases, definition, matrix, select_flash, authorize_example_chat, followup_prompt
from open_webui.execution.workflow_templates import validate_template


class AdaptiveExampleTests(unittest.TestCase):
    def test_followup_is_anchored_to_the_accepted_initial_run_without_supplying_recall_values(self):
        ref = '12345678-1234-4234-8234-123456789abc'
        value = followup_prompt(cases()[2], ref)
        self.assertIn(ref, value)
        self.assertIn('not the earliest failed/rejected attempt', value)
        self.assertNotIn('saffron', value)
        self.assertNotIn('amber', value)
    def test_saved_suite_cannot_write_to_another_owner_or_project_chat(self):
        authorize_example_chat(SimpleNamespace(user_id='owner', folder_id='project'), 'owner', 'project')
        for chat in (None, SimpleNamespace(user_id='other', folder_id='project'),
                     SimpleNamespace(user_id='owner', folder_id='elsewhere')):
            with self.assertRaises(HTTPException):
                authorize_example_chat(chat, 'owner', 'project')

    def test_examples_begin_without_a_predefined_work_topology(self):
        value = validate_template(definition())
        self.assertEqual(len(value['graph']['nodes']), 1)
        self.assertEqual(value['graph']['nodes'][0]['op'], 'finish')
        self.assertTrue(value['graph']['controller']['initial'])
        self.assertEqual(value['graph']['reviewer']['binding'], 'reviewer')
        self.assertEqual({r['id'] for r in value['roles']}, {'controller', 'reviewer'})
        self.assertEqual(value['graph']['controller']['catalog_scope'], 'project')
        self.assertEqual(len(cases()), 3)
        self.assertTrue(all(c['followup'] for c in cases()))

    def test_matrix_has_nine_controlled_teams_and_one_mixed_team(self):
        chosen = {f: {'id': f} for f in ('dsh', 'claude-code', 'hermes')}
        jobs = matrix(chosen)
        self.assertEqual(len(jobs), 10)
        self.assertTrue(all(len(set(j['bindings'].values())) == 1 for j in jobs[:9]))
        self.assertTrue(all(set(j['bindings']) == {'controller', 'reviewer'} for j in jobs))
        self.assertEqual(jobs[-1]['bindings']['controller'], 'claude-code')
        self.assertEqual(set(jobs[-1]['executor_pool']), {'hermes', 'dsh', 'claude-code'})
        self.assertTrue(all(len(j['executor_pool']) == 1 for j in jobs[:9]))

    def test_flash_selection_does_not_substitute_pro_or_unenabled_models(self):
        models = [{'id': f, 'execution': {'model_id': 'deepseek-v4-flash'}} for f in ('dsh', 'claude-code', 'hermes')]
        participants = [{'id': f, 'model_id': f, 'framework_id': f} for f in ('dsh', 'claude-code', 'hermes')]
        self.assertEqual(len(select_flash(models, participants)), 3)
        models[0]['execution']['model_id'] = 'deepseek-v4-pro'
        with self.assertRaises(HTTPException):
            select_flash(models, participants)
