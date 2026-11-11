# Callable modules and host Actions

A module reference is an existing participant ID or `workflow:<saved UUID>`.
The same reference can be saved in a Project role slot, a chat-only role override,
or a node binding. A role supplies a binding and instructions; the node's Action
continues to determine what the host does. Changing a Direct/API participant to a
Framework team or a compatible Workflow never changes `module.call` into another
Action. `workflow.call` remains a legacy alias restricted to saved Workflows.

All module-bearing Actions support compatible native and composite bindings:
`module.call`, `graph.plan`, `result.review`, `tasks.dispatch`, `flow.route`,
`items.map`, and `task.repeat`. Input/output boundaries, collection, human input
and returning existing data are host Actions and cannot bind an Agent.
Unsupported ports or environments reject explicitly. There is no fallback model.

## Public ports and delivery

A saved Workflow exposes a typed public input object and named public outputs.
The input boundary maps `source: input` fields into local named fields. The output
boundary maps upstream values into public names. A reusable child can use names
such as `question`, `count`, `flag`, or `verdict`; no `task` or `answer` is required.
`false`, `0`, `null`, and empty strings are data and are preserved.

A root chat chooses `graph.delivery_port` from the public output names. Existing
`answer` delivery is the compatible default. Custom-only root outputs require an
explicit choice before work starts. Children complete by validating their entire
public output object, so a review's legitimate `revise` with `answer: ""` reaches
the caller's correction gate. Public output fields are independent of the host's
text report and reserved `__report` provenance.

Public output aliases used by internal consumers (for example a planner's
`candidate`) stay internal when an output boundary exists. Its named fields
identify the actual public interface. Caller contracts remain effective: known
required-field/type incompatibilities reject before descendants, and unknown
values are validated at the actual boundary. Descriptions do not change type
compatibility.

For `items.map` and `task.repeat`, the child schema describes each invocation.
The host supplies `item` or `previous`; these are not required user mappings on
the outer node. Item instances get stable separate paths; a repeat reuses its
own path. Local limits and all ancestors' call/token/deadline restrictions remain
effective through the existing executor.

## Explicit invocation context

A child input boundary can select `source: invocation` with one of these known
fields in the normal source/field selector:

| Field | Meaning |
| --- | --- |
| `/action` | Registered caller Action, as data |
| `/instruction` | Responsibilities assigned by the caller node |
| `/inputs` | Only that node's selected inputs |
| `/output_schema` | Contract checked by the caller host |
| `/context/prompt` | Host planning/review packet; only for `graph.plan` or `result.review` |
| `/context/dispatch` | Allowed dispatch branches/correction state; only for `tasks.dispatch` |
| `/context/feedback` | Host-issued corrections when available |

For example, a reusable review graph can receive the review packet in a local
`evidence` field:

```json
{
  "id": "receive",
  "action": "workflow.receive",
  "inputs": {
    "evidence": {"source": "invocation", "pointer": "/context/prompt"}
  },
  "output_schema": {
    "type": "object",
    "properties": {"evidence": {"type": "string"}},
    "required": ["evidence"]
  }
}
```

Its descendants receive this field only through declared bindings. The snapshot
is never broadcast into child private histories. It cannot expose credentials,
private checkpoints or unrelated peer dialogue. A root has no parent snapshot,
and ordinary module calls do not receive planning/review packets. Missing host
fields reject before leaf work. User input cannot overwrite the snapshot.

The output remains subject to the caller's registered host protocol: controller
commands are validated by the graph mutation handler; review verifies exact host
report references, verdict, issues and bounded corrections; dispatch verifies
allowed targets; route verifies declared cases. Prompts and child outputs cannot
grant mutation or delivery authority. A composite control result has its own
invocation/report identity, independently of its last descendant.

## Contexts, accounting and compatibility

New nodes default to their node ID as the logical instance key. Sharing a model
or role slot alone does not share private dialogue. An explicit equal
`context_key` can continue a work instance. Current and retained planner/reviewer
contexts cannot be borrowed by ordinary work. Stable nested paths continue across
turns and branch regeneration; ordering edges do not copy peer reports.

Saved older definitions retain their previous implicit role keys on a copied
read/upgrade view. Run journals and checkpoints are not rewritten. Their report
identity stays attributable to the same retained history. Native handle claims
are atomic by source checkpoint plus actual native handle (or logical scope),
so changing a role key cannot resume a consumed session. Legacy role-only claim
files trigger safe dialogue replay when handle consumption cannot be established.

The existing WorkflowEngine performs all scheduling, root admission, local
limits, manual control, cancellation and event propagation. Depth is limited to
four including the root; recursive or unavailable transitive bindings reject
before paid work. Composite summaries have zero self usage and cite their actual
leaf calls; only those real leaf attempts enter the call/token ledger. A native
session incompatibility rotates the transport while retaining logical history.

The shared Module selector is used by graph nodes, Project role defaults and
chat role overrides. A Workflow selection displays its public contract and saved
child bindings. Native presets/effort remain available through the existing
native controls. Ordinary None chat continues to use its original path.
