import { AlertTriangle, BarChart3, Landmark, ReceiptText, Scale } from "lucide-react";

import type { PaperDashboardPayload, PaperStrategy } from "../api/types";
import { Badge } from "../components/Badge";
import { ExposureBars, OrderNotionalBars, RiskReturnMap, StrategyConcentrationBars } from "../components/Charts";
import { Explainer, MetricCard, Panel } from "../components/Cards";
import { DataTable } from "../components/Table";
import { formatCurrency, formatNumber, formatPercent, pipelineLabel, toneFromNumber } from "../utils/format";
import { getAllOrders, orderNotional } from "../utils/quant";
import { useState } from "react";

export function RiskMonitor({ payload }: { payload: PaperDashboardPayload }) {
  const [selectedStrategy, setSelectedStrategy] = useState<PaperStrategy | null>(payload.strategies[0] ?? null);
  const orders = getAllOrders(payload.strategies);

  return (
    <div className="page-stack">
      <section className="hero-panel">
        <div>
          <p className="eyebrow">Risk Monitor</p>
          <h2>Know where the fake money is exposed before trusting any agent</h2>
          <span>
            This page separates capital, exposure, order notional, concentration, and diagnostics so you can see whether
            a strategy is making money responsibly or simply taking more risk.
          </span>
        </div>
        <Badge label="risk first" tone="info" />
      </section>

      <section className="metric-grid">
        <MetricCard label="Gross Exposure" value={formatCurrency(payload.totals.gross_exposure)} detail={formatPercent(payload.totals.gross_exposure_ratio)} icon={<Scale size={17} />} />
        <MetricCard label="Turnover" value={formatCurrency(payload.totals.turnover)} detail="Latest simulated rebalance" icon={<ReceiptText size={17} />} />
        <MetricCard label="Cash" value={formatCurrency(payload.totals.cash)} detail="Cash after rebalance" icon={<Landmark size={17} />} />
        <MetricCard label="Daily PnL" value={formatCurrency(payload.totals.daily_pnl)} tone={toneFromNumber(payload.totals.daily_pnl)} icon={<BarChart3 size={17} />} />
      </section>

      <div className="content-grid content-grid--wide">
        <Panel title="Risk / Return Map" subtitle="Exposure, PnL, and trade count">
          <RiskReturnMap strategies={payload.strategies} />
        </Panel>
        <Panel title="Gross Exposure By Sleeve" subtitle="Higher is not automatically better">
          <ExposureBars strategies={payload.strategies} />
        </Panel>
      </div>

      <div className="content-grid">
        <Panel title="Order Notional" subtitle="Latest simulated trades">
          <OrderNotionalBars orders={orders} />
        </Panel>
        <Panel title="Concentration Inspector" subtitle={selectedStrategy?.name ?? "No strategy selected"}>
          <div className="strategy-selector">
            {payload.strategies.map((strategy) => (
              <button
                key={strategy.name}
                type="button"
                className={selectedStrategy?.name === strategy.name ? "pill pill--active" : "pill"}
                onClick={() => setSelectedStrategy(strategy)}
              >
                {strategy.name}
              </button>
            ))}
          </div>
          <StrategyConcentrationBars strategy={selectedStrategy} />
        </Panel>
      </div>

      <Panel title="Sleeve Risk Table" subtitle="Current ledger state">
        <DataTable
          rows={payload.strategies}
          empty="No paper strategies available."
          getKey={(row) => row.name}
          columns={[
            {
              key: "strategy",
              header: "Strategy",
              render: (row) => (
                <div className="stacked-cell">
                  <strong>{row.name}</strong>
                  <span>{pipelineLabel(row.pipeline)} | {row.mode}</span>
                </div>
              )
            },
            { key: "equity", header: "Equity", align: "right", render: (row) => formatCurrency(row.equity) },
            { key: "gross", header: "Gross", align: "right", render: (row) => formatPercent(row.gross_exposure_ratio) },
            { key: "cash", header: "Cash", align: "right", render: (row) => formatCurrency(row.cash) },
            { key: "positions", header: "Positions", align: "right", render: (row) => formatNumber(row.position_count, 0) },
            { key: "trades", header: "Trades", align: "right", render: (row) => formatNumber(row.trade_count, 0) },
            {
              key: "pnl",
              header: "Daily PnL",
              align: "right",
              render: (row) => <span className={`number number--${toneFromNumber(row.daily_pnl)}`}>{formatCurrency(row.daily_pnl)}</span>
            }
          ]}
        />
      </Panel>

      <Panel title="Latest Simulated Orders" subtitle={`${orders.length} orders`}>
        <DataTable
          rows={orders}
          empty="No simulated orders were generated in the latest run."
          getKey={(row, index) => `${row.strategy}-${row.instrument}-${index}`}
          columns={[
            { key: "strategy", header: "Strategy", render: (row) => row.strategy },
            { key: "instrument", header: "Instrument", render: (row) => row.instrument ?? "-" },
            { key: "side", header: "Side", render: (row) => <Badge label={String(row.side ?? "unknown")} tone={row.side === "buy" ? "good" : "bad"} /> },
            { key: "quantity", header: "Quantity", align: "right", render: (row) => formatNumber(row.quantity ?? 0, 3) },
            { key: "price", header: "Exec Price", align: "right", render: (row) => formatCurrency(row.execution_price ?? row.mark_price) },
            { key: "notional", header: "Notional", align: "right", render: (row) => formatCurrency(orderNotional(row)) }
          ]}
        />
      </Panel>

      <section className="explain-grid">
        <Explainer title="Gross exposure" body="Gross exposure is the sum of absolute position values. Long-short books can have high gross exposure even if net market direction is small." />
        <Explainer title="Synthetic stat-arb mode" body="Stat-arb paper mode can track synthetic residual components before live broker leg routing exists. Treat it as research shadow PnL." />
        <Explainer title="Risk smell test" body="If PnL improves only when exposure or turnover explodes, the strategy may be fragile after real costs and slippage." />
      </section>

      {payload.totals.gross_exposure_ratio > 1.5 ? (
        <section className="alert-card">
          <AlertTriangle size={18} />
          <div>
            <strong>High gross exposure</strong>
            <span>The combined paper book is above 150% gross exposure. Review leverage, synthetic components, and execution assumptions.</span>
          </div>
        </section>
      ) : null}
    </div>
  );
}
