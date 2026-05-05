import type {
  BacktestJob,
  BacktestRunRequest,
  BacktestTemplate,
  AdminOverviewPayload,
  AdminUserRecord,
  AuthResponse,
  ApiKeyCreateRequest,
  BillingCheckoutRequest,
  BillingResponse,
  BillingStatusPayload,
  ExperimentRecord,
  HealthResponse,
  PaperDashboardPayload,
  PaperRunRequest,
  PaperRunJob,
  PaperAgentRecord,
  ProjectCreateRequest,
  RefreshRunRecord,
  RefreshStatusPayload,
  SentimentAccumulationRequest,
  SentimentAccumulationJob,
  SentimentDatasetPayload,
  PaperStrategy,
  PricingPayload,
  SignupRequest,
  StrategyCatalogItem,
  SystemMetadata,
  TelemetryEventRecord,
  TelemetryEventRequest,
  WorkspacePayload
} from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
let authToken: string | null = null;
let activeOrganizationId: string | null = null;

export function setApiAuth(token: string | null, organizationId?: string | null) {
  authToken = token;
  if (organizationId !== undefined) activeOrganizationId = organizationId;
}

export function setActiveOrganizationId(organizationId: string | null) {
  activeOrganizationId = organizationId;
}

function apiPath(path: string) {
  return `${API_BASE_URL}${path}`;
}

async function responseErrorMessage(response: Response) {
  const body = await response.text();
  if (!body) return `Request failed with status ${response.status}`;

  try {
    const payload = JSON.parse(body) as { detail?: unknown; error?: unknown; message?: unknown };
    const detail = payload.detail ?? payload.error ?? payload.message;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object" && "msg" in item) return String((item as { msg: unknown }).msg);
          return JSON.stringify(item);
        })
        .join(" ");
    }
    if (detail != null) return JSON.stringify(detail);
  } catch {
    return body;
  }

  return body;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json"
  };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  if (activeOrganizationId) headers["X-Organization-Id"] = activeOrganizationId;
  const response = await fetch(apiPath(path), {
    headers: {
      ...headers,
      ...init?.headers
    },
    ...init
  });

  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }

  return response.json() as Promise<T>;
}

export function getHealth() {
  return requestJson<HealthResponse>("/api/health");
}

export function login(email: string, password: string) {
  return requestJson<AuthResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password })
  });
}

