import { useEffect, useState } from "react";
import { AlertTriangle, BarChart3, BrainCircuit, TrendingUp } from "lucide-react";

import { listCommitteeDecisions, getDecisionsSummary } from "../api/client";
import type { CommitteeDecision } from "../api/types";
import { Badge } from "../components/Badge";
import { MetricCard, Panel } from "../components/Cards";
import { formatDateTime, formatNumber, statusTone } from "../utils/format";

function decisionTone(decision: string) {
  if (decision === "BUY") return "good" as const;
  if (decision === "SELL" || decision === "AVOID") return "bad" as const;
  if (decision === "HOLD") return "info" as const;
  return "neutral" as const;
}

function metricValue(value: unknown) {
  if (typeof value === "number") return formatNumber(value, 4);
  if (typeof value === "string") return value;
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}

interface DecisionHistoryPanelProps {
  tickerFilter?: string;
  compact?: boolean;
}

export function DecisionHistoryPanel({ tickerFilter, compact }: DecisionHistoryPanelProps) {
  const [decisions, setDecisions] = useState<CommitteeDecision[]>([]);
  const [summary, setSummary] = useState<{ total_decisions: number; unique_tickers: number; decision_breakdown: Record<string, number>; average_confidence: number } | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      listCommitteeDecisions(tickerFilter, compact ? 20 : 100),
      tickerFilter ? Promise.resolve(null) : getDecisionsSummary(),
    ])
      .then(([d, s]) => {
        setDecisions(d);
        setSummary(s);
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Failed to load decisions."))
      .finally(() => setLoading(false));
  }, [tickerFilter, compact]);

  if (loading) return <Panel title="Decision History"><div className="empty-state">Loading decisions...</div></Panel>;
  if (error) return <Panel title="Decision History"><div className="inline-error"><AlertTriangle size={16} />{error}</div></Panel>;

  return (
    <Panel title="AI Committee Decision History" subtitle="Past recommendations, reasoning, and confidence scores">
      {summary && !compact ? (
        <div className="metric-grid metric-grid--small">
          <MetricCard label="Total Decisions" value={formatNumber(summary.total_decisions, 0)} icon={<BrainCircuit size={16} />} />
          <MetricCard label="Unique Tickers" value={formatNumber(summary.unique_tickers, 0)} icon={<TrendingUp size={16} />} />
          <MetricCard label="Avg Confidence" value={`${formatNumber(summary.average_confidence, 1)}%`} icon={<BarChart3 size={16} />} />
          {Object.entries(summary.decision_breakdown).slice(0, 4).map(([decision, count]) => (
            <MetricCard key={decision} label={decision} value={formatNumber(count, 0)} tone={decisionTone(decision)} />
          ))}
        </div>
      ) : null}

      <div className="decision-list">
        {decisions.length === 0 ? (
          <div className="empty-state">No committee decisions recorded yet. Run research to generate decisions.</div>
        ) : (
          decisions.map((d) => (
            <div key={d.id} className={`decision-row ${expanded === d.id ? "decision-row--expanded" : ""}`}>
              <button type="button" className="decision-row-header" onClick={() => setExpanded(expanded === d.id ? null : d.id)}>
                <div className="decision-row-info">
                  <strong>{d.ticker}</strong>
                  {d.pair_ticker ? <span className="decision-pair">pair {d.pair_ticker}</span> : null}
                  <span className="decision-date">{formatDateTime(d.timestamp)}</span>
                </div>
                <div className="decision-row-meta">
                  <Badge label={d.decision} tone={decisionTone(d.decision)} />
                  <span className="decision-confidence">{d.confidence}/100</span>
                </div>
              </button>
              {expanded === d.id ? (
                <div className="decision-details">
                  <div className="decision-detail-section">
                    <h4>Reasoning</h4>
                    <p>{d.reasoning}</p>
                  </div>
                  {d.market_metrics && Object.keys(d.market_metrics).length > 0 ? (
                    <div className="decision-detail-section">
                      <h4>Market Metrics</h4>
                      <div className="decision-metrics">
                        {Object.entries(d.market_metrics).slice(0, 8).map(([key, value]) => (
                          <span key={key} className="metric-tag">
                            <strong>{key.replace(/_/g, " ")}</strong>
                            <span>{metricValue(value)}</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {d.evaluation && Object.keys(d.evaluation).length > 0 ? (
                    <div className="decision-detail-section">
                      <h4>Evaluation</h4>
                      <div className="decision-metrics">
                        {Object.entries(d.evaluation).slice(0, 6).map(([key, value]) => (
                          <span key={key} className="metric-tag">
                            <strong>{key.replace(/_/g, " ")}</strong>
                            <span>{metricValue(value)}</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  {d.data_quality && typeof d.data_quality === "object" && "warnings" in d.data_quality ? (
                    <div className="decision-detail-section">
                      <h4>Data Quality</h4>
                      <p>Score: {(d.data_quality as { overall_quality_score?: number }).overall_quality_score?.toFixed(3) ?? "n/a"}</p>
                    </div>
                  ) : null}
                  <div className="decision-detail-section">
                    <h4>LLM Provider</h4>
                    <p>{d.llm_provider || "mock"} / {d.llm_model || "mock-research-v1"}</p>
                  </div>
                </div>
              ) : null}
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}
