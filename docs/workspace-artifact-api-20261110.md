# Coding workspace API (first spine)

Production source is shared for independent review. Legacy Projects without `workspace.enabled` keep existing behavior. This API does not certify native CLI OS isolation.

## ProjectForm optional workspace

```json
{"workspace":{"enabled":true,"backend":"local-worktree","baseline_revision":"FULL_GIT_COMMIT","command_backend":"docker","docker_image":"python:3.14-slim","commands":[{"id":"tests","argv":["python3","-m","unittest","discover","-s","tests"],"timeout_seconds":60,"authoritative_paths":["tests"]}],"hermes_profile":"ADMIN_SELECTED_PROFILE_DIRECTORY"}}
```

`directory` remains the existing admin-authorized clean Git root. All workspace settings are admin-authored. Hermes native uses the explicitly selected profile's public model/provider and its private `.env` inside a child process; profile model/provider must match the selected Hermes catalog entry. Optional trusted `hermes_source` and `hermes_python` override the installed conventional `~/.hermes/hermes-agent` source/interpreter. Private per-lease runtime state is separate from the published tree. No global native defaults/configuration change.

Native work calls use ordinary `module.call`, `context_key` and no additional caller-supplied workspace directory. The host automatically leases, executes in real files, snapshots and exposes `__workspace` / `__artifacts` output fields. `environment.isolation: sandbox` and unsupported native restrictions reject; Docker evaluation does not change native guarantees. The same qualified context key continues file lineage across turns; stale pinned branches fork.

## Registered host Actions

All use normal `inputs` references, edges, review/feedback and existing limits. Operation aliases in parentheses:

- `workspace.command` (`command`): `workspace: {instance:"worker_context",command_ids:["tests"]}`. Executes named local commands on the declared owned instance and publishes a snapshot. For Docker choose immutable `artifact.evaluate` instead.
- `artifact.evaluate` (`evaluate`): `workspace: {command_ids:["tests"]}`; all artifact receipts recursively present in selected inputs are evaluated independently. Restores authoritative paths from pinned baseline, then runs project-selected command backend in disposable scratch. Output `{status,report,evaluations}`; status is `passed` or `failed`, with original command exit receipts. A failed evaluation remains data for optional/repair/review flow; never use `node completed` as proof that tests passed.
- `artifact.integrate` (`integrate`): `workspace: {instance:"integration",strategy:"select"|"merge"}`; selected inputs contain only selected artifact(s). `select` requires exactly one receipt. `merge` applies explicit selected immutable revisions through real Git merge. Output `{status,report,conflicts,__workspace,__artifacts}`; status is `integrated` or `conflict`. A conflict yields no artifact, keeps real Git conflict files/index and both parent revisions.
- `workspace.publish` (`publish`): `workspace: {instance:"integration"}`. Host snapshots current owned files, rejecting conflict markers; resolves/stages the Git index and commits the merge. Returns published receipt fields.

`instance` names a declared native module `context_key` (or node id by default), or a declared `artifact.integrate` instance. To repair actual integration files, invoke an ordinary native module whose `context_key` is the integration instance; its automatic publication snapshots the resolved merge. Then evaluate that module's `__artifacts`, then normal independent `result.review`. No final integrated delivery without cleared conflicts and authoritative tests of the exact final revision. Source-only subgraphs can return candidates for caller composition.

Example data reference to a producer artifact list:

```json
{"source":"node","node":"worker","pointer":"/__artifacts"}
```

Artifacts pass only through explicitly selected declared inputs. Host-owned reserved fields cannot be produced by model JSON. A mutable instance receives only validated artifact versions found in its input snapshot, materialized in private read-only input copies outside the candidate tree. Local chmod is not hostile-process confinement.

## Receipts and direct service

`__workspace` fields: `id,instance_id,source_baseline,base_revision,current_revision,generation,backend,guarantees,status,conflicts`. Backend `local-worktree` advertises `os_sandbox:false`, `private_git_tree:true`, `managed_api_ownership:true`. No public native config/checkpoint directory.

`__artifacts` list entries have `kind:"workspace-artifact",id,producer,lease_id,source_baseline,base_revision,previous_revision,revision,tree,files,changed_paths,sha256,size,media_type,created_at,provenance`. `files` rows contain relative `path,sha256,size,media_type`. `base_revision` is the common host baseline; `previous_revision` is the per-publication incremental base. File bytes stay out of prompt JSON; immutable private Git repository stores them. Canonical receipt and all bytes/hashes are checked on consumption.

Evaluation receipts: `kind:"workspace-evaluation",id,artifact_id,candidate_revision,candidate_sha256,checks_sha256,checks,commands,status,result_sha256`. Commands include command ID/digest/argv, actual environment, started/finished/elapsed timestamps, status/exit_code, bounded stdout/stderr/output bytes/truncation. Host operations are separate from model token accounting, in run `host_operations`. Workspace pins persist in `workspace_states`.

Independent service entry point:

```python
from open_webui.execution.workspaces import WorkspaceService
s = WorkspaceService(project_dict, {"user_id":"u","project_id":"p","chat_id":"c"}, root=temporary_root)
lease, forked = s.acquire("qualified/instance", previous_public_receipt_or_none)
with s.lock(lease["id"]):
    work = s.checkout(lease)  # private host-side path, never model-selected
    public, artifact = s.snapshot(lease, provenance={"invocation_id":"..."})
# s.artifact(artifact, granted={artifact['id']}) verifies canonical receipt and bytes.
# await s.evaluate(artifact, ['tests'], granted={artifact['id']}, record=host_operation_list)
# s.integrate('integration', [artifact], 'select', previous=None, granted={artifact['id']})
```

