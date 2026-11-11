# Composable workflow nodes

New definitions use graph schema 2.0. Predefined and generated work are programs
for the same interpreter. Names such as Controller, Executor and Reviewer are
binding labels, not workflow modes.

Every new template has explicit `input` and `output` nodes. They use the same
named `inputs` references, declared `output_schema`, dependency edges and node
states as work nodes, and execute in the host without a model call. The input
node maps the host's current `/task` into named fields. Other nodes reference
those fields through the input node, rather than reaching into an undeclared
host payload. The output node maps upstream fields into its named `answer`;
the graph's public `outputs.answer` points at that boundary. Neither boundary
is an Agent or a module binding. Missing source fields fail graph validation;
boundary type errors stop execution before downstream paid calls.

An input binding is `local input name ← source node.output field`. It creates
the required data dependency automatically. An additional explicit edge only
sets execution order: checking a prerequisite never copies its report, and
removing an ordering edge never deletes an input binding. The editor starts
with Dependencies, then shows input bindings and output consumers. Collectors
and other host operations expose their code-defined result fields in the same
output contract view. The current chat host input contains task text and its
visible-history context; extra host fields such as `/history` are not assumed.

- Every callable node declares an operation, responsibilities (`instruction`),
  module binding, stable private `context_key`, named input references and an
  output contract. `call`, `plan` and `review` all use the same module invocation,
  history, native-child accounting and output validation boundary.
- `call` returns a report or schema-validated JSON. `plan` publishes/patches
  ordinary graph nodes and a named output port. `review` validates evidence and
  its verdict in host code before delivering its answer. These capabilities are
  configured in the common node editor, without workflow-specific panels.
- Graph `outputs` map names to references. `answer` is delivery; `candidate` is
  only a conventional intermediate port. A node can consume a named graph output.
  Data references compile dependencies; unresolved planner-owned ports wait for
  a plan update. Generated edges are rebuilt when a port changes.
  `result_node` in normalized runtime snapshots is a derived v1 compatibility
  alias; `outputs.answer` is the authoritative v2 delivery definition.
- A graph currently permits one serialized graph-mutating planner. It may
  observe events while independent work runs. Review gates are normal graph
  nodes; rejected evidence cannot silently become an approved final answer.
- Planning cannot rewrite started/completed work or reserved control nodes, alter
  bindings outside the enabled Project catalog, or change the final review gate.
  Repair appends work and changes the candidate port, preserving earlier reports.
- v1 controllers/reviewers are imported to nodes at the definition boundary.
  Archived definitions and completed run journals remain unchanged. Native
  contexts are resumed only when their existing compatibility signature permits;
  otherwise saved role dialogue is replayed into a fresh native scope.
  On a new turn, compacted control snapshots are replayed when a native session
  could otherwise retain superseded snapshots. Durable dialogue and decisions
  remain unchanged; ordinary executor contexts are never compacted this way.

The new saved example is Controller call → independent Executor calls → Reviewer
node. Controller returns two structured assignments; each executor receives only
its assignment; Reviewer receives the original goal and both reports. The graph
returns Reviewer.answer. Fixed and AI-generated graphs use identical references,
readiness rules, context keys and per-invocation accounting.

Validation includes contract/dependency rejection, schema output errors, review
gating, asynchronous fast-branch progress, budget attribution and two-turn private
context continuation. Real acceptance uses existing enabled Flash participants in
Example, through the normal authenticated application UI.
