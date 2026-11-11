"""Read installed DSH preset declarations without starting its desktop service."""

import json
from pathlib import Path
import struct

import yaml

ASAR = Path('/Applications/DeepSeek Harness.app/Contents/Resources/app.asar')
WEB_APP = 'dsh/node_modules/@deepseek-ai/dsh-web-app/'


class JsExpression(str):
    pass


class DshLoader(yaml.SafeLoader):
    pass


class DshDumper(yaml.SafeDumper):
    pass


DshLoader.add_constructor('tag:yaml.org,2002:js', lambda loader, node: JsExpression(loader.construct_scalar(node)))
DshDumper.add_representer(
    JsExpression, lambda dumper, value: dumper.represent_scalar('tag:yaml.org,2002:js', str(value))
)


class InstalledDsh:
    def __init__(self, path=ASAR):
        self.path = Path(path)
        with self.path.open('rb') as stream:
            prefix = stream.read(16)
            _, size, _, json_size = struct.unpack('<4I', prefix)
            if json_size > 16 * 1024 * 1024:
                raise ValueError('DSH package header exceeds the limit')
            self.header = json.loads(stream.read(json_size))
            self.offset = 8 + size

    def text(self, path):
        row = self.header
        for part in path.split('/'):
            row = row['files'][part]
        if row.get('unpacked') or row.get('size', 0) > 1024 * 1024:
            raise ValueError('Unsupported DSH package entry')
        with self.path.open('rb') as stream:
            stream.seek(self.offset + int(row['offset']))
            return stream.read(row['size']).decode()


def preset_rows(home=None):
    native = InstalledDsh()
    package = json.loads(native.text(WEB_APP + 'package.json'))
    declarations = []
    for name in package['dsh']['bundle']['patch']:
        if '/presets/' in name:
            declarations.extend(yaml.load(native.text(WEB_APP + name.removeprefix('./')), Loader=DshLoader) or [])
    rows = {row['id']: row for patch in declarations for row in patch.get('insert', [])}
    for filename in ('cordis.yml', 'cordis.patch.yml'):
        path = Path(home or Path.home()) / '.dsh/profiles/desktop' / filename
        try:
            changes = yaml.load(path.read_text(), Loader=DshLoader) or []
        except OSError:
            continue
        for patch in changes if isinstance(changes, list) else []:
            if not isinstance(patch, dict):
                continue
            for row in patch.get('insert', []):
                if row.get('name') == '@deepseek-ai/dsh-agent-preset':
                    rows[row['id']] = row
            if patch.get('id') in rows:
                rows[patch['id']].update(patch)
    return [row for row in rows.values() if row.get('disabled') is not True]


def public_dsh_presets(home=None):
    try:
        return [
            {
                'id': 'dsh-preset:' + row['config']['id'],
                'framework_id': 'dsh',
                'name': row['config'].get('name') or row['config']['id'],
                'native_name': row['config']['id'],
                'kind': 'native-agent-preset',
                'source': 'DSH Agent Presets',
                'available': True,
                'execution_mode': 'native-preset',
                'note': 'Native plugin composition; read-only execution boundary',
            }
            for row in preset_rows(home)
        ]
    except (OSError, ValueError, KeyError, yaml.YAMLError, struct.error):
        return []


def preset_patch(preset, bridge_path):
    chosen = next((row for row in preset_rows() if row['config']['id'] == preset), None)
    if chosen is None:
        raise ValueError('DSH Agent Preset no longer exists')
    web = yaml.load(InstalledDsh().text(WEB_APP + 'cordis.patch.yml'), Loader=DshLoader)
    # Move only the agent-plane rows behind the native preset. No web server or UI plugins start.
    boundary = next(
        i for i, row in enumerate(web) if row.get('id') == 'tool-plugin-manager' and row.get('disabled') is True
    )
    disabled = [row for row in web[boundary:] if row.get('disabled') is True and set(row) <= {'id', 'disabled'}]
    return [
        *disabled,
        {
            'insert': [
                {
                    'id': 'subagent-model-selection-settings',
                    'name': '@deepseek-ai/dsh-tool-subagent/model-selection-settings',
                },
                {
                    'id': 'agent-preset-registry',
                    'name': '@deepseek-ai/dsh-agent-preset-registry',
                    'config': {'default': preset},
                },
                chosen,
                {'id': 'owui-preset-bridge', 'name': str(bridge_path), 'config': {'preset': preset}},
            ]
        },
    ]
