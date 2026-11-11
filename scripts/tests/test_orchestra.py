"""Launch checks without importing the app or touching existing local data."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("orchestra", Path(__file__).parents[1] / "orchestra.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LocalLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.args = argparse.Namespace(port=18080)
        self.root_patch = patch.object(launcher, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.env_patch = patch.dict(os.environ, {}, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_fresh_data_creates_persistent_private_authentication_key(self):
        first = launcher.backend_environment(self.args)
        key = self.root / "backend/data/.webui_secret_key"
        self.assertGreaterEqual(len(first["WEBUI_SECRET_KEY"]), 48)
        self.assertEqual(launcher.backend_environment(self.args)["WEBUI_SECRET_KEY"], first["WEBUI_SECRET_KEY"])
        if os.name != "nt":
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_existing_install_preserves_authentication_and_custom_data_is_separate(self):
        legacy = self.root / "backend/.webui_secret_key"
        legacy.parent.mkdir()
        legacy.write_text("existing-install-key")
        self.assertEqual(launcher.backend_environment(self.args)["WEBUI_SECRET_KEY"], legacy.read_text())
        with patch.dict(os.environ, {"ORCHESTRA_DATA_DIR": str(self.root / "separate")}):
            isolated = launcher.backend_environment(self.args)
        self.assertNotEqual(isolated["WEBUI_SECRET_KEY"], legacy.read_text())
        self.assertEqual(legacy.read_text(), "existing-install-key")

    def test_explicit_environment_key_is_not_overwritten(self):
        with patch.dict(os.environ, {"WEBUI_SECRET_KEY": "external-managed-key"}):
            self.assertEqual(launcher.backend_environment(self.args)["WEBUI_SECRET_KEY"], "external-managed-key")
        self.assertFalse((self.root / "backend/data/.webui_secret_key").exists())

    def test_browser_runtime_only_writes_generated_build_files(self):
        source = self.root / "node_modules/pyodide"
        source.mkdir(parents=True)
        (source / "package.json").write_text('{"version":"314.0.3"}')
        (source / "pyodide.mjs").write_text("export {};")
        (source / "pyodide.asm.wasm").write_bytes(b"wasm-runtime")
        lock = self.root / "static/pyodide/pyodide-lock.json"
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({"packages": {"demo": {"file_name": "demo.whl"}}}))
        before = hashlib.sha256(lock.read_bytes()).digest()
        launcher.browser_runtime()
        self.assertEqual(hashlib.sha256(lock.read_bytes()).digest(), before)
        self.assertEqual((self.root / "build/pyodide/pyodide.mjs").read_text(), "export {};")
        generated = json.loads((self.root / "build/pyodide/pyodide-lock.json").read_text())
        self.assertEqual(generated["packages"]["demo"]["file_name"], "https://cdn.jsdelivr.net/pyodide/v314.0.3/full/demo.whl")

    def test_busy_port_does_not_start_or_stop_any_service(self):
        self.args.command = "backend"
        self.args.host = "127.0.0.1"
        with patch.object(launcher, "VENV_PYTHON", Path(__file__)), patch.object(launcher.socket, "socket") as socket_factory, patch.object(launcher, "run") as run:
            socket_factory.return_value.bind.side_effect = OSError("busy")
            with self.assertRaisesRegex(RuntimeError, "in use"):
                launcher.backend(self.args)
            socket_factory.return_value.close.assert_called_once()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
