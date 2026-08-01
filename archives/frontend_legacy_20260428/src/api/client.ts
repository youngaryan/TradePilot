import type {
  BacktestJob,
  BacktestRunRequest,
  BacktestTemplate,
  HealthResponse,
  PaperDashboardPayload,
  PaperRunRequest,
  PaperRunJob,
  PaperStrategy,
  StrategyCatalogItem,
  SystemMetadata
} from "./types";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

function apiPath(path: string) {
  return `${API_BASE_URL}${path}`;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(apiPath(path), {
    headers: {
      "Content-Type": "application/json",
      ...init?.headers
    },
    ...init
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function getHealth() {
  return requestJson<HealthResponse>("/api/health");
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
