# Module, Action and graph composition contract

This describes the bounded interpreter and its contracts. Static type checking
is conservative; actual values are checked at every boundary. Planning quality
and OS sandboxing are separate from executing a valid graph.

## The layers

```text
Project: participant catalog, directory, resources, default bindings
  Chat: user-visible history and workflow-local overrides
    Workflow definition: input/output boundary + nodes + edges + limits
      Run: frozen definition, revisions, node attempts, reports and ledger
        Node: Action + responsibility + input bindings + output contract
          optional Module Role / catalog binding
            logical module instance (context_key)
              AgentModule
                ChatAgentModule -> adapter -> Provider/Model/Profile/Effort
                WorkflowModule -> saved child graph -> its bound modules
```

A Module Role is a configuration slot (`id`, `name`, `instructions`), not a Python
subclass or a graph node. A Node is one unit of work. Several nodes may reuse one
module instance, and the same role/backend may create separate instances with
different context keys. Host nodes such as collect/return/input/output do not
invoke a module. The Action belongs to the **node**; an Edge is a dependency or
feedback condition, not executable Agent behavior.

Two useful views should not be conflated:

- An actor diagram can draw modules as vertices and messages as arrows.
- The executable graph draws invocations/control operations as vertices and
  data/control dependencies as arrows. One actor can appear in several vertices.

## Actual common module boundary

`execution/modules.py` defines the inheritance chain:

```text
AgentModule (abstract run/execute boundary)
  ChatAgentModule (wraps a supplied backend invocation callback)
  WorkflowModule (executes a saved, project-bound child graph)
```

The framework adapters themselves expose transport behavior; they do not all
inherit a framework-specific SDK Agent class. Direct model calls and native
Agent Teams pass through the same wrapper.

| Object | Implemented fields / behavior |
| --- | --- |
| ModuleScope | run_id, invocation_id, instance_id, context_id, definition_id |
| ModuleInput | scope, binding, messages, native_session_id, ModuleEnvironment, typed inputs, input_schema, output_schema |
| ModuleResult | scope, raw output, validated value, usage, execution metadata, timings |
| Events | model text/tool/usage/metadata and one terminal module.finished event |
| Retained state | Engine owns role dialogue, compatible native handles, signatures and branch ancestry; it is not returned as a public module output |
| Public report | Host-owned envelope: report ID, producer, node, revision, digest, dependencies, context and usage reference |

`AgentModule.run` deep-copies the invocation snapshot, attributes metrics to its
scope, rejects empty/oversized responses, ignores late events, and reports
failure/cancellation timing. The frozen dataclasses prevent field reassignment;
their dictionaries/lists are not recursively immutable. Deep copies protect the
caller's retained values from adapter mutation.

Structured JSON is validated inside AgentModule before committing successful
history, then the Node validates its host-owned outcome. Invalid originals and
their usage remain in the failed call, never as valid downstream values. A report
body cannot manufacture host provenance.

## Environment and isolation

The conceptual execution is `(selected inputs, own history, environment) ->
(result, updated own history, events, usage, timings)`. Currently, environment is
declared in a host-owned `ModuleEnvironment`: namespace, directory, filesystem,
tools, network, isolation, enforcing authority and credential boundary. A node
may narrow inherited capabilities. Unsupported requests fail before admission;
children cannot widen requirements. The actual manifest is recorded in
`call.execution.environment` and Agent Details. Its signature prevents resuming
native state with incompatible permissions.

| Boundary | Current guarantee | Limit |
| --- | --- | --- |
| Dialogue | Private context keys, copied inputs, per-context serialization, branch-aware history | Explicit report bindings intentionally transfer selected data; `shared` input policy intentionally sends the full task |
| Backend switch | Signature mismatch stops resuming an incompatible native session; dialogue can be replayed | Same logical history is retained; this is not process-memory virtualization |
| Project | Catalog and saved role bindings constrain allowed participants; resources/profiles are selected by hooks | Modules in a Project can use the same directory and common project instructions/resources |
| Permissions | Claude uses restricted tools, strict empty MCP configuration and safe mode; Codex requests read-only; DSH requests read-only and denies ACP permission elevation | Policies differ by adapter. They are not a common per-instance filesystem/network namespace |
| Hermes | Context/session headers and replayed dialogue separate logical sessions | Tools and runtime environment are owned by the external gateway; the wrapper does not create its sandbox |
| Resources | Skills/prompts/profile instructions affect a selected invocation and its signature | App MCP binding metadata currently reports `mcp_execution=false`; it is not a newly granted execution capability |
| Accounting | Invocation ledger, native self/children/inclusive counters and interval accounting | Unknown native counters stay unknown; token budgets can only use reported usage |

