<script lang="ts">
	import type { WorkspaceReceipt, ArtifactReceipt } from '$lib/apis/execution';
	export let workspaces: Record<string, WorkspaceReceipt> = {};
	export let artifacts: ArtifactReceipt[] = [];
	export let operations: Record<string, any>[] = [];
	const operationCommands = (operation: Record<string, any>) =>
		operation.commands ?? (operation.argv ? [operation] : []);
</script>

{#if Object.keys(workspaces).length || artifacts.length || operations.length}
	<details class="my-3 rounded-xl border border-gray-200 dark:border-gray-700 p-3" open>
		<summary class="cursor-pointer text-sm font-medium">Files and verification</summary>
		<div class="mt-3 space-y-3 text-xs">
			{#each Object.values(workspaces) as workspace (workspace.id)}
				<div class="rounded-lg bg-gray-50 dark:bg-gray-900 p-3 break-all">
					<strong>{workspace.instance_id}</strong> · {workspace.status} · {workspace.backend}
					<p class="mt-1">
						Revision {workspace.current_revision} · generation {workspace.generation}
					</p>
					<p class="mt-1 text-gray-500">
						{workspace.guarantees?.os_sandbox
							? 'OS-enforced environment'
							: 'Trusted local execution · no OS sandbox'} · separate Git tree
					</p>
					{#if workspace.conflicts?.length}<p class="mt-2 text-red-600">
							Unresolved conflicts: {workspace.conflicts.join(', ')}
						</p>{/if}
				</div>
			{/each}
			{#each artifacts as artifact (artifact.id)}
				<details class="rounded-lg border p-2">
					<summary class="cursor-pointer"
						>Artifact · {artifact.producer} · {artifact.changed_paths.length} changed files · {artifact.size}
						bytes</summary
					>
					<p class="mt-2 break-all">Revision {artifact.revision}<br />SHA-256 {artifact.sha256}</p>
					<ul class="mt-2 list-disc pl-5">
						{#each artifact.files as file}<li class="break-all">
								{file.path} · {file.size} bytes · {file.sha256}
							</li>{/each}
					</ul>
				</details>
			{/each}
			{#each operations as operation, index (operation.id ?? `operation-${index}`)}
				<details class="rounded-lg border p-2">
					<summary class="cursor-pointer"
						>{operation.kind ?? 'Host command'} · {operation.status ??
							'recorded'}{operation.command_id ? ` · ${operation.command_id}` : ''}</summary
					>
					{#if operation.candidate_revision}<p class="my-2 break-all">
							Tested revision: {operation.candidate_revision}<br />Authoritative checks: {operation.checks_sha256}
						</p>{/if}
					{#if operation.environment}<p class="my-2">
							{operation.environment.backend} · {operation.environment.os_sandbox
								? 'OS-enforced scratch environment'
								: 'Trusted local process'} · network {operation.environment.network}
						</p>{/if}
					{#each operationCommands(operation) as command}
						<div class="mt-2 border-t pt-2">
							<p>
								{command.command_id} · {command.status} · exit {command.exit_code ?? 'unavailable'} ·
								{command.elapsed_ms ?? 0} ms
							</p>
							<pre class="mt-1 whitespace-pre-wrap break-words">{command.argv?.join(' ')}</pre>
							<p class="mt-1 text-gray-500">
								{command.environment?.backend} · {command.environment?.os_sandbox
									? 'OS-enforced'
									: 'Trusted local'}
							</p>
							{#if command.stdout}<pre
									class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap">{command.stdout}</pre>{/if}{#if command.stderr}<pre
									class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-red-600">{command.stderr}</pre>{/if}
						</div>
					{/each}
					{#if operation.conflicts?.length}<p class="my-2 text-red-600">
							Conflicts: {operation.conflicts.join(', ')}
						</p>{/if}
				</details>
			{/each}
		</div>
	</details>
{/if}
