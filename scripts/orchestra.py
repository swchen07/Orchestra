#!/usr/bin/env python3
"""Portable source-checkout launcher. Runtime state never belongs in Git."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / "backend" / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(argv, *, cwd=ROOT, env=None):
    subprocess.run([str(x) for x in argv], cwd=cwd, env=env, check=True)


def python_runtime():
    candidates = [os.getenv("ORCHESTRA_PYTHON"), str(VENV_PYTHON),
                  shutil.which("python3.14"), shutil.which("python3.12"),
                  shutil.which("python3.13"), sys.executable]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        check = subprocess.run([candidate, "-c", "import sys;sys.exit(not ((3,12)<=sys.version_info[:2]<(3,15)))"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if check.returncode == 0:
            return candidate
    raise RuntimeError("Install Python 3.12–3.14, or set ORCHESTRA_PYTHON to its executable.")


def node_runtime():
    candidates = [os.getenv("ORCHESTRA_NODE"), shutil.which("node")]
    brew = shutil.which("brew")
    if brew:
        prefix = subprocess.run([brew, "--prefix", "node@22"], capture_output=True, text=True)
        if prefix.returncode == 0:
            candidates.append(str(Path(prefix.stdout.strip()) / "bin/node"))
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        version = subprocess.check_output([candidate, "--version"], text=True).strip()
        if version.split(".")[0] == "v22":
            env = os.environ.copy()
            env["PATH"] = str(Path(candidate).parent) + os.pathsep + env.get("PATH", "")
            env["npm_package_version"] = json.loads((ROOT / "package.json").read_text())["version"]
            return candidate, env
    raise RuntimeError("Install Node.js 22 LTS, or set ORCHESTRA_NODE to its executable.")


def fingerprint():
    files = [ROOT / "package.json", ROOT / "package-lock.json", ROOT / "svelte.config.js",
             ROOT / "vite.config.ts", ROOT / "scripts/orchestra.py"]
    for folder in ("src", "static", "scripts/encryption", "crypto/encryption"):
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file()
                     and "target" not in p.parts and not p.is_symlink()
                     and (not p.is_relative_to(ROOT / "static/pyodide") or p.name == "pyodide-lock.json"))
    result = hashlib.sha256()
    for path in sorted(set(files)):
        result.update(str(path.relative_to(ROOT)).encode() + b"\0")
        result.update(hashlib.sha256(path.read_bytes()).digest())
    return result.hexdigest()


def browser_runtime():
    """Generate slim browser runtime in build only; leave tracked locks untouched."""
    source = ROOT / "node_modules/pyodide"
    target = ROOT / "build/pyodide"
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.is_file() and (item.suffix in {".js", ".mjs", ".wasm", ".zip"}):
            shutil.copy2(item, target / item.name)
    version = json.loads((source / "package.json").read_text())["version"]
    lock = json.loads((ROOT / "static/pyodide/pyodide-lock.json").read_text())
    for package in lock.get("packages", {}).values():
        filename = package.get("file_name", "")
        if filename and not filename.startswith(("http://", "https://")):
            package["file_name"] = f"https://cdn.jsdelivr.net/pyodide/v{version}/full/{filename}"
    (target / "pyodide-lock.json").write_text(json.dumps(lock))


def build():
    node, env = node_runtime()
    if not (ROOT / "node_modules/vite/bin/vite.js").is_file():
        raise RuntimeError("Frontend dependencies missing. Run: python3 scripts/orchestra.py setup")
    run([node, "scripts/encryption/artifact.mjs"], env=env)
    run([node, "--max-old-space-size=12288", "node_modules/vite/bin/vite.js", "build"], env=env)
    browser_runtime()
    (ROOT / "build/.orchestra-source.json").write_text(json.dumps({"fingerprint": fingerprint()}))


def ensure_build():
    stamp = ROOT / "build/.orchestra-source.json"
    try:
        current = json.loads(stamp.read_text()).get("fingerprint")
    except (OSError, ValueError):
        current = None
    if not (ROOT / "build/index.html").is_file() or current != fingerprint():
        build()


def setup(args):
    if not VENV_PYTHON.is_file():
        run([python_runtime(), "-m", "venv", VENV])
    run([VENV_PYTHON, "-m", "pip", "install", "-r", "requirements-slim.txt"], cwd=ROOT / "backend")
    _, env = node_runtime()
    env.setdefault("CYPRESS_INSTALL_BINARY", "0")
    run(["npm.cmd" if os.name == "nt" else "npm", "ci"], env=env)
    if not args.no_build:
        build()
    print("Setup complete. Run ./start.sh, then open http://localhost:8080", flush=True)


def backend_environment(args):
    env = os.environ.copy()
    data = Path(os.getenv("ORCHESTRA_DATA_DIR", str(ROOT / "backend/data"))).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)
    key_file = data / ".webui_secret_key"
    legacy_key = ROOT / "backend/.webui_secret_key"
    if not env.get("WEBUI_SECRET_KEY"):
        if data == (ROOT / "backend/data").resolve() and legacy_key.is_file():
            key_file = legacy_key
        elif not key_file.exists():
            try:
                fd = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(secrets.token_urlsafe(48))
            except FileExistsError:
                pass
        env["WEBUI_SECRET_KEY"] = key_file.read_text().strip()
        if not env["WEBUI_SECRET_KEY"]:
            raise RuntimeError("The saved authentication key is empty; restore a valid key.")
    env.update(DATA_DIR=str(data), FRONTEND_BUILD_DIR=str(ROOT / "build"), USE_SLIM_DOCKER="true")
    env.setdefault("CORS_ALLOW_ORIGIN", f"http://localhost:{args.port};http://127.0.0.1:{args.port};http://localhost:5173;http://127.0.0.1:5173")
    env.setdefault("ENABLE_VERSION_UPDATE_CHECK", "false")
    return env


def backend(args):
    if not VENV_PYTHON.is_file():
        raise RuntimeError("Backend dependencies missing. Run: python3 scripts/orchestra.py setup")
    if args.command == "start":
        ensure_build()
    check = socket.socket()
    try:
        check.bind((args.host, args.port))
    except OSError:
        raise RuntimeError(f"Port {args.port} is in use. Choose --port <free-port>; no existing service was stopped.") from None
    finally:
        check.close()
    print(f"Orchestra / Open WebUI: http://localhost:{args.port} (Ctrl+C to stop)", flush=True)
    run([VENV_PYTHON, "-m", "uvicorn", "open_webui.main:app", "--host", args.host, "--port", args.port],
        cwd=ROOT / "backend", env=backend_environment(args))


def frontend(args):
    node, env = node_runtime()
    ensure_build()
    env["ORCHESTRA_DEV"] = "true"
    run([node, "scripts/encryption/artifact.mjs"], env=env)
    run([node, "node_modules/vite/bin/vite.js", "dev", "--host", args.host, "--port", args.port, "--strictPort"], env=env)


def doctor(args):
    print(f"Source: {ROOT}")
    print(f"Python: {python_runtime()}")
    print(f"Node 22: {node_runtime()[0]}")
    print(f"Backend installed: {VENV_PYTHON.is_file()}")
    print(f"Frontend built: {(ROOT / 'build/index.html').is_file()}")
    for name in ("git", "docker", "hermes", "claude", "codex", "dsh"):
        print(f"{name}: {shutil.which(name) or 'optional / not installed'}")


def main():
    parser = argparse.ArgumentParser(description="Orchestra local web launcher (Open WebUI based)")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("setup", help="Install slim backend, frontend dependencies and build")
    install.add_argument("--no-build", action="store_true")
    sub.add_parser("build", help="Build local web UI without rewriting tracked Pyodide files")
    sub.add_parser("doctor", help="Inspect local prerequisites; do not change runtime configuration")
    for name, port in (("start", 8080), ("backend", 8080), ("frontend", 5173)):
        cmd = sub.add_parser(name)
        cmd.add_argument("--host", default="127.0.0.1")
        cmd.add_argument("--port", type=int, default=port)
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup(args)
        elif args.command == "build":
            build()
        elif args.command == "doctor":
            doctor(args)
        elif args.command == "frontend":
            frontend(args)
        else:
            backend(args)
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f"Orchestra: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