export function signup(request: SignupRequest) {
  return requestJson<AuthResponse>("/api/auth/signup", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function getCurrentUser() {
  return requestJson<Omit<AuthResponse, "access_token" | "token_type">>("/api/auth/me");
}

export function logout() {
  return requestJson<{ status: string }>("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
}

export function getWorkspace() {
  return requestJson<WorkspacePayload>("/api/workspaces");
}

export function getPricing() {
  return requestJson<PricingPayload>("/api/billing/pricing");
}

export function getBillingStatus() {
  return requestJson<BillingStatusPayload>("/api/billing/status");
}

export function createProject(request: ProjectCreateRequest) {
  return requestJson<WorkspacePayload["projects"][number]>("/api/workspaces/projects", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function createApiKeyMetadata(request: ApiKeyCreateRequest) {
  return requestJson<WorkspacePayload["api_keys"][number]>("/api/workspaces/api-keys", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function listWorkspaceExperiments() {
  return requestJson<ExperimentRecord[]>("/api/workspaces/experiments");
}

export function getWorkspaceExperiment(experimentId: string) {
  return requestJson<ExperimentRecord>(`/api/workspaces/experiments/${encodeURIComponent(experimentId)}`);
}

export function listWorkspacePaperAgents() {
  return requestJson<PaperAgentRecord[]>("/api/workspaces/paper-agents");
}

export function getWorkspacePaperAgent(agentId: string) {
  return requestJson<PaperAgentRecord>(`/api/workspaces/paper-agents/${encodeURIComponent(agentId)}`);
}

export function startBillingCheckout(request: BillingCheckoutRequest) {
  return requestJson<BillingResponse>("/api/billing/checkout", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function openBillingPortal(returnUrl?: string) {
  return requestJson<BillingResponse>("/api/billing/portal", {
    method: "POST",
    body: JSON.stringify({ return_url: returnUrl ?? null })
  });
}

export function getAdminOverview() {
  return requestJson<AdminOverviewPayload>("/api/admin/overview");
}

export function listAdminUsers(params?: {
  search?: string;
  role?: string;
  status?: string;
  sort_by?: string;
  sort_dir?: string;
  limit?: number;
}) {
  const search = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim()) search.set(key, String(value));
  });
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return requestJson<AdminUserRecord[]>(`/api/admin/users${suffix}`);
}

export function updateAdminUser(userId: string, request: { role?: string | null; status?: string | null }) {
  return requestJson<AuthResponse["user"]>(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: JSON.stringify(request)
  });
}

export function trackTelemetryEvent(request: TelemetryEventRequest) {
  return requestJson<{ stored: boolean; reason?: string; event?: TelemetryEventRecord }>("/api/telemetry/events", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function listTelemetryEvents(limit = 100) {
  return requestJson<TelemetryEventRecord[]>(`/api/telemetry/events?limit=${encodeURIComponent(limit)}`);
}

export function getRefreshStatus() {
  return requestJson<RefreshStatusPayload>("/api/refresh/status");
}

export function runDailyRefresh(force = false) {
  return requestJson<RefreshRunRecord>("/api/refresh/run", {
    method: "POST",
    body: JSON.stringify({ force })
  });
}

export function getSystemMetadata() {
  return requestJson<SystemMetadata>("/api/system/metadata");
}

export function getPaperSummary() {
  return requestJson<PaperDashboardPayload>("/api/paper/summary");
}

export function getPaperStrategy(strategyName: string) {
  return requestJson<PaperStrategy>(`/api/paper/strategies/${encodeURIComponent(strategyName)}`);
}

export function runPaperBatch(asofDate?: string) {
  return requestJson<PaperDashboardPayload>("/api/paper/run", {
    method: "POST",
    body: JSON.stringify({ asof_date: asofDate || null })
  });
}

export function startPaperRunJob(request?: PaperRunRequest) {
  return requestJson<PaperRunJob>("/api/paper/run-job", {
    method: "POST",
    body: JSON.stringify(request ?? {})
  });
}

export function listPaperRunJobs() {
  return requestJson<PaperRunJob[]>("/api/paper/jobs");
}

export function getPaperRunJob(jobId: string) {
  return requestJson<PaperRunJob>(`/api/paper/jobs/${encodeURIComponent(jobId)}`);
}

export function getStrategyCatalog() {
  return requestJson<StrategyCatalogItem[]>("/api/strategies/catalog");
}

export function getBacktestTemplates() {
  return requestJson<BacktestTemplate[]>("/api/backtests/templates");
}

export function startBacktest(request: BacktestRunRequest) {
  return requestJson<BacktestJob>("/api/backtests/run", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function listBacktestJobs() {
  return requestJson<BacktestJob[]>("/api/backtests/jobs");
}

export function getBacktestJob(jobId: string) {
  return requestJson<BacktestJob>(`/api/backtests/jobs/${encodeURIComponent(jobId)}`);
}

export function getSentimentDataset(outputDir?: string | null) {
  const suffix = outputDir ? `?output_dir=${encodeURIComponent(outputDir)}` : "";
  return requestJson<SentimentDatasetPayload>(`/api/sentiment/dataset${suffix}`);
}

export function accumulateSentiment(request: SentimentAccumulationRequest) {
  return requestJson<SentimentDatasetPayload>("/api/sentiment/accumulate", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function startSentimentAccumulationJob(request: SentimentAccumulationRequest) {
  return requestJson<SentimentAccumulationJob>("/api/sentiment/accumulate-job", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function listSentimentAccumulationJobs() {
  return requestJson<SentimentAccumulationJob[]>("/api/sentiment/jobs");
}

export function getSentimentAccumulationJob(jobId: string) {
  return requestJson<SentimentAccumulationJob>(`/api/sentiment/jobs/${encodeURIComponent(jobId)}`);
}
