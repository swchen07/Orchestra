# Nodes, actions and ports

A workflow is a graph of steps. A node supplies an ID, a registered action,
responsibilities, named input bindings, an output contract and (for Agent
actions) a module binding and stable private-context key. Edges and result
references define readiness and delivery. Action names do not grant tools or
select a Provider; Project participant bindings still own runtime selection.

`backend/open_webui/execution/node_actions.json` is the shared action catalog.
The editor imports its public export `nodeActions.generated.json`; the compiler,
planner and interpreter read the original definitions. Regenerate the export
with `python scripts/export-node-actions.py` after changing the catalog. A
contract test rejects drift. The backend directory is not exposed to Vite.
`node_actions.py` binds each operation to an explicit
interpreter handler. An unknown action or a missing implementation is rejected.
`action` is the public action ID; `op` is a checked compatibility alias. New
definitions may supply only `action`; normalized snapshots retain both fields.
Planning is owned by the serialized controller event loop; other actions use
the registered node handlers. Both paths use the common invocation ledger.

For example:

```json
{
  "id": "north_subtotal",
  "action": "module.call",
  "binding": "executor_01",
  "context_key": "north",
  "instruction": "Compute the subtotal from the supplied assignment.",
  "inputs": {
    "assignment": {"source": "node", "node": "assign", "pointer": "/task_b"}
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "subtotal": {"type": "integer", "description": "Subtotal in cents, including every assigned line item."}
    },
    "required": ["subtotal"]
  }
}
```

Users choose a source node and one of its declared output fields. The editor
generates the reference path and its required dependency. Changing a source
selects a valid field from the new source; it preserves the optional-input
policy. Adding an input creates an unconnected binding, rather than silently
duplicating the original task. Advanced paths remain available for imported
custom definitions. A local input name is independent of the source field name.

Output field descriptions are JSON Schema `description` values. They are sent
to the producing Agent with its output requirements and accompany explicitly
bound values sent to consuming Agents. Plain-text replies use
`report_description` for the report port. The host validates structured names
and types after the response; descriptions explain semantics, not a claim that
the framework provides constrained generation or that semantic correctness is
mechanically proved.

The three starting examples use the same editor and guide:

| Example | Saved structure | Model-generated content |
| --- | --- | --- |
| Adaptive Modules | Input, planning, review and output nodes | Work nodes, assignments, bindings from the enabled Project pool, dependencies and candidate selection |
| Compare | Input, independent module calls, collector and output | Each independent complete answer |
| Distribute & Review | Input, dispatcher, two parallel Executors, Reviewer, bounded rejection feedback and output | Assignments, selected correction branches, reports and reviewed answer |

`example` is editable learning metadata (purpose, sample task, expected behavior).
It is neither added to the Agent prompt nor executed automatically. The guide's
connection table is generated from the actual current graph. Duplicate or create
from any of the three examples, edit actions and bindings, then bind actual
Agent Teams in the Project. No example name selects a runtime mode.

## Interaction references and scope

