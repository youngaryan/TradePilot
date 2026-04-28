import { useMemo, useState } from "react";
import { BookOpen, Copy, Search, ShieldCheck, Target } from "lucide-react";

import type { StrategyCatalogItem } from "../api/types";
import { Badge } from "../components/Badge";
import { Explainer, Panel } from "../components/Cards";
import { pipelineLabel } from "../utils/format";

function families(catalog: StrategyCatalogItem[]) {
  return ["All", ...Array.from(new Set(catalog.map((item) => item.family))).sort()];
}

export function StrategyLibrary({ catalog }: { catalog: StrategyCatalogItem[] }) {
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("All");
  const [selectedId, setSelectedId] = useState<string | null>(catalog[0]?.id ?? null);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return catalog.filter((item) => {
      const familyMatch = family === "All" || item.family === family;
      const textMatch =
        !normalized ||
        [item.name, item.summary, item.how_it_works, item.best_for, item.watch_out, item.pipeline]
          .join(" ")
          .toLowerCase()
          .includes(normalized);
      return familyMatch && textMatch;
    });
  }, [catalog, family, query]);

  const selected = catalog.find((item) => item.id === selectedId) ?? filtered[0] ?? catalog[0];

  return (
    <div className="page-stack">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Strategy Library</p>
          <h2>Understand the agent before you run the agent</h2>
          <span>
            The library explains each method in operational language: what it does, why it might work, what can go wrong,
            which parameters matter, and how to launch it from the command line or paper cockpit.
          </span>
        </div>
        <Badge label={`${catalog.length} strategies`} tone="info" />
      </section>

      <div className="library-layout">
        <Panel title="Browse Strategies" subtitle="Filter by family or search text">
          <div className="library-search">
            <Search size={17} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search trend, stat-arb, event, RSI..." />
          </div>
          <div className="toggle-row">
            {families(catalog).map((item) => (
              <button key={item} type="button" className={family === item ? "pill pill--active" : "pill"} onClick={() => setFamily(item)}>
                {item}
              </button>
            ))}
          </div>

          <div className="strategy-list">
            {filtered.map((item) => (
              <button
                key={item.id}
                type="button"
                className={selected?.id === item.id ? "strategy-list-item strategy-list-item--active" : "strategy-list-item"}
                onClick={() => setSelectedId(item.id)}
              >
                <strong>{item.name}</strong>
                <span>{item.summary}</span>
                <small>{item.family} | {item.difficulty}</small>
              </button>
            ))}
          </div>
        </Panel>

        <Panel title={selected?.name ?? "No strategy selected"} subtitle={selected ? pipelineLabel(selected.pipeline) : undefined}>
          {selected ? (
            <div className="strategy-detail-panel">
              <div className="badge-row">
                <Badge label={selected.family} tone="info" />
                <Badge label={selected.difficulty} tone={selected.difficulty === "Advanced" ? "warn" : "neutral"} />
              </div>

              <section className="detail-block">
                <BookOpen size={18} />
                <div>
                  <strong>How it works</strong>
                  <p>{selected.how_it_works}</p>
                </div>
              </section>

              <section className="detail-block">
                <Target size={18} />
                <div>
                  <strong>Best for</strong>
                  <p>{selected.best_for}</p>
                </div>
              </section>

              <section className="detail-block">
                <ShieldCheck size={18} />
                <div>
                  <strong>Watch out</strong>
                  <p>{selected.watch_out}</p>
                </div>
              </section>

              <div className="parameter-chips">
                {selected.key_parameters.map((parameter) => (
                  <span key={parameter}>{parameter}</span>
                ))}
              </div>

              <div className="code-card">
                <div>
                  <strong>CLI example</strong>
                  <Copy size={15} />
                </div>
                <pre>{selected.example_cli}</pre>
              </div>

              <div className="code-card">
                <div>
                  <strong>Paper config example</strong>
                  <Copy size={15} />
                </div>
                <pre>{JSON.stringify(selected.paper_config_example, null, 2)}</pre>
              </div>
            </div>
          ) : (
            <Explainer title="No catalog loaded" body="Start the backend and refresh. The strategy catalog is served by /api/strategies/catalog." />
          )}
        </Panel>
      </div>

      <section className="explain-grid">
        <Explainer title="Why this library exists" body="Professional quant platforms make method behavior visible so operators do not deploy black boxes by accident." />
        <Explainer title="Parameters are risk controls" body="Windows, z-score thresholds, rebalance cadence, and max weights change turnover, drawdown, and execution sensitivity." />
        <Explainer title="Start simple" body="ETF trend and volatility-target trend are easier to operationalize than stat-arb or event trading. Use complexity only when it pays for itself." />
      </section>
    </div>
  );
}
