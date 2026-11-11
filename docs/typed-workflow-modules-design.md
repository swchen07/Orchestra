# Typed, environment-declared Workflow modules

## One execution contract

All nodes declare `input_schema` and `output_schema`. The compiler infers missing
input ports from their source declarations for compatible older graphs. Explicit
consumer contracts are checked conservatively against producer contracts. Unknown
types are checked at invocation; known incompatible types fail before admission.
The same local JSON Schema validator checks workflow ingress, node inputs, node
outputs and workflow egress. No coercion, remote schema resolution or model-only
validation is permitted. Descriptions reach the Agent with its port contract.

## Explicit environment

`ModuleEnvironment` is a host-owned, serializable invocation manifest: context
namespace, project directory, filesystem policy, tools policy, network policy,
credential boundary and enforcing authority. A node may narrow the inherited
environment but cannot grant capabilities. Unsupported requested restrictions
fail closed. Logical conversation isolation is distinct from an OS sandbox;
external Hermes gateways explicitly own their environment. Environment identity
participates in native continuation compatibility. Credentials never enter the
manifest, planner catalog or report body.

## WorkflowModule

`workflow.call` selects an authorized, saved and project-bound Workflow from a
host-built module catalog. Its frozen definition and role bindings cannot be
supplied by model text. The call accepts the child's public input object and
returns its declared public output object. Each invocation path owns child role
histories and reports, retained across chat turns. Recursion cycles and excessive
depth are rejected. Child leaf calls share the root call ledger, concurrency
slots, token admission and deadline, and also respect tighter local limits.
Workflow rollups reference leaf call IDs; they never add another token charge.
Cancellation propagates into every descendant. Child snapshots remain available
in the parent run and ordinary activity/trajectory instead of a special mode.

## Event control rather than model polling

Dependency readiness, waiting and completion are host operations. Normal ready
branches and their downstream consumers run without another Controller call.
The Controller wakes for the initial graph, failed work, review feedback, held or
unresolved graph frontiers, explicit requested observations and human changes.
Successful events with executable/settling dependencies are coalesced. Full prior
control snapshots are compacted; catalogs/action protocols are bounded and
duplicate module definitions are removed. The journal records wake reasons and
suppressed observations so token savings are auditable.

## Acceptance

1. Negative and positive type connections, optional outcomes, structured arrays,
   ingress/egress and runtime invalid-value rejection before model admission.
2. Capability narrowing, unsupported sandbox rejection, environment continuation
   mismatch and per-path child context isolation.
3. Nested workflow composition, shared budgets/concurrency, no duplicate tokens,
   cancellation, recursion rejection and two-turn context restoration.
4. Real DSH / Claude Code / Hermes Flash multi-Agent tasks, including a saved
   Workflow called by another Workflow. Inspect actual graphs, histories, native
   counters and timestamps; correct answers alone are insufficient.
5. A complete dependency graph needs one initial Controller decision and no
   model `wait` polling. A fast branch's consumer must start before an unrelated
   slow branch finishes.