- [LangGraph Graph API: Send and Command](https://docs.langchain.com/oss/python/langgraph/graph-api#send)
  separates data-bearing dynamic dispatch from state updates and routing. We
  retain explicit payload references and typed controller commands. Our event
  scheduler keeps dependency-ready fast branches moving independently rather
  than importing a global round barrier.
- [LangGraph subgraphs](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)
  describes wrapping a graph as a node with explicit parent/child input and
  output mapping. Our module invocation boundary similarly treats a native
  Agent Team as a callable unit with private retained dialogue.
- [OpenAI Agents SDK orchestration](https://openai.github.io/openai-agents-python/multi_agent/)
  distinguishes a manager calling Agents as tools (control returns to the
  manager) from a handoff (a specialist takes over). `module.call` implements
  the bounded invocation/report pattern; it does not claim conversation takeover.
- [AutoSwarm architecture](https://github.com/HKUDS/AutoSwarm/blob/main/docs/architecture.zh-CN.md)
  separates run, wait/receive, native resume, cancel and directed messages, and
  distinguishes stable Agent identity from individual call identity. We retain
  that distinction in context keys, invocations and report provenance.

The current catalog exposes implemented delegation, dynamic planning, routing,
item dispatch, collection, review, bounded repetition, user checkpoints and
input/output actions. Native peer messaging and ownership-transferring handoffs
are separate capabilities, not aliases for an ordinary module call. Adding one
requires a typed payload, an execution handler, an explicit context-transfer
policy, transport capability checks and accounting/continuation tests. An
arbitrary new string in the catalog is not executable infrastructure.

## Bounded review feedback

`tasks.dispatch` (`op: dispatch`) is a reusable node action. `dispatch.targets`
declares its permitted downstream work nodes. Its output schema includes
`selected_nodes`; the host validates a nonempty, unique subset of those IDs.
It cannot change topology, choose arbitrary backends or bypass a Reviewer.

A review node selects `review.repair_node` and `review.max_repairs` (0–8 extra
attempts). The compiler derives an `on: revise` control edge back to a compatible
upstream node (or the graph-planning node). Forward data dependencies remain a DAG;
feedback is a bounded transition between attempts, not an ordinary prerequisite
which would deadlock the first execution. Both configuration and run diagrams
show the same feedback edge and correction limit.

Feedback is an Action capability in `node_actions.json`, never a Workflow-name
branch. Input and output boundaries cannot receive feedback. Other Actions declare
one of three policies:

| Policy | Receiver | Host behavior |
| --- | --- | --- |
| `replay` | Ordinary work, route, map, loop, collector, checkpoint or review node | Reopen this node and its downstream dependencies; preserve unrelated committed branches. |
| `select` | Task dispatcher | Reopen the dispatcher, then invalidate only its selected correction branches and their dependants. |
| `replan` | Graph planner | Give the Controller structured issues and current results; validate its graph change or retry command. |

All three use the same feedback queue, correction counters, dependency invalidation,
attempt history and output gate. Pending work in the affected dependency cut is
held; active affected invocations settle before replay, while unrelated branches
can continue. Feedback contains the originating review node, attempt number,
verdict, issues and checked report references, and is supplied to the receiving
Agent alongside its declared inputs and retained private context.
Every model invocation also carries host-owned node, module-instance and Action
identity. A module produces its own report; a narrative that pretends other nodes,
feedback or approval happened is not an executed trace. Structured output schemas
can further constrain the report fields, while semantic correctness still needs
the independent review criteria.

A Controller can emit `{"action":"retry_nodes","base_revision":3,
"node_ids":["existing_work"],"reason":"Correct the rejected report"}`. This
uses the same replay primitive as a predefined graph, retains stable node/context
identities, and does not rewrite committed node definitions or previous calls.
Normal graph proposal/patch commands remain available for a changed topology.
On a review-triggered graph patch, only the rejected review and its downstream
delivery cut reopen as new attempts. This permits a revised candidate output
alias to point to newly appended corrective work. Existing work-node definitions,
inputs and completed invocations stay frozen; changing a module's responsibility
requires a new node rather than disguising that change as an in-place retry.
Generated graph inputs are always typed references, including constants such as
`{"source":"literal","value":3}`. Malformed bindings are returned to the
Controller as proposal errors, without committing a partial graph or crashing
the retained run. They consume a planning attempt under the same budget.

For a dispatcher, rejection supplies typed feedback, committed reports and
previous assignments. It selects which branches require correction. The host
retains unselected assignments and reports, invalidates selected nodes and their
dependants, then reopens the Reviewer and delivery gate. Stable context keys
continue the same module histories. Each fresh invocation has a new call/report
ID and contributes usage once. Earlier rejected reports remain in the journal
and node attempt history; current review evidence includes only committed latest
reports for replayed nodes plus retained reports of unchanged branches.
For map/loop nodes, evidence includes every call in the current attempt, not only
the final item. An upstream Reviewer can itself feed a later Reviewer; its current
review report participates in the same provenance rules.

Plain Compare intentionally has no semantic judge: it keeps independent answers
separate. To add a closed loop, insert an ordinary review node after its collector,
bind a Reviewer module, select a compatible feedback target, and connect the
Workflow output to the approved review answer. This is a graph edit using the
same Actions as Adaptive Modules and Distribute & Review, not a new mode.

Every review verdict is saved in `review_attempts`. Exhausted correction, call,
token or time budgets stop execution without releasing an unapproved answer.
As with all review actions, semantic checking is performed by the Reviewer;
the host enforces contracts, provenance and delivery conditions.
