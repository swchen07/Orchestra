import sys
import types
import unittest
from unittest.mock import patch

from open_webui.execution.workflows import workflow_models
from open_webui.execution.workflow_presets import ensure_presets, resolve_workflow


class ColdWorkflowCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_is_atomic_idempotent_and_retired_chat_definition_still_resolves(self):
        import copy
        legacy = {'id': 'legacy-compare', 'name': 'Compare', 'version': 2,
                  'roles': [{'id': 'critic', 'name': 'Critic', 'instructions': 'Old independent perspective'}]}
        values = {'execution.workflows': [copy.deepcopy(legacy)]}
        writes = []

        class FakeConfig:
            @staticmethod
            async def get(key):
                return copy.deepcopy(values.get(key))

            @staticmethod
            async def upsert(update):
                writes.append(copy.deepcopy(update))
                values.update(copy.deepcopy(update))

        module = types.ModuleType('open_webui.models.config')
        module.Config = FakeConfig
        with patch.dict(sys.modules, {'open_webui.models.config': module}):
            active = await ensure_presets()
            self.assertEqual([r['name'] for r in active], ['Adaptive Modules', 'Compare'])
            self.assertEqual(await ensure_presets(), active)
            self.assertEqual(len(writes), 1)
            self.assertEqual(set(writes[0]), {'execution.workflows', 'execution.workflow_archive'})
            archived = await resolve_workflow('legacy-compare')
            self.assertEqual(archived['roles'], legacy['roles'])
            self.assertEqual(archived['version'], 2)
            self.assertTrue(archived['archived_at'])
            self.assertIsNone(await resolve_workflow('missing'))

    async def test_a_workflow_loads_models_without_a_prior_browser_catalog_request(self):
        request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(MODELS={})))
        user = object()
        expected = [{'id': 'flash', 'execution': {'framework_id': 'dsh'}}]

        async def load(incoming, **kwargs):
            self.assertIs(incoming, request)
            self.assertIs(kwargs['user'], user)
            request.app.state.MODELS = {m['id']: m for m in expected}
            return expected

        module = types.ModuleType('open_webui.utils.models')
        module.get_all_models = load
        with patch.dict(sys.modules, {'open_webui.utils.models': module}):
            self.assertEqual(await workflow_models(request, user), expected)
        self.assertIn('flash', request.app.state.MODELS)
