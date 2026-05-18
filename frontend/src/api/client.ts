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
  CommitteeDecision,
  ExperimentRecord,
  HealthResponse,
  MarketResearchJob,
  MarketResearchReportDetail,
  MarketResearchReportListQuery,
  MarketResearchRuntimeConfig,
  MarketResearchReportSummary,
  MarketResearchRunRequest,
  StockUniverseResponse,
  PaperDashboardPayload,
  PaperRunRequest,
  PaperRunJob,
  PaperAgentRecord,
  ProjectCreateRequest,
  RefreshRunRecord,
  RefreshStatusPayload,
  SentimentAccumulationRequest,
  SentimentAccumulationJob,
  SentimentDatasetInfo,
  SentimentDatasetPayload,
  SentimentEvalResult,
  SentimentModelInfo,
  FinancialEventsPayload,
  PaperStrategy,
  PricingPayload,
  SignupRequest,
  StrategyBuilderApprovalResponse,
  StrategyBuilderMessage,
  StrategyBuilderResponse,
  StrategyCatalogItem,
  SystemAdminCounts,
  SystemMetadata,
  TelemetryEventRecord,
  TelemetryEventRequest,
  UserStrategyRecord,
  WorkspacePayload
} from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
let activeOrganizationId: string | null = null;

export function setApiAuth(_token: string | null, organizationId?: string | null) {
  if (organizationId !== undefined) activeOrganizationId = organizationId;
}

export function setActiveOrganizationId(organizationId: string | null) {
  activeOrganizationId = organizationId;
}

function apiPath(path: string) {
  return `${API_BASE_URL}${path}`;
}

function cookieValue(name: string) {
  return document.cookie
    .split(";")
    .map((item) => item.trim())
    .find((item) => item.startsWith(`${name}=`))
    ?.slice(name.length + 1);
}

