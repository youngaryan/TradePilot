import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  BrainCircuit,
  Database,
  FlaskConical,
  Gauge,
  LayoutDashboard,
  RefreshCw,
  Rocket
} from "lucide-react";

import {
  getBacktestTemplates,
  getHealth,
  getPaperSummary,
  getStrategyCatalog,
  getSystemMetadata,
  listBacktestJobs,
  listPaperRunJobs
} from "./api/client";
import type {
  BacktestJob,
  BacktestTemplate,
  HealthResponse,
  PaperDashboardPayload,
  PaperRunJob,
  StrategyCatalogItem,
  SystemMetadata
} from "./api/types";
import { Badge } from "./components/Badge";
import { EmptyState } from "./components/Cards";
import { BacktestLab } from "./features/BacktestLab";
import { CommandCenter } from "./features/CommandCenter";
import { LiveOps } from "./features/LiveOps";
import { SystemGuide } from "./features/SystemGuide";

type ViewId = "command" | "live" | "backtests" | "system";

const views: Array<{ id: ViewId; label: string; description: string; icon: ReactNode }> = [
  {
    id: "command",
    label: "Home",
    description: "Plain-English money, risk, and current agent state.",
    icon: <LayoutDashboard size={18} />
  },
  {
    id: "live",
    label: "Run Paper",
    description: "Run fake-money agents with optional news and official SEC events.",
    icon: <Rocket size={18} />
  },
  {
    id: "backtests",
    label: "Backtest",
    description: "Test strategies before trusting them.",
    icon: <FlaskConical size={18} />
  },
  {
    id: "system",
    label: "Guide",
    description: "What every number means and how the backend works.",
    icon: <Database size={18} />
  }
];

function backendTone(health: HealthResponse | null) {
  return health?.status === "ok" ? "good" : "warn";
}

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>("command");
  const [payload, setPayload] = useState<PaperDashboardPayload | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [metadata, setMetadata] = useState<SystemMetadata | null>(null);
  const [catalog, setCatalog] = useState<StrategyCatalogItem[]>([]);
  const [templates, setTemplates] = useState<BacktestTemplate[]>([]);
  const [paperJobs, setPaperJobs] = useState<PaperRunJob[]>([]);
  const [backtestJobs, setBacktestJobs] = useState<BacktestJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  async function refreshAll() {
    setIsLoading(true);
    setError(null);
    try {
      const [
        nextHealth,
        nextPayload,
        nextCatalog,
        nextTemplates,
        nextMetadata,
        nextPaperJobs,
        nextBacktestJobs
      ] = await Promise.all([
        getHealth(),
        getPaperSummary(),
        getStrategyCatalog(),
        getBacktestTemplates(),
        getSystemMetadata(),
        listPaperRunJobs(),
        listBacktestJobs()
      ]);
      setHealth(nextHealth);
      setPayload(nextPayload);
      setCatalog(nextCatalog);
      setTemplates(nextTemplates);
      setMetadata(nextMetadata);
      setPaperJobs(nextPaperJobs);
      setBacktestJobs(nextBacktestJobs);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The backend did not return a usable response.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void refreshAll();
  }, []);

  const activeMeta = useMemo(() => views.find((view) => view.id === activeView) ?? views[0], [activeView]);

  return (
    <div className="quant-shell">
      <aside className="nav-rail" aria-label="Quant cockpit navigation">
        <div className="brand-mark">
          <BrainCircuit size={24} />
          <div>
            <strong>QuantOps</strong>
            <span>Research to paper</span>
          </div>
        </div>

        <nav className="nav-stack">
          {views.map((view) => (
            <button
              key={view.id}
              type="button"
              className={view.id === activeView ? "nav-item nav-item--active" : "nav-item"}
              onClick={() => setActiveView(view.id)}
            >
              {view.icon}
              <span>{view.label}</span>
            </button>
          ))}
        </nav>

        <div className="nav-footer">
          <Badge label={health?.status === "ok" ? "Backend online" : "Backend unknown"} tone={backendTone(health)} />
          <span>{metadata?.counts.experiment_runs ?? 0} saved experiments</span>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">Professional Quant Control Room</p>
            <h1>{activeMeta.label}</h1>
            <span>{activeMeta.description}</span>
          </div>
          <div className="topbar-actions">
            <Badge label={payload?.run_timestamp_utc ? "state loaded" : "waiting for run"} tone={payload?.run_timestamp_utc ? "good" : "warn"} />
            <button type="button" className="ghost-button" onClick={() => void refreshAll()} disabled={isLoading}>
              <RefreshCw size={17} />
              <span>{isLoading ? "Refreshing" : "Refresh"}</span>
            </button>
          </div>
        </header>

        {error ? (
          <section className="alert-card">
            <AlertTriangle size={19} />
            <div>
              <strong>Backend response problem</strong>
              <span>{error}</span>
            </div>
          </section>
        ) : null}

        {!payload && !error ? (
          <EmptyState
            icon={<Gauge size={34} />}
            title={isLoading ? "Loading quant cockpit" : "No paper state found"}
            body="The cockpit reads from the backend API. Start the backend, then run or replay a paper deployment to populate ledgers."
          />
        ) : null}

        {payload && activeView === "command" ? (
          <CommandCenter
            payload={payload}
            health={health}
            metadata={metadata}
            paperJobs={paperJobs}
            backtestJobs={backtestJobs}
          />
        ) : null}

        {payload && activeView === "live" ? (
          <LiveOps
            payload={payload}
            catalog={catalog}
            paperJobs={paperJobs}
            onJobsChange={setPaperJobs}
            onPaperPayload={setPayload}
            onRefresh={() => void refreshAll()}
          />
        ) : null}

        {activeView === "backtests" ? (
          <BacktestLab
            catalog={catalog}
            templates={templates}
            jobs={backtestJobs}
            onJobsChange={setBacktestJobs}
          />
        ) : null}

        {activeView === "system" ? (
          <SystemGuide
            health={health}
            metadata={metadata}
            paperJobs={paperJobs}
            backtestJobs={backtestJobs}
          />
        ) : null}

        <section className="research-note">
          <Gauge size={16} />
          <span>
            Everything here is fake-money research until a real broker adapter is deliberately added. Start with Home, then Run Paper, then Backtest.
          </span>
        </section>
      </main>
    </div>
  );
}