Provider credentials remain transport configuration, not planner catalog data.
The environment manifest lists selected resource references; it does not claim
an immutable snapshot of every native configuration file or filesystem content.
Native processes retain host environment/account configuration except removed
host-chat identity/recursion markers, and frameworks may
share project files or native resource configuration. Therefore the module
boundary is **logical dialogue/accounting isolation**, with adapter-specific
restrictions, not a hermetic environment or a pure side-effect-free function.
Native invocation admission is also shared across runs. Invocation duration
includes admission/transport overhead; it is not pure model inference time.
Concurrent durations must not be summed and called workflow wall time. Inclusive
native usage is counted once, with self and children shown as a decomposition.

## Action vocabulary

The registry `execution/node_actions.json` and explicit host handler table are
the authority. An arbitrary Action string or prompt cannot install an operator.

| Action | Executor | Effect |
| --- | --- | --- |
| workflow.receive | Host | Publish named workflow inputs |
| module.call | Agent | Execute a scoped assignment; return text or declared JSON |
| workflow.call | Host + child modules | Execute a saved, project-bound Workflow through AgentModule; module.call accepts the same module binding |
| tasks.dispatch | Agent + host validation | Produce assignments and select declared execution branches |
| graph.plan | Controller + host validation | Propose/patch work, retry existing nodes, wait or ask for input |
| result.review | Reviewer + host validation | Approve an attributed candidate or return concrete issues |
| flow.route | Agent | Choose a declared case; host activates matching edges |
| items.map | Agent invocations + host scheduling | Process at most 24 items in isolated stable per-item contexts |
| results.collect | Host | Collect all outcomes, all successes, first success or quorum |
| task.repeat | Agent invocations + host scheduling | Repeat in one context with previous output and a bounded stop rule |
| human.ask | Human + host | Wait for a supplied answer without guessing it |
| data.return | Host | Return already supplied data without a model call |
| workflow.deliver | Host | Map the selected upstream answer to chat output |

Planning commands (`propose_plan`, `patch_plan`, `retry_nodes`, `wait`, etc.) are
the output protocol of graph.plan. Edge conditions (`success`, `terminal`,
`case`, `revise`) are another layer. These are not extra Workflow modes.

## Data and execution rules

Each named Node input binds one source: workflow input, predecessor field,
retained report, literal value or named graph output alias. Field selection uses
JSON pointers internally. References compile dependency edges. Optional inputs
can yield an unavailable/error outcome instead of a value; they do not start a
new task.

Forward dependencies form a DAG. The host starts ready work independently within
the shared concurrency/call budget. Reusing a context serializes its invocations;
an explicit edge is still needed when their order or data dependency matters.
First-success/quorum collectors can unblock successors early; that does not
automatically cancel other branches or end the entire run immediately.
Nonoverlapping timestamps do not prove a dependency: independent branches may
queue behind shared slots. Identical model-generated values or verification
codes do not prove shared dialogue. Isolation checks use host context IDs,
declared inputs/dependencies and attributed history; explicit output-uniqueness
requirements are a separate content criterion.
Map contexts are keyed by node and item position, not a document's semantic ID.
Reordering a batch across chat turns therefore does not automatically preserve
per-document affinity; use explicit stable instances when that affinity matters.

Review feedback is an attempted-state transition outside the forward DAG:

- replay: reopen an ordinary receiver and its affected successors;
- select: dispatcher selects correction branches and retains other reports;
- replan: Controller retries existing work or appends a corrective subgraph.

Started work definitions are immutable. Review-triggered output-alias changes
reopen the rejected review/delivery cut as new attempts. Reports and usage from
earlier attempts stay in the ledger; current review references use current
attempt reports and unchanged retained branches. Correction limits are enforced.

## Typed ports, nested graphs and event control

Every node has an input_schema, inferred from declared sources when omitted.
Known incompatible scalar, array, object and enum connections fail compilation.
Unknown types defer to actual-value validation. Optional unavailable outcomes
must fit the consumer's declared union. Workflow ingress/egress, module calls and
host outcomes use the same local JSON Schema validator, without coercion, remote
references or non-finite JSON. This is not a full static schema implication prover.
Map/repeat output_schema describes each invocation; the host owns the typed
items/partial or last/iterations envelope. Field meanings reach the Agent.