function csrfToken() {
  const token = cookieValue("quantops_csrf");
  return token ? decodeURIComponent(token) : null;
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
  if (activeOrganizationId) headers["X-Organization-Id"] = activeOrganizationId;
  const method = String(init?.method ?? "GET").toUpperCase();
  const csrf = csrfToken();
  if (csrf && !["GET", "HEAD", "OPTIONS"].includes(method)) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(apiPath(path), {
    credentials: "include",
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
  return requestJson<AuthResponse>("/api/auth/me");
}

export function logout() {
  return requestJson<{ status: string }>("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
}

export function requestEmailVerification(email: string) {
  return requestJson<{ status: string; delivery?: Record<string, unknown> }>("/api/auth/verify-email/request", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

export function verifyEmail(token: string) {
  return requestJson<{ status: string; user?: AuthResponse["user"] }>("/api/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token })
  });
}

export function requestPasswordReset(email: string) {
  return requestJson<{ status: string; message: string }>("/api/auth/password-reset/request", {
    method: "POST",
    body: JSON.stringify({ email })
  });
}

export function confirmPasswordReset(token: string, newPassword: string) {
  return requestJson<{ status: string; message: string }>("/api/auth/password-reset/confirm", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword })
  });
}

export function setupMfa() {
  return requestJson<{ status: string; method: string; secret: string; otpauth_url: string; enabled: boolean }>("/api/auth/mfa/setup", {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function verifyMfa(code: string) {
  return requestJson<{ status: string; method: string }>("/api/auth/mfa/verify", {
    method: "POST",
    body: JSON.stringify({ code })
  });
}

export function exportAccount() {
  return requestJson<Record<string, unknown>>("/api/account/export");
}

export function deleteAccount() {
  return requestJson<{ status: string; user?: AuthResponse["user"] }>("/api/account", {
    method: "DELETE",
    body: JSON.stringify({})
  });
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

export function syncBillingSubscription() {
  return requestJson<Record<string, unknown>>("/api/billing/sync", {
    method: "POST",
    body: JSON.stringify({})
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

export function getSystemAdminCounts() {
  return requestJson<SystemAdminCounts>("/api/system/admin-counts");
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
  return requestJson<StrategyCatalogItem[]>("/api/strategies/allowed");
}

export function getPublicStrategyCatalog() {
  return requestJson<StrategyCatalogItem[]>("/api/strategies/catalog");
}

export function listUserStrategies() {
  return requestJson<UserStrategyRecord[]>("/api/strategies/user");
}

export function chatStrategyBuilder(messages: StrategyBuilderMessage[], draftSpec?: Record<string, unknown> | null) {
  return requestJson<StrategyBuilderResponse>("/api/strategies/builder/chat", {
    method: "POST",
    body: JSON.stringify({ messages, draft_spec: draftSpec ?? null })
  });
}

export function approveStrategySpec(spec: Record<string, unknown>, approvalText: string) {
  return requestJson<StrategyBuilderApprovalResponse>("/api/strategies/builder/approve", {
    method: "POST",
    body: JSON.stringify({ spec, approved: true, approval_text: approvalText })
  });
}

export function listAdminUserStrategies(params?: { organization_id?: string; user_id?: string; status?: string; risk_level?: string; limit?: number }) {
  const search = new URLSearchParams();
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  });
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return requestJson<UserStrategyRecord[]>(`/api/admin/user-strategies${suffix}`);
}

export function updateAdminUserStrategy(strategyId: string, status: "active" | "disabled") {
  return requestJson<UserStrategyRecord>(`/api/admin/user-strategies/${encodeURIComponent(strategyId)}`, {
    method: "PATCH",
    body: JSON.stringify({ status })
  });
}

export function deleteAdminUserStrategy(strategyId: string) {
  return requestJson<UserStrategyRecord>(`/api/admin/user-strategies/${encodeURIComponent(strategyId)}`, {
    method: "DELETE"
  });
}

export function getSentimentEvaluation(params?: { dataset?: string; models?: string; max_samples?: number }) {
  const q = new URLSearchParams();
  if (params?.dataset) q.set("dataset", params.dataset);
  if (params?.models) q.set("models", params.models);
  if (params?.max_samples) q.set("max_samples", String(params.max_samples));
  const qs = q.toString();
  return requestJson<SentimentEvalResult>(`/api/admin/sentiment-evaluation${qs ? `?${qs}` : ""}`);
}

export function getSentimentDatasets() {
  return requestJson<SentimentDatasetInfo[]>("/api/admin/sentiment-datasets");
}

export function getSentimentModels() {
  return requestJson<SentimentModelInfo[]>("/api/admin/sentiment-models");
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

export function startMarketResearchJob(request: MarketResearchRunRequest) {
  return requestJson<MarketResearchJob>("/api/market-research/run-job", {
    method: "POST",
    body: JSON.stringify(request)
  });
}

export function getMarketResearchRuntime() {
  return requestJson<MarketResearchRuntimeConfig>("/api/market-research/runtime");
}

export function listMarketResearchJobs() {
  return requestJson<MarketResearchJob[]>("/api/market-research/jobs");
}

export function getMarketResearchJob(jobId: string) {
  return requestJson<MarketResearchJob>(`/api/market-research/jobs/${encodeURIComponent(jobId)}`);
}

export function getStockUniverse(params?: { sector?: string; country?: string; exchange?: string }) {
  const search = new URLSearchParams();
  if (params?.sector) search.set("sector", params.sector);
  if (params?.country) search.set("country", params.country);
  if (params?.exchange) search.set("exchange", params.exchange);
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return requestJson<StockUniverseResponse>(`/api/market-research/universe${suffix}`);
}

export function getUniverseGroups() {
  return requestJson<{ sectors: Array<{ name: string; count: number }>; countries: Array<{ name: string; count: number }>; exchanges: Array<{ name: string; count: number }> }>("/api/market-research/universe/groups");
}

export function listCommitteeDecisions(ticker?: string, limit?: number) {
  const search = new URLSearchParams();
  if (ticker) search.set("ticker", ticker);
  if (limit) search.set("limit", String(limit));
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return requestJson<CommitteeDecision[]>(`/api/market-research/decisions${suffix}`);
}

export function getDecisionsSummary() {
  return requestJson<{ total_decisions: number; unique_tickers: number; decision_breakdown: Record<string, number>; average_confidence: number }>("/api/market-research/decisions/summary");
}

export function getChartData(jobId: string) {
  return requestJson<{ charts: Record<string, unknown> }>(`/api/market-research/charts/${encodeURIComponent(jobId)}`);
}

export function listWorkspaceReports(query?: MarketResearchReportListQuery) {
  const search = new URLSearchParams();
  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim()) search.set(key, String(value));
  });
  const suffix = search.toString() ? `?${search.toString()}` : "";
  return requestJson<MarketResearchReportSummary[]>(`/api/workspaces/reports${suffix}`);
}

export function getWorkspaceReport(reportId: string) {
  return requestJson<MarketResearchReportDetail>(`/api/workspaces/reports/${encodeURIComponent(reportId)}`);
}

export function deleteWorkspaceReport(reportId: string) {
  return requestJson<MarketResearchReportSummary>(`/api/workspaces/reports/${encodeURIComponent(reportId)}`, {
    method: "DELETE"
  });
}

export function regenerateWorkspaceReport(reportId: string) {
  return requestJson<MarketResearchJob>(`/api/workspaces/reports/${encodeURIComponent(reportId)}/regenerate`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export function exportWorkspaceReport(reportId: string) {
  return requestJson<{ format: "json" | string; report: MarketResearchReportDetail }>(
    `/api/workspaces/reports/${encodeURIComponent(reportId)}/export?format=json`
  );
}

export function getSentimentDataset(datasetId?: string | null) {
  const suffix = datasetId ? `?dataset_id=${encodeURIComponent(datasetId)}` : "";
  return requestJson<SentimentDatasetPayload>(`/api/sentiment/dataset${suffix}`);
}

export function getFinancialEvents(params: { symbols: string[]; start: string; end: string; limit?: number }) {
  const search = new URLSearchParams();
  search.set("symbols", params.symbols.join(","));
  search.set("start", params.start);
  search.set("end", params.end);
  if (params.limit) search.set("limit", String(params.limit));
  return requestJson<FinancialEventsPayload>(`/api/sentiment/financial-events?${search.toString()}`);
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
