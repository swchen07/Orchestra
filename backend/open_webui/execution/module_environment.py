"""Host-owned capability manifest. Unsupported restrictions are never promises."""

from dataclasses import asdict, dataclass
import hashlib
import json


@dataclass(frozen=True)
class ModuleEnvironment:
    namespace: str
    directory: str | None
    filesystem: str
    tools: str
    network: str
    isolation: str
    authority: str
    credentials: str
    resources: tuple[str, ...] = ()
    workspace: dict | None = None

    def public(self):
        value = asdict(self)
        if self.workspace is None:
            value.pop('workspace')
        return value

    def signature(self):
        value = self.public()
        value.pop('namespace')  # Stable permissions, not the newly claimed native handle.
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def narrow_requests(inherited, requested):
    result = dict(inherited or {})
    for key, value in (requested or {}).items():
        if value == 'inherit':
            continue
        old = result.get(key, 'inherit')
        if (old == 'none' and value != 'none' or old == 'provider-only' and value != old
                or key == 'filesystem' and old == 'read-only' and value not in {'none', 'read-only'}):
            raise ValueError('A child module cannot widen inherited environment capabilities')
        if key == 'isolation' and {'inherit': 0, 'logical': 0, 'process': 1, 'sandbox': 2}.get(value, -1) < {'inherit': 0, 'logical': 0, 'process': 1, 'sandbox': 2}.get(old, 0):
            raise ValueError('A child module cannot weaken inherited environment isolation')
        result[key] = value
    return result


def resolve_environment(binding, namespace):
    framework = binding.get('framework_id', 'direct')
    if framework not in {'direct', 'claude-code', 'codex', 'dsh', 'hermes'}:
        raise ValueError('No registered environment contract for framework ' + str(framework))
    base = binding.get('_environment_base') or {}
    directory = base.get('directory')
    native = framework in {'claude-code', 'codex', 'dsh'}
    external = framework == 'hermes'
    policy = base.get('workspace') or {}
    coding = bool(policy.get('enabled')) and framework in {'claude-code', 'dsh', 'hermes', 'codex'}
    if coding and policy.get('backend', 'local-worktree') != 'local-worktree':
        raise ValueError('This native coding backend is unavailable; no call was admitted')
    values = dict(
        namespace=namespace, directory=directory if native else None,
        filesystem='gateway-owned' if external else ('read-only' if native and directory else 'native-read-only' if native else 'none'),
        tools='gateway-owned' if external else 'restricted-native' if native else 'none',
        network='gateway-owned' if external else 'native-owned' if native else 'provider-only',
        isolation='process' if native else 'logical',
        authority='external-gateway' if external else 'adapter-and-native-runtime' if native else 'host-api-transport',
        credentials='selected-provider-transport; native account configuration' if native else 'selected-provider-transport',
        resources=tuple(base.get('resources') or ()),
    )
    if coding:
        values.update(directory=directory, filesystem='workspace-write', tools='coding-native', network='native-owned',
                      isolation='process', authority='trusted-local-worktree; not an OS sandbox')
    request = binding.get('_environment_request') or {}
    if set(request) - {'filesystem', 'tools', 'network', 'isolation'}:
        raise ValueError('Unknown module environment capability')
    for key, value in request.items():
        if value == 'inherit':
            continue
        if key == 'isolation':
            if value == 'logical' or value == values[key]:
                continue  # A caller cannot remove existing process isolation.
        elif value == values[key]:
            continue
        elif key in {'tools', 'filesystem'} and value == 'none' and framework == 'claude-code':
            if binding.get('_profile_execution_mode') == 'native-agent':
                raise ValueError('A native Agent preset cannot enforce tools=none; choose a prompt preset or default')
            # The bridge installs the no-tools adapter policy, including native delegation.
            values.update(tools='none', filesystem='none')
            continue
        elif coding and key == 'filesystem' and value == 'read-only':
            if framework == 'hermes':
                raise ValueError('Workspace-native Hermes cannot enforce read-only tools; no call was admitted')
            values.update(filesystem='read-only', tools='restricted-native')
            continue
        elif key == 'filesystem' and value == 'read-only' and native:
            continue
        raise ValueError('Unsupported module environment requirement: ' + key + '=' + str(value)
                         + ' for ' + framework + '; no call was admitted')
    return ModuleEnvironment(**values)


def environment_capabilities(binding):
    """Planner-readable requests, derived by exercising the same resolver."""
    actual = resolve_environment(binding, 'catalog').public()
    choices = {'filesystem': ['inherit', 'none', 'read-only', 'workspace-write'], 'tools': ['inherit', 'none'],
               'network': ['inherit', 'provider-only'], 'isolation': ['logical', 'process', 'sandbox']}
    supported = {}
    for key, options in choices.items():
        supported[key] = []
        for option in options:
            try:
                probe = {**binding, '_environment_request': narrow_requests(binding.get('_environment_request'), {key: option})}
                resolve_environment(probe, 'catalog')
            except ValueError:
                continue
            supported[key].append(option)
    return {'authority': actual['authority'], 'actual': {k:actual[k] for k in choices}, 'supported_requests': supported}
