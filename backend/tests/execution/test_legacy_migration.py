import importlib.util
import unittest
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


class MigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_on_isolated_database(self):
        path = Path(__file__).parents[2] / 'open_webui/migrations/versions/84ab20e612cd_add_workbench_runs.py'
        spec = importlib.util.spec_from_file_location('migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = create_engine('sqlite://')
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertEqual(set(inspect(connection).get_table_names()), {'workbench_run', 'workbench_event'})
                migration.downgrade()
                self.assertEqual(inspect(connection).get_table_names(), [])
        engine.dispose()