Initial evidence: 258 existing backend tests passed. Three implementation spine tests passed after correcting an implementation test's adjacent-line fixture: Git correctly reported adjacent edits as a conflict; the corrected non-overlapping fixture separates edits by unchanged lines. Original failure is retained in private implementation evidence.

## Final delivery and accounting

The final delivery guard requires an actually approved independent review at the current graph revision. The reviewer must receive the passed host evaluation receipt(s) for every final integration revision through its declared inputs. A disabled/skipped/failed review, stale test receipt or unresolved conflict cannot release integrated code.

`workspace_usage` records distinct host command/evaluation counts, union command environment elapsed time, bounded captured output bytes and published artifact/file/snapshot bytes separately from model usage. Native filesystem syscall byte counts remain `null` with completeness false; publication size is not labeled actual native I/O. Native Hermes reports observed own counters and actual `run_conversation.api_calls`, while unavailable descendant lineage remains incomplete. `WorkspaceService.close(lease)` closes a mutable lease with retained Git evidence; `acquire` can resume its same pinned revision. Native `sandbox` requirements remain unsupported and reject regardless of Docker evaluator availability.

## Publication compatibility and invocation counters

New publications explicitly filter generated Python `__pycache__` directories and `.pyc`/`.pyo` files. Other ignored regular output files remain force-published and hash-verifiable. Native coding processes receive `PYTHONDONTWRITEBYTECODE=1` and `PYTHONNOUSERSITE=1`. Existing immutable artifact receipts and Git trees are never rewritten; their canonical verification includes their original bytecode files when present. The new receipt's `publication_filter.generated_python_bytecode` states `excluded`.

`execution_counts` separates logical, leaf and Workflow module invocations, actual native-entry events, native-reported own model request counts and unknown leaf counts. Native entries increment only on actual process creation (or an accepted legacy gateway response), so a pre-admission failure stays zero. A wrapped Workflow does not add model/token charges. `model_requests_total` remains null and `model_request_counts_complete` false when complete internal request evidence is unavailable; available own native counts are named separately. Existing journals are read without mutation.

Docker command receipts also pin `environment.image_id` to the locally inspected immutable image, independently of the administrator's configured tag.

`workspace.publish` retains an unchanged canonical artifact when the owned revision/index and current file hashes still match. Its output adds `retained:true`, preserving the original artifact ID, producer and provenance without a model call. `WorkspaceService.publish(lease, provenance=...)` returns `(workspace_receipt, artifact_receipt, retained)`. Changed content still creates a fresh artifact; native invocation snapshots remain invocation-attributed.

Integration replay is idempotent for a previously consumed exact receipt after canonical verification and an actual Git ancestry check. Output `retained_artifacts` lists skipped committed receipts. New descendant artifacts from a repaired producer may replace that producer's earlier desired input while unchanged peers remain selected. A conflict persists still-unapplied IDs in `__workspace.pending_artifacts`; snapshotting a resolution does not erase that obligation. Rerun `artifact.integrate` with the desired contributor set to retain already integrated commits and apply the remainder. Every still-pending contributor must remain selected, and delivery rejects any pending contributor. This is generic host Action feedback behavior, with no task or Workflow-name branches.

Existing materialized input copies are revalidated for private directory/regular-file paths, immutable permissions, sizes and canonical byte hashes at every managed admission. Tampered copies or symlink replacements reject; trusted local execution never excuses a false canonical receipt.

Host Actions share a separate root process pool bounded by graph `max_concurrency`; nested Workflows also narrow through each local ancestor pool. Host slots do not occupy model-call slots or token/call budgets. Ancestor deadlines include host admission waits, command timeout/cancellation and process cleanup.

Managed native state follows the host runtime directory independently of writable permissions. In particular, a DSH read-only consumer writes native session logs only under its lease-private runtime and reads usage there. If a saved DSH native handle has no private checkpoint, the adapter starts a fresh native session and replays that role's own supplied dialogue; `context_mode:workspace-history-replay` and `native_context_rotation:private-checkpoint-unavailable` record the migration. It never resumes the missing handle through global session history. Claude managed configuration/temp state and Codex managed CODEX_HOME are also private; unbound legacy behavior is unchanged.

Required host command/evaluation capabilities are checked before native admission. The enabled declared graph and its bound saved Workflow descendants determine the requirements; an unused configured evaluator causes no probe. Trusted command IDs, authoritative paths/local executables and Docker daemon plus already-installed image availability must pass. Checks are single-flight and cached privately per root run, including failures; graph revisions check newly required Actions before new work. No image is pulled and no Project read performs execution probing.

Interrupted DSH calls preserve only committed usage facts matching the current private session/turn reference, including available cache counters and native metrics. Cancel, timeout and native errors retain an explicitly incomplete known subtotal before propagating the original failure. A bounded native cancel/close opportunity precedes owned process cleanup; a missing/unreadable accounting fact remains unknown. Existing native logs and prior run journals are read without rewriting.

Hermes native tool receipts follow its installed callbacks exactly: start `(tool_call_id, name, arguments)`, completion `(tool_call_id, name, arguments, function_result)`. The host receives the same stable ID plus tool name, bounded/redacted actual input/output, done/status and available native exit/error fields. Status uses installed Hermes `_detect_tool_failure`; when that classifier is unavailable the bridge keeps actual fields with an unknown status rather than inferring success. These tool receipts remain evidence of native activity, not authoritative host test receipts. Existing raw run traces are never rewritten.
