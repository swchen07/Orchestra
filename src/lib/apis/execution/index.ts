import { WEBUI_API_BASE_URL } from '$lib/constants';
import type { Model } from '$lib/stores';
export type FrameworkId = 'direct' | 'hermes' | 'claude-code' | 'codex' | 'dsh';
export type Framework = { id: FrameworkId; name: string; description: string };
export type Provider = {
	id: string;
	name: string;
	framework_id: FrameworkId;
	source: string;
	models: string[];
	model_ids: string[];
	model_mappings: Record<string, string>;
	current?: boolean;
	default_model?: string;
	endpoint_host?: string;
	available: boolean;
	unavailable_reason?: string;
	common_config?: boolean;
	model_details?: Record<
		string,
		{ hidden?: boolean; effort_options?: string[]; default_effort?: string; display_name?: string }
	>;
	unlisted_models?: string[];
	catalog_warning?: string;
};
export type ExecutionSettings = {
	frameworks: Partial<
		Record<
			FrameworkId,
			{ enabled?: boolean; disabled_models?: string[]; disabled_providers?: string[] }
		>
	>;
};
export type WorkspacePolicy = {
	enabled: boolean;
	backend?: 'local-worktree';
	baseline_revision?: string;
	command_backend?: 'local-worktree' | 'docker';
	docker_image?: string;
	commands?: {
		id: string;
		argv: string[];
		timeout_seconds?: number;
		authoritative_paths?: string[];
	}[];
	hermes_profile?: string;
	hermes_source?: string;
	hermes_python?: string;
};
export type WorkspaceReceipt = {
	id: string;
	instance_id: string;
	current_revision: string;
	source_baseline: string;
	backend: string;
	status: string;
	generation: number;
	conflicts?: string[];
	guarantees: { os_sandbox: boolean; private_git_tree?: boolean; managed_api_ownership?: boolean };
};
export type ArtifactReceipt = {
	kind: 'workspace-artifact';
	id: string;
	producer: string;
	revision: string;
	sha256: string;
	size: number;
	changed_paths: string[];
	files: { path: string; size: number; sha256: string; media_type: string }[];
};
export type ProjectConfig = {
	workspace?: WorkspacePolicy;
	provider_ids: string[];
	default_model_id: string;
	directory: string | null;
};
export type Project = {
	id: string;
	name: string;
	user_id: string;
	data: {
		execution_participants?: string[];
		description?: string;
		system_prompt?: string;
		model_ids?: string[];
		execution_project: ProjectConfig;
	};
};
export type ProjectForm = {
	workspace?: WorkspacePolicy;
	name: string;
	description: string;
	system_prompt: string;
	directory: string;
	provider_ids: string[];
	default_model_id: string;
};
export type NativeConfig = {
	codex?: { installed: boolean; providers: Provider[] };
	dsh?: { installed: boolean; providers: Provider[] };
	hermes: { model?: string; provider?: string };
	'claude-code': {
		providers?: Provider[];
		models: string[];
		default?: string;
		installed: boolean;
		provider?: string;
		source?: string;
	};
};
export type Runtime = {
	id: FrameworkId;
	name: string;
	path: string;
	version: string;
	selection: string;
	unavailable?: boolean;
	candidates: { path: string; version: string }[];
};
export type ApiProvider = {
	id: string;
	framework_id: FrameworkId;
	kind: 'openai' | 'ollama';
	index: number;
	name: string;
	url: string;
	has_key: boolean;
	enabled: boolean;
	service_enabled: boolean;
	model_ids: string[];
	prefix_id: string;
	endpoint_host?: string;
	native_model?: string;
	native_provider?: string;
};
export type SettingsResponse = {
	runtimes?: Runtime[];
	adapters?: { id: FrameworkId; name: string }[];
	settings: ExecutionSettings;
	native: NativeConfig;
	frameworks: Framework[];
};
export type Catalog = { frameworks: Framework[]; models: Model[]; providers: Provider[] };
export async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
	const response = await fetch(`${WEBUI_API_BASE_URL}/execution${path}`, {
		method,
		headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.token}` },
		...(body === undefined ? {} : { body: JSON.stringify(body) })
	});
	const data = await response.json();
	if (!response.ok)
		throw new Error(
			typeof data.detail === 'string' ? data.detail : `配置请求失败（${response.status}）`
		);
	return data;
}
export const getExecutionCatalog = () => request<Catalog>('/catalog');
export const getNativeProfiles = () => request<ExecutionProfile[]>('/profiles');
export type Participant = {
	id: string;
	name: string;
	model_id: string;
	profile_id?: string | null;
	effort?: string | null;
	framework_id?: FrameworkId;
	provider_id?: string;
	provider_name?: string;
	model_name?: string;
	builtin?: boolean;
};
export type CallableModule = {
	id: string;
	name: string;
	module_type?: 'workflow' | string;
	input_schema?: unknown;
	output_schema?: unknown;
	model_id?: string;
	profile_id?: string | null;
	effort?: string | null;
	framework_id?: string;
	provider_name?: string;
	configuration?: {
		workflow_id: string;
		roles: { id: string; name: string }[];
		bindings: Record<string, string>;
	};
};
export type GraphRef = {
	source: 'input' | 'node' | 'report' | 'literal' | 'output' | 'invocation';
	port?: string;
	pointer?: string;
	node?: string;
	report_ref?: string;
	value?: unknown;
	optional?: boolean;
};
export type GraphNode = {
	action?: string;
	report_description?: string;
	enabled?: boolean;
	id: string;
	label?: string;
	op:
		| 'call'
		| 'workflow'
		| 'dispatch'
		| 'route'
		| 'map'
		| 'join'
		| 'loop'
		| 'checkpoint'
		| 'finish'
		| 'plan'
		| 'review'
		| 'input'
		| 'output'
		| 'command'
		| 'evaluate'
		| 'integrate'
		| 'publish';
	binding?: string;
	instruction?: string;
	inputs: Record<string, GraphRef>;
	context_key?: string;
	output_schema?: unknown;
	input_schema?: unknown;
	input_schema_inferred?: boolean;
	environment?: {
		filesystem?: 'inherit' | 'none' | 'read-only' | 'workspace-write';
		tools?: 'inherit' | 'none';
		network?: 'inherit' | 'provider-only';
		isolation?: 'logical' | 'process' | 'sandbox';
	};
	cases?: string[];
	policy?: string;
	quorum?: number;
	items?: GraphRef;
	max_iterations?: number;
	stop_when?: { pointer: string; equals: unknown };
	question?: string;
	acceptance?: string[];
	planning?: {
		initial?: boolean;
		on_events?: boolean;
		catalog_scope?: 'project' | 'roles';
		output_port?: string;
		observe_nodes?: string[];
	};
	review?: { max_repairs?: number; repair_node?: string };
	dispatch?: { targets: string[] };
	workspace?: { instance?: string; command_ids?: string[]; strategy?: 'select' | 'merge' };
};
export type WorkflowGraph = {
	delivery_port?: string;
	input_schema?: unknown;
	output_schema?: unknown;
	output_schema_inferred?: boolean;
	input_policy?: 'shared' | 'scoped';
	schema_version?: string;
	revision?: number;
	nodes: GraphNode[];
	edges: {
		from: string;
		to: string;
		on?: 'success' | 'terminal' | 'case' | 'revise';
		case?: string;
		generated?: boolean;
	}[];
	result_node: string;
	outputs?: Record<string, GraphRef>;
	controller?: {
		binding: string;
		initial?: boolean;
		on_events?: boolean;
		instructions?: string;
		catalog_scope?: 'project' | 'roles';
	} | null;
	reviewer?: { binding: string; instructions?: string; max_repairs?: number } | null;
	limits?: Partial<{
		max_calls: number;
		max_seconds: number;
		max_concurrency: number;
		max_replans: number;
		max_tokens: number;
	}>;
};
export type NodeState = {
	workflow?: {
		path: string;
		workflow_id?: string;
		child_run_id?: string;
		leaf_call_ids?: string[];
	};
	held?: boolean;
	status: string;
	started_at?: number;
	finished_at?: number;
	output?: Record<string, unknown>;
	error?: string;
	question?: string;
	call_ids?: string[];
	attempt_call_ids?: string[];
	plan_revision?: number;
};
export type Workflow = {
	example?: { purpose: string; task: string; expected: string };
	id?: string;
	name: string;
	preset_key?: string;
	builtin_key?: string;
	archived?: boolean;
	description?: string;
	mode?: 'single' | 'parallel' | 'summarize' | 'delegate';
	graph?: WorkflowGraph;
	execution_policy?: 'automatic' | 'review' | 'step';
	participant_ids?: string[];
	lead_id?: string;
	instructions: string;
	max_rounds: number;
	version?: number;
	available?: boolean;
	roles?: { id: string; name: string; instructions: string }[];
	lead_role_id?: string;
	bindings?: Record<string, string>;
	unavailable_reason?: string;
};
export type WorkflowOverrides = {
	executor_pool?: string[];
	workflow_id: string;
	roles: Record<string, { participant_id: string; effort?: string | null }>;
};
export type WorkflowEvent = {
	seq: number;
	at: number;
	updated_at?: number;
	type: string;
	title: string;
	role_id?: string;
	call_id?: string;
	text?: string;
	data?: Record<string, unknown>;
	report_id?: string;
};
export type NativeUsage = {
	reconciled_from_receipts?: boolean;
	root_id: string;
	complete: boolean;
	child_count: number;
	warnings: string[];
	self: Record<string, number>;
	children: Record<string, number>;
	inclusive: Record<string, number>;
	timings: {
		wall_ms?: number;
		self_exclusive_ms?: number;
		children_exclusive_ms?: number;
		tree_exclusive_ms?: number;
	};
	agents: {
		id: string;
		parent_id?: string;
		native_session_id?: string;
		native_agent_id?: string;
		status: string;
		usage: Record<string, number>;
		source_event_ids: string[];
		timings: {
			wall_ms?: number;
			exclusive_wall_ms?: number;
			started_at?: number;
			finished_at?: number;
		};
	}[];
};
export type WorkflowCall = {
	workflow_path?: string;
	local_node_id?: string;
	local_participant_id?: string;
	module?: {
		timings?: {
			invocation_ms?: number;
			self_exclusive_ms?: number;
			children_exclusive_ms?: number;
			tree_exclusive_ms?: number;
		};
	};
	id: string;
	participant_id: string;
	name: string;
	status: string;
	kind?: string;
	node_id?: string;
	plan_revision?: number;
	binding?: Record<string, string>;
	phase?: string;
	input: string;
	report_id?: string;
	output?: string;
	error?: string;
	created_at: number;
	started_at?: number;
	finished_at?: number;
	last_event_at?: number;
	depends_on?: string[];
	usage?: Record<string, unknown>;
	request_stats?: {
		input_chars: number;
		retained_context_chars: number;
		adapter_context_chars: number;
		submitted_text_chars: number;
		compacted_snapshots: number;
		replay_chars_removed: number;
		delivery: string;
	};
	observed?: Record<string, string>;
	execution?: Record<string, any> & { native_usage?: NativeUsage };
};
export type WorkflowRun = {
	workspace_states?: Record<string, WorkspaceReceipt>;
	host_operations?: Record<string, any>[];
	workflow_path?: string;
	child_workflows?: Record<string, WorkflowRun>;
	module_reports?: { id: string; role_id: string; name: string; output: string }[];
	review_attempts?: {
		node_id: string;
		call_id?: string;
		summary?: string;
		status: string;
		revision: number;
		issues?: string[];
	}[];
	module_names?: Record<string, string>;
	review?: {
		status: string;
		call_id?: string;
		revision: number;
		summary?: string;
		issues?: string[];
		checked_report_refs?: string[];
	};
	plan?: WorkflowGraph;
	node_states?: Record<string, NodeState>;
	plan_revisions?: {
		revision: number;
		at: number;
		reason: string;
		source: string;
		graph: WorkflowGraph;
	}[];
	execution_policy?: string;
	control?: {
		paused: boolean;
		controller_running: boolean;
		controller_calls: number;
		review_repairs?: number;
		controller_limit?: number;
		can_edit: boolean;
		errors: string[];
		budget?: {
			reported_tokens: number;
			metered_calls: number;
			calls: number;
			max_tokens: number;
			exceeded: boolean;
		};
	};
	modules?: CallableModule[];
	id: string;
	status: string;
	role_activity?: Record<string, { status: string; phase: string }>;
	created_at: number;
	updated_at?: number;
	heartbeat_at?: number;
	last_event_at?: number;
	finished_at?: number;
	trace_version?: number;
	legacy_trace?: boolean;
	events_omitted?: number;
	events?: WorkflowEvent[];
	template?: {
		name: string;
		id: string;
		roles?: { id: string; name: string }[];
		graph?: WorkflowGraph;
		lead_id?: string;
	};
	participants?: {
		id: string;
		name: string;
		binding_id?: string;
		module_type?: string;
		framework_id?: string;
		provider_name?: string;
		model_name?: string;
	}[];
	output?: string;
	error?: string;
	continuation?: {
		previous_run_ids: string[];
		roles: Record<string, { history_messages: number; source_run_id?: string; method: string }>;
	};
	calls: WorkflowCall[];
};
export const getParticipants = (projectId = '') =>
	request<Participant[]>(`/participants?project_id=${encodeURIComponent(projectId)}`);
export const getModules = (projectId = '') =>
	request<CallableModule[]>(`/modules?project_id=${encodeURIComponent(projectId)}`);
export const getProjectParticipants = (id: string) =>
	request<{ available: Participant[]; selected_ids: string[] }>(
		`/projects/${encodeURIComponent(id)}/participant-options`
	);
export const saveProjectParticipants = (id: string, participant_ids: string[]) =>
	request(`/projects/${encodeURIComponent(id)}/participants`, 'PUT', { participant_ids });
export const saveParticipant = (
	value: Pick<Participant, 'id' | 'name' | 'model_id' | 'profile_id' | 'effort'>
) => request<Participant>('/participants', 'POST', value);
export const getWorkflows = (projectId = '', archivedId = '') =>
	request<Workflow[]>(
		`/workflows?project_id=${encodeURIComponent(projectId)}&archived_id=${encodeURIComponent(archivedId)}`
	);
export const getWorkflowStarters = () => request<Workflow[]>('/workflows/starters');
export const getDeletedWorkflows = () => request<Workflow[]>('/workflows/deleted');
export const deleteWorkflow = (value: Workflow) =>
	request(`/workflows/${encodeURIComponent(value.id!)}?version=${value.version ?? 1}`, 'DELETE');
export const restoreWorkflow = (id: string) =>
	request<Workflow>(`/workflows/${encodeURIComponent(id)}/restore`, 'POST');
export const getProjectWorkflowBindings = (id: string) =>
	request<{ workflows: Workflow[]; participants: Participant[]; modules: CallableModule[] }>(
		`/projects/${encodeURIComponent(id)}/workflow-bindings`
	);
export const saveProjectWorkflowBindings = (
	id: string,
	workflow_id: string,
	bindings: Record<string, string>
) =>
	request(`/projects/${encodeURIComponent(id)}/workflow-bindings`, 'PUT', {
		workflow_id,
		bindings
	});
export const saveWorkflow = (value: Workflow) =>
	request<Workflow>(
		'/workflows',
		'POST',
		Object.fromEntries(
			Object.entries(value).filter(([key]) =>
				[
					'id',
					'name',
					'mode',
					'participant_ids',
					'lead_id',
					'instructions',
					'max_rounds',
					'version',
					'roles',
					'lead_role_id',
					'description',
					'graph',
					'execution_policy',
					'preset_key',
					'example'
				].includes(key)
			)
		)
	);
export const getWorkflowRun = (id: string) =>
	request<WorkflowRun>(`/workflow-runs/${encodeURIComponent(id)}`);
export const getExecutionSettings = () => request<SettingsResponse>('/settings');
export const saveExecutionSettings = (settings: ExecutionSettings) =>
	request<SettingsResponse>('/settings', 'PUT', settings);
export const getProjects = () => request<Project[]>('/projects');
export type ResourceCatalog = {
	integration: { compatible: boolean; version?: string; error?: string };
	resources: {
		skills: Resource[];
		prompts: Resource[];
		mcp: Resource[];
		connection_presets: Resource[];
	};
};
export const getNativeResources = () => request<ResourceCatalog>('/resources/catalog');
export const getNativeResourceText = (id: string) =>
	request<{ content: string }>(`/resources/${encodeURIComponent(id)}`);
export type NativeSession = {
	webui_chat_id?: string;
	id: string;
	framework_id: string;
	title: string;
	directory: string | null;
	updated_at: number;
	model?: string;
	source: string;
};
export type NativePreview = {
	session: NativeSession;
	messages: { role: string; content: string; timestamp?: number }[];
	truncated: boolean;
	read_only: boolean;
};
export const listNativeSessions = (framework = '', q = '', offset = 0, includeAutomation = false) =>
	request<{
		items: NativeSession[];
		total: number;
		counts: Record<string, number>;
		warnings: string[];
	}>(
		`/native-sessions?${new URLSearchParams({ framework, q, offset: String(offset), include_automation: String(includeAutomation) })}`
	);
export const readNativeSession = (id: string) =>
	request<NativePreview>(`/native-sessions/${encodeURIComponent(id)}`);
export const listConversations = (framework = '', q = '', offset = 0, includeAutomation = false) =>
	request<Awaited<ReturnType<typeof listNativeSessions>>>(
		`/conversations?${new URLSearchParams({ framework, q, offset: String(offset), include_automation: String(includeAutomation) })}`
	);
export const importNativeSession = (id: string, projectId: string, modelId: string) =>
	request<{ id: string; reused: boolean }>(
		`/native-sessions/${encodeURIComponent(id)}/import`,
		'POST',
		{ project_id: projectId || null, model_id: modelId }
	);
export const getProject = (id: string) =>
	request<Project | null>(`/projects/${encodeURIComponent(id)}`);
export const createProject = (form: ProjectForm) => request<Project>('/projects', 'POST', form);
export const updateProject = (id: string, form: ProjectForm) =>
	request<Project>(`/projects/${encodeURIComponent(id)}`, 'PUT', form);

export type ExecutionProfile = {
	id: string;
	name: string;
	framework_id: string;
	kind: string;
	source: string;
	available: boolean;
	execution_mode: string;
	note?: string;
	model_hint?: string;
};
export type Resource = {
	id: string;
	name: string;
	kind: string;
	description?: string;
	frameworks?: string[];
	available: boolean;
	executable?: boolean;
	note?: string;
};
export type ProjectBindings = {
	skill_ids?: string[];
	prompt_ids?: string[];
	mcp_ids?: string[];
	profile_id?: string | null;
};
export type Session = {
	id: string;
	title: string;
	updated_at: number;
	source?: string;
	stage?: string;
	active?: boolean;
	response_done?: boolean;
	workflow_status?: string;
	error?: boolean;
	framework_id?: string;
	session_id?: string;
};
export type UsageRecord = {
	usage_available?: boolean;
	id: string;
	source: string;
	model: string;
	input_tokens: number;
	output_tokens: number;
	cache_read_tokens: number;
	cache_write_tokens: number;
	cost_usd: string | null;
	created_at: number;
};
export type TopologyNode = {
	id: string;
	name: string;
	model_id: string;
	profile_id?: string | null;
	prompt: string;
	depends_on: string[];
};
export type Topology = { id?: string; name: string; nodes: TopologyNode[]; layers?: string[][] };
export type TopologyRun = {
	id: string;
	name: string;
	status: string;
	task: string;
	error?: string;
	nodes: Record<string, { name: string; status: string; chat_id: string | null; error?: string }>;
};
export type ProjectDashboard = {
	project: Project;
	integration: {
		version?: string;
		commit?: string;
		schema_version?: number;
		compatible: boolean;
		error?: string;
		hooks?: string[];
	};
	resources: {
		skills: Resource[];
		prompts: Resource[];
		mcp: Resource[];
		connection_presets: Resource[];
	};
	profiles: ExecutionProfile[];
	bindings: ProjectBindings;
	sessions: Session[];
	native_sessions: Session[];
	usage: {
		summary: {
			input_tokens: number;
			output_tokens: number;
			cache_read_tokens: number;
			cache_write_tokens: number;
			total_tokens: number;
			known_cost_usd: string;
			unknown_cost_records: number;
			unmetered_records?: number;
			request_count: number;
		};
		records: UsageRecord[];
		coverage: string;
	};
	topologies: Topology[];
	runs: TopologyRun[];
};
export const getProjectDashboard = (id: string) =>
	request<ProjectDashboard>(`/projects/${encodeURIComponent(id)}/dashboard`);
export const getProjectProfiles = (id: string) =>
	request<ExecutionProfile[]>(`/projects/${encodeURIComponent(id)}/profiles`);
export const saveProjectBindings = (id: string, bindings: ProjectBindings) =>
	request<ProjectBindings>(`/projects/${encodeURIComponent(id)}/bindings`, 'PUT', bindings);
export const createProjectProfile = (
	id: string,
	value: { name: string; framework_id: string; prompt: string }
) => request<ExecutionProfile>(`/projects/${encodeURIComponent(id)}/profiles`, 'POST', value);
export const getResourcePreview = (id: string, resource: string) =>
	request<{ content: string }>(
		`/projects/${encodeURIComponent(id)}/resources/${encodeURIComponent(resource)}`
	);
export const getNativeSession = (id: string, session: string) =>
	request<{ session: Session; messages: { role: string; content: string }[]; truncated: boolean }>(
		`/projects/${encodeURIComponent(id)}/native-sessions/${encodeURIComponent(session)}`
	);
export const moveProjectSession = (id: string, chat: string, stage: string) =>
	request(`/projects/${encodeURIComponent(id)}/sessions/${encodeURIComponent(chat)}/stage`, 'PUT', {
		stage
	});
export const createProjectTask = (id: string, name: string) =>
	request<{ id: string }>(`/projects/${encodeURIComponent(id)}/tasks`, 'POST', { name });
export const saveProjectTopology = (id: string, topology: Topology) =>
	request<Topology>(`/projects/${encodeURIComponent(id)}/topologies`, 'POST', {
		id: topology.id,
		name: topology.name,
		nodes: topology.nodes
	});
export const startTopologyRun = (id: string, topology: string, task: string) =>
	request<TopologyRun>(
		`/projects/${encodeURIComponent(id)}/topologies/${encodeURIComponent(topology)}/runs`,
		'POST',
		{ task }
	);
export const stopTopologyRun = (id: string, run: string) =>
	request(`/projects/${encodeURIComponent(id)}/runs/${encodeURIComponent(run)}/stop`, 'POST');

export const matchProjectWorkflows = (id: string, workflow_id?: string) =>
	request<{ workflows: Workflow[]; participants: Participant[]; modules?: CallableModule[] }>(
		`/projects/${encodeURIComponent(id)}/workflow-bindings/match`,
		'POST',
		{ workflow_id }
	);
export const refreshConnections = () => request<SettingsResponse>('/refresh', 'POST');
export const saveRuntime = (family: FrameworkId, path: string) =>
	request<SettingsResponse>(`/runtimes/${family}`, 'PUT', { path });
export const getApiProviders = () => request<ApiProvider[]>('/api-providers');
export const saveApiProvider = (value: {
	kind: 'openai' | 'ollama';
	index?: number;
	name: string;
	url: string;
	key?: string;
	clear_key?: boolean;
	enabled: boolean;
	model_ids: string[];
	prefix_id: string;
	framework_id?: 'direct' | 'hermes';
	native_model?: string;
	native_provider?: string;
}) => request<ApiProvider[]>('/api-providers', 'PUT', value);

export const controlWorkflowRun = (id: string, value: Record<string, unknown>) =>
	request<WorkflowRun>(`/workflow-runs/${encodeURIComponent(id)}/control`, 'POST', value);
export const validateWorkflowGraph = (value: WorkflowGraph) =>
	request<WorkflowGraph>('/graphs/validate', 'POST', value);
