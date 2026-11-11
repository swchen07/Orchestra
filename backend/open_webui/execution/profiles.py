"""Behavior profiles are separate from provider credentials and project topology."""

import hashlib
import tomllib
from pathlib import Path
import yaml
from open_webui.execution.dsh_config import public_dsh_presets

ROLES = {
    'planner': ('Planner', 'Clarify goals and constraints, then break the task into actionable steps.'),
    'worker': ('Worker', 'Complete the assigned work and clearly state results and unresolved issues.'),
    'reviewer': ('Reviewer', 'Independently verify results and identify concrete errors, gaps and improvements.'),
}


def discover_profiles(directory=None, home=None, custom=()):
    home = Path(home or Path.home())
    profiles = []
    for framework in ('direct', 'claude-code', 'hermes', 'codex', 'dsh'):
        for role, (name, prompt) in ROLES.items():
            profiles.append(
                {
                    'id': f'role:{framework}:{role}',
                    'framework_id': framework,
                    'name': name,
                    'kind': 'role',
                    'source': 'Role template',
                    'prompt': prompt,
                    'available': True,
                    'execution_mode': 'instructions',
                }
            )
    roots = [home / '.claude/agents']
    if directory:
        roots.append(Path(directory) / '.claude/agents')
    for root in roots:
        for path in sorted(root.glob('*.md'))[:50]:
            try:
                if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 65536:
                    continue
                text = path.read_text()
            except (OSError, UnicodeError):
                continue
            config = {}
            if text.startswith('---\n'):
                parts = text.split('---', 2)
                if len(parts) == 3:
                    try:
                        config = yaml.safe_load(parts[1]) or {}
                    except yaml.YAMLError:
                        continue
                    text = parts[2].strip()
            if not isinstance(config, dict):
                continue
            identity = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:20]
            profiles.append(
                {
                    'id': 'claude-agent:' + identity,
                    'framework_id': 'claude-code',
                    'name': str(config.get('name') or path.stem),
                    'kind': 'native-agent',
                    'source': 'Claude Code agents',
                    'prompt': text,
                    'model_hint': config.get('model'),
                    'available': True,
                    'native_name': str(config.get('name') or path.stem),
                    'description': str(config.get('description') or path.stem),
                    'execution_mode': 'native-agent',
                    'note': 'Native agent for API providers; native OAuth uses prompt compatibility. Selected model and read-only tools take precedence.',
                }
            )
    try:
        config = tomllib.loads((home / '.codex/config.toml').read_text())
    except (OSError, ValueError):
        config = {}
    codex_profiles = config.get('profiles', {})
    for name, value in (codex_profiles if isinstance(codex_profiles, dict) else {}).items():
        if isinstance(value, dict):
            profiles.append(
                {
                    'id': 'codex-profile:' + name,
                    'framework_id': 'codex',
                    'name': name,
                    'kind': 'native-profile',
                    'source': 'Codex config.toml',
                    'model_hint': value.get('model'),
                    'reasoning_effort': value.get('model_reasoning_effort'),
                    'available': True,
                    'execution_mode': 'config-overlay',
                    'config_overrides': {k: v for k, v in value.items() if k in {'model_reasoning_effort', 'model_verbosity', 'developer_instructions', 'personality'}},
                    'note': 'Legacy profile: supported behavior fields are applied; model and tool permissions remain explicit',
                }
            )
    for path in sorted((home / '.codex').glob('*.config.toml'))[:50]:
        try:
            value = tomllib.loads(path.read_text())
        except (OSError, ValueError):
            continue
        name = path.name.removesuffix('.config.toml')
        if name and len(name) < 100:
            profiles.append(
                {
                    'id': 'codex-profile-v2:' + name, 'framework_id': 'codex', 'name': name,
                    'native_name': name, 'kind': 'native-profile', 'source': 'Codex profile file',
                    'model_hint': value.get('model'), 'available': True, 'execution_mode': 'native-profile',
                    'note': 'Loaded by Codex; selected model and read-only permissions take precedence',
                }
            )
    profiles.extend(public_dsh_presets(home))
    profiles.extend(custom)
    return profiles


def public_profiles(profiles):
    return [{k: v for k, v in profile.items() if k not in {'prompt', 'config_overrides'}} for profile in profiles]


def validate_profile(form):
    if not isinstance(form, dict) or set(form) - {'name', 'framework_id', 'prompt'}:
        raise ValueError('Profile 格式不正确')
    if form.get('framework_id') not in {'direct', 'claude-code', 'hermes', 'codex', 'dsh'}:
        raise ValueError('Unsupported framework')
    if not isinstance(form.get('name'), str) or not form['name'].strip() or len(form['name']) > 100:
        raise ValueError('请输入 Profile 名称')
    if not isinstance(form.get('prompt'), str) or not form['prompt'].strip() or len(form['prompt'].encode()) > 32768:
        raise ValueError('请输入不超过 32 KiB 的角色指令')
    return {
        **form,
        'name': form['name'].strip(),
        'kind': 'role',
        'source': 'Project role',
        'available': True,
        'execution_mode': 'instructions',
    }
