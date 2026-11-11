"""Display identities are stable; task labels, backend bindings and storage IDs stay separate."""

import re
import json
import uuid
from functools import lru_cache
from pathlib import Path


def display_name(base, name=''):
    alias = name.strip()
    alias = re.sub(r'^(?:Executor\s+\d+(?:\.\d+)?|Controller|Reviewer)\s*·\s*', '', alias, flags=re.I)
    generic = r'(?:Executor(?:\s+\d+(?:\.\d+)?)?|Worker(?:[ _-]+(?:[A-Z]|\d+))?|Module)'
    defaults = {'Controller': {'manager', 'controller', 'coordinator', 'lead'},
                'Reviewer': {'reviewer'}}
    if not alias or re.fullmatch(generic, alias, re.I) or alias.lower() in defaults.get(base, set()):
        return base
    return (base + ' · ' + alias)[:100]


class ModuleNames:
    def __init__(self, template, bindings=(), previous=()):
        self.labels = {}
        for record in previous:
            self.labels.update(record.get('module_names') or {})
            self._template(record.get('template') or {})
            self._instances(record.get('participants') or ())
            self._instances({'id': c['participant_id']} for c in record.get('calls', []))
        self._template(template)
        self._instances(bindings)

    def _template(self, template):
        graph = template.get('graph') or {}
        from open_webui.execution.node_program import planner_node, review_nodes
        controller = (planner_node(graph) or graph.get('controller') or {}).get('binding')
        if not controller and template.get('mode') == 'delegate':
            controller = template.get('lead_role_id') or template.get('lead_id')
        reviewer = next(iter(review_nodes(graph)), graph.get('reviewer') or {}).get('binding')
        roles = template.get('roles') or []
        declared = {r['id']: r for r in roles}
        for identity, base in ((controller, 'Controller'), (reviewer, 'Reviewer')):
            if identity:
                self.labels[identity] = display_name(base, declared.get(identity, {}).get('name', ''))
        for role in roles:
            if role['id'] not in {controller, reviewer}:
                if role.get('name') in {'Controller', 'Reviewer'}:
                    self.labels[role['id']] = role['name']
                else:
                    self.name(role['id'], role.get('name', ''))

    def _instances(self, rows):
        for row in rows:
            # A dynamic instance's old name was often its task label. Do not adopt it.
            self.name(row['id'], slot=row.get('role_id'))

    def name(self, identity, alias=None, slot=None):
        existing = self.labels.get(identity)
        if existing:
            if alias is not None and existing.startswith('Executor '):
                base = existing.split(' · ')[0]
                self.labels[identity] = display_name(base, alias)
            return self.labels[identity]
        if slot and slot != identity:
            parent = self.name(slot)
            base = parent.split(' · ')[0]
            if base.startswith('Executor '):
                pattern = re.escape(base) + r'\.(\d+)(?: ·|$)'
                used = [int(m[1]) for label in self.labels.values() if (m := re.match(pattern, label))]
                # The unsuffixed slot is its original/default context; .02 is a separate instance.
                label = f'{base}.{max(used, default=1) + 1:02d}'
                self.labels[identity] = display_name(label, parent.split(' · ', 1)[1] if ' · ' in parent else '')
                return self.labels[identity]
        used = [int(m[1]) for label in self.labels.values()
                if (m := re.match(r'Executor (\d+)(?: ·|$)', label))]
        base = f'Executor {max(used, default=0) + 1:02d}'
        self.labels[identity] = display_name(base, alias or '')
        return self.labels[identity]


def record_module_names(record, previous=()):
    names = ModuleNames(record.get('template') or {}, (), previous)
    names.labels.update(record.get('module_names') or {})
    names._instances(record.get('participants') or ())
    names._instances({'id': c['participant_id']} for c in record.get('calls', []))
    return names.labels


@lru_cache(maxsize=128)
def legacy_record_names(path, modified_ns):
    """Frozen old runs need one ancestry projection, not a scan on every UI poll.

    Return immutable pairs so a caller cannot mutate the cached registry. The
    current run's owner is checked by read_run before this helper is called.
    """
    target = Path(path)
    record = json.loads(target.read_text())
    previous = []
    for identity in record.get('continuation', {}).get('previous_run_ids', []):
        try:
            if str(uuid.UUID(identity)) != identity:
                continue
            prior = json.loads((target.parent / (identity + '.json')).read_text())
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        if (all(prior.get(k) == record.get(k) for k in ('user_id', 'chat_id', 'project_id'))
                and prior.get('template', {}).get('id') == record.get('template', {}).get('id')):
            previous.append(prior)
    return tuple(record_module_names(record, previous).items())