The host builds Workflow module entries from active definitions with authorized
Project bindings. Unavailable descendants and recursive definitions are excluded.
Models select catalog IDs and public inputs; they cannot replace saved child
definitions or bindings. Generated internal aliases cannot enlarge the frozen
public output contract. A stable invocation path retains private child histories
across turns; separate paths isolate them. Checkpoints stay in the root journal
and are removed from the public live view. Root controls route child approval and
human answers through the same authenticated endpoint; child policies are honored.

Descendants share root slots, call/token admission and deadline, and tighter child
limits. Workflow rollups have zero model tokens of their own and reference leaf
call IDs; those calls are charged once. Workflow wall time, the UNION of descendant
invocation intervals, and remaining host/wait time are separate measures.
Composite outputs have citable host-owned reports in `module_reports`, including
leaf usage references; these are not extra model calls. They remain referenceable
in later turns through the same report resolver and archive.
Cancellation drains descendant tasks and closes their transports; a remote
provider may still finish already accepted work. Nesting is bounded to four levels with no call
cycles. Module identity paths and root_run_id locate nested provenance.

Normal successful events no longer invoke the Controller to wait. The host runs
dependencies and review gates. Controller wakes are initial planning, failure,
feedback, held/unresolved frontiers, explicit observe_nodes or user requests.
Wake reasons and suppressed events are recorded. A retained instance directory
preserves exact context keys while old graph snapshots/decisions are compacted.
Invalid Controller JSON is quarantined and corrected within the shared budget;
the original response and charge remain in the journal.

No current adapter claims an enforceable OS sandbox: isolation=sandbox fails
closed. Claude tools=none removes model-visible tools and native delegation,
not the process's ability to read authentication configuration. Unsupported
Hermes gateway restrictions also fail closed. A native Team's own tools, native
configuration and host OS resources remain under its named authority. Explicit
environment contracts do not create virtualization by inheritance alone.

## Supported composition domain and remaining limits

The currently implemented language is a **bounded graph program with isolated
callable modules, structured references and bounded feedback**. It expresses
chains, fork/join, independent comparison, conditional branches, batches,
bounded refinement, human checkpoints, review/rework and Controller revisions.
It is not a claim that all possible multi-agent programs can be represented.

Hard limits currently include 80 nodes, 320 compiled forward edges (generated
feedback edges are added by validation), 24 calls/run, concurrency at most 2,
24 map items, 8 loop iterations, 8 review corrections, 600 active seconds,
1,000,000 reported tokens, and one serialized graph-mutating planning node.
Template roles are limited to 8. Model/Controller/Reviewer calls consume the same
call/token budget as work. In-flight calls can exceed a token threshold before
their final counters arrive; no missing usage is treated as zero.

Typed ports, explicit environment contracts and WorkflowModule now exist. Their
limits remain explicit: static schema implication is conservative; semantic task
correctness is not proved by types; logical isolation is not OS virtualization;
nesting is bounded rather than unrestricted recursion. Each graph has one
graph-mutating planner; child graphs can have their own planners under the same
root budget. Arbitrary cycles and conflicting concurrent feedback are unsupported.

There is also no first-class peer mailbox or arbitrary conversation handoff:
cross-module exchange currently uses declared report/data dependencies and
feedback transitions. A loop node repeats one module, not an arbitrary nested
multi-node body.

These limits describe a bounded compositional language, not Turing completeness,
arbitrary effect sandboxing or universally optimal AI planning.

## The two independent validation questions

1. **Interpreter conformance:** Given a declared graph and deterministic module
   results, verify routing, dependency readiness, scoped context, cancellation,
   feedback gates and single-count accounting. Small-graph enumeration and
   negative unsupported-shape tests belong here. They do not test AI reasoning.
2. **Adaptive plan fitness:** Give task goals and information-flow constraints
   without supplying node IDs, Action choices, edges or graph JSON. Inspect the
   generated graph and actual calls against an independent structural/answer
   oracle. A correct final answer or a Reviewer approval alone is insufficient:
   an independent task must really be independent, and a restricted downstream
   consumer must not receive the original source.

Finite tests establish evidence for the exercised programs, not a proof that any
future graph is safe, all environments are isolated, or every AI-generated graph
is semantically correct or minimal in token cost.
