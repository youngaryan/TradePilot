from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
from pathlib import Path
import re
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..engines.backtesting import json_ready


FIELD_GLOSSARY: dict[str, tuple[str, str]] = {
    "equity_before": (
        "Equity Before Rebalance",
        "Fake-money account value after marking existing positions to the latest prices, but before placing the new simulated trades.",
    ),
    "daily_pnl": (
        "Market Move",
        "The mark-to-market gain or loss since the prior saved snapshot. This tells you what the open book earned before today's rebalance.",
    ),
    "rebalance_cost_pnl": (
        "Rebalance Cost",
        "The immediate change created by today's simulated trading. It mainly reflects slippage and commissions and is usually negative.",
    ),
    "equity_after": (
        "Ending Equity",
        "The fake-money account value after today's simulated rebalance is complete.",
    ),
    "cash_after": (
        "Cash After Rebalance",
        "Uninvested cash left in the sleeve after trades. Negative cash means the sleeve is effectively financing part of the book.",
    ),
    "gross_exposure_notional": (
        "Gross Exposure",
        "Sum of absolute market value across all open positions. It measures how much capital is deployed without netting longs and shorts.",
    ),
    "gross_exposure_ratio": (
        "Gross Exposure Ratio",
        "Gross exposure divided by ending equity. A value above 1.0 means leverage or long-short offsetting exposure is in use.",
    ),
    "turnover_notional": (
        "Turnover",
        "Absolute traded notional during the latest simulated rebalance.",
    ),
    "trade_count": (
        "Trade Count",
        "How many simulated orders were placed on that run.",
    ),
    "positions": (
        "Positions",
        "The open fake-money holdings after the rebalance. These are the positions that will move with the market until the next run.",
    ),
    "target_weights": (
        "Target Weights",
        "What the strategy wanted the book to hold after the rebalance, expressed as portfolio weights.",
    ),
}

MODE_EXPLANATIONS = {
    "asset": "This sleeve trades underlying symbols directly in the fake-money ledger, so the book maps to tradable assets.",
    "synthetic": "This sleeve is still a synthetic shadow book. It tracks residual or component PnL faithfully, but it is not yet routed as live broker legs.",
}

PIPELINE_EXPLANATIONS = {
    "etf_trend": "ETF trend and momentum rotation. The sleeve ranks liquid ETFs and holds the strongest trend candidates.",
    "stat_arb": "Sector-neutral statistical arbitrage. The current implementation combines a residual book with classic pairs as a sub-sleeve.",
    "edgar_event": "EDGAR-driven event sleeve. It reacts to filing-style signals and can remain flat when no valid events are present.",
    "ma_cross": "Directional moving-average crossover strategy.",
    "rsi_mean_reversion": "Directional RSI mean-reversion strategy.",
    "donchian_breakout": "Directional Donchian breakout trend strategy.",
}


@dataclass
class PaperStrategyView:
    name: str
    pipeline: str
    mode: str
    summary: dict[str, Any]
    state: dict[str, Any]
    history: pd.DataFrame
    positions: pd.DataFrame
    target_weights: pd.DataFrame
    latest_orders: pd.DataFrame
    diagnostics: dict[str, Any]
    page_name: str


class PaperDashboardVisualizer:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.charts_dir = self.output_dir / "charts"
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self.colors = {
            "ink": "#10233f",
            "blue": "#1260cc",
            "teal": "#0f766e",
            "green": "#1f8a47",
            "gold": "#b7791f",
            "orange": "#d97706",
            "red": "#c2410c",
            "berry": "#9f1239",
            "muted": "#526581",
            "line": "#d7e0ec",
            "panel": "#ffffff",
            "surface": "#f4f7fb",
            "sky": "#eaf3ff",
        }

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "item"

    @staticmethod
    def _read_json(path: Path) -> Any:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        numeric = pd.to_numeric(value, errors="coerce")
        if pd.isna(numeric):
            return default
        return float(numeric)

    def _save_figure(self, fig: plt.Figure, filename: str) -> Path:
        path = self.charts_dir / filename
        fig.tight_layout()
        fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def _apply_axis_style(self, ax: plt.Axes, *, ylabel: str | None = None) -> None:
        ax.set_facecolor("#ffffff")
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color(self.colors["line"])
        ax.spines["bottom"].set_color(self.colors["line"])
        ax.grid(True, axis="y", color="#e6edf5", linewidth=0.9, alpha=0.9)
        ax.tick_params(colors=self.colors["muted"], labelsize=9)
        if ylabel:
            ax.set_ylabel(ylabel, color=self.colors["muted"], fontsize=10)

    def _draw_placeholder(self, ax: plt.Axes, *, title: str, body: str) -> None:
        ax.set_facecolor("#ffffff")
        ax.axis("off")
        ax.text(0.02, 0.62, title, transform=ax.transAxes, color=self.colors["ink"], fontsize=13, fontweight="bold")
        ax.text(0.02, 0.40, body, transform=ax.transAxes, color=self.colors["muted"], fontsize=10, wrap=True)

    def _format_label(self, value: str) -> str:
        return value.replace("_", " ").replace("/", " / ").title()

    def _format_value(self, key: str, value: Any) -> str:
        if value is None or value == "":
            return "N/A"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (np.integer, int)):
            return f"{int(value):,}"
        if isinstance(value, (np.floating, float)):
            money_like = any(token in key for token in ("equity", "cash", "pnl", "notional", "price", "commission", "market_value"))
            ratio_like = any(token in key for token in ("return", "ratio", "weight"))
            if money_like:
                return f"${float(value):,.2f}"
            if ratio_like:
                return f"{float(value):.2%}"
            return f"{float(value):.3f}"
        if isinstance(value, pd.Timestamp):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, (list, tuple)):
            if not value:
                return "[]"
            if len(value) > 8:
                return ", ".join(str(item) for item in value[:8]) + f", +{len(value) - 8} more"
            return ", ".join(str(item) for item in value)
        if isinstance(value, dict):
            return json.dumps(json_ready(value), indent=2)
        return str(value)

    def _metric_card(self, label: str, value: str, caption: str) -> str:
        return (
            "<article class='metric-card'>"
            f"<p class='metric-label'>{escape(label)}</p>"
            f"<p class='metric-value'>{escape(value)}</p>"
            f"<p class='metric-caption'>{escape(caption)}</p>"
            "</article>"
        )

    def _render_table(self, frame: pd.DataFrame, *, max_rows: int = 14) -> str:
        if frame.empty:
            return "<p class='empty-state'>No rows to show yet.</p>"

        preview = frame.head(max_rows).replace({np.nan: ""}).copy()
        header_html = "".join(f"<th>{escape(self._format_label(str(column)))}</th>" for column in preview.columns)
        body_rows: list[str] = []
        for _, row in preview.iterrows():
            cells = "".join(
                f"<td>{escape(self._format_value(str(column), value))}</td>"
                for column, value in row.items()
            )
            body_rows.append(f"<tr>{cells}</tr>")

        table = (
            "<div class='table-shell'>"
            "<table class='data-table'>"
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table>"
            "</div>"
        )
        if len(frame) > max_rows:
            table += f"<p class='table-note'>Showing the first {max_rows} of {len(frame)} rows.</p>"
        return table

    def _render_key_value(self, mapping: dict[str, Any]) -> str:
        if not mapping:
            return "<p class='empty-state'>No details to show yet.</p>"

        rows = "".join(
            "<tr>"
            f"<th>{escape(self._format_label(str(key)))}</th>"
            f"<td>{escape(self._format_value(str(key), value))}</td>"
            "</tr>"
            for key, value in mapping.items()
        )
        return (
            "<div class='table-shell'>"
            "<table class='data-table compact'>"
            f"<tbody>{rows}</tbody>"
            "</table>"
            "</div>"
        )

    def _json_block(self, payload: Any) -> str:
        text = json.dumps(json_ready(payload), indent=2)
        return f"<pre>{escape(text)}</pre>"

    def _chart_card(self, title: str, body: str, path: Path) -> str:
        relative = path.relative_to(self.output_dir).as_posix()
        return (
            "<article class='chart-card'>"
            "<div class='chart-header'>"
            f"<h3>{escape(title)}</h3>"
            f"<p>{escape(body)}</p>"
            "</div>"
            f"<img src='{escape(relative)}' alt='{escape(title)}' />"
            "</article>"
        )

    def _tab_group(self, group_id: str, tabs: list[tuple[str, str, str]]) -> str:
        buttons = []
        panels = []
        for index, (tab_id, label, html) in enumerate(tabs):
            active_attr = "true" if index == 0 else "false"
            selected_class = " is-active" if index == 0 else ""
            hidden = "" if index == 0 else " hidden"
            buttons.append(
                f"<button type='button' class='tab-button{selected_class}' data-tab-target='{escape(tab_id)}' aria-selected='{active_attr}'>{escape(label)}</button>"
            )
            panels.append(
                f"<section class='tab-panel' data-tab-panel='{escape(tab_id)}'{hidden}>{html}</section>"
            )

        return (
            f"<div class='tab-group' data-tab-group='{escape(group_id)}'>"
            f"<div class='tab-list'>{''.join(buttons)}</div>"
            f"<div class='tab-panels'>{''.join(panels)}</div>"
            "</div>"
        )

    def _build_history_frame(self, state: dict[str, Any], summary: dict[str, Any]) -> pd.DataFrame:
        rows = list(state.get("history", []))
        if not rows and summary:
            rows = [summary]
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame

        frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
        frame = frame.dropna(subset=["timestamp"]).sort_values("timestamp")
        frame = frame.drop_duplicates(subset=["timestamp"], keep="last").set_index("timestamp")
        for column in (
            "equity_before",
            "equity_after",
            "daily_pnl",
            "rebalance_cost_pnl",
            "net_return_since_inception",
            "cash_after",
            "gross_exposure_notional",
            "gross_exposure_ratio",
            "position_count",
            "trade_count",
            "turnover_notional",
        ):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.sort_index()

    def _build_positions_frame(self, state: dict[str, Any], summary: dict[str, Any]) -> pd.DataFrame:
        positions = dict(state.get("positions", {}) or summary.get("positions", {}))
        prices = dict(state.get("instrument_prices", {}))
        equity = max(self._safe_float(summary.get("equity_after"), self._safe_float(state.get("initial_cash"))), 1e-9)
        rows: list[dict[str, Any]] = []
        for instrument, quantity in positions.items():
            price = self._safe_float(prices.get(instrument), np.nan)
            market_value = self._safe_float(quantity) * price if pd.notna(price) else np.nan
            rows.append(
                {
                    "instrument": instrument,
                    "quantity": self._safe_float(quantity),
                    "price": price,
                    "market_value": market_value,
                    "actual_weight": market_value / equity if pd.notna(market_value) else np.nan,
                }
            )
        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        return frame.sort_values("market_value", key=lambda series: series.abs(), ascending=False).reset_index(drop=True)

    def _build_weights_frame(self, summary: dict[str, Any]) -> pd.DataFrame:
        weights = dict(summary.get("target_weights", {}))
        frame = pd.DataFrame(
            [{"instrument": instrument, "target_weight": self._safe_float(weight)} for instrument, weight in weights.items()]
        )
        if frame.empty:
            return frame
        return frame.sort_values("target_weight", key=lambda series: series.abs(), ascending=False).reset_index(drop=True)

    def _build_orders_frame(self, orders_payload: Any) -> pd.DataFrame:
        frame = pd.DataFrame(orders_payload or [])
        if frame.empty:
            return frame
        for column in ("quantity", "mark_price", "execution_price", "target_weight", "commission", "notional"):
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame.sort_values("notional", key=lambda series: series.abs(), ascending=False).reset_index(drop=True)

    def _load_strategy_views(
        self,
        batch_summary: dict[str, Any],
        state_dir: Path,
        *,
        allow_legacy: bool = False,
    ) -> dict[str, PaperStrategyView]:
        from ..operations.paper_state import ledger_key

        views: dict[str, PaperStrategyView] = {}
        for name, summary in batch_summary.get("strategies", {}).items():
            key = str(summary.get("ledger_key") or ledger_key(name))
            state = self._read_json(state_dir / f"{key}.json") or {}
            if not state and allow_legacy:
                state = self._read_json(state_dir / f"{name}.json") or {}
            latest_orders = state.get("latest_orders") if isinstance(state.get("latest_orders"), list) else None
            if latest_orders is None and allow_legacy:
                projection_key = key if (state_dir / f"{key}_latest_orders.json").exists() else name
                latest_orders = self._read_json(state_dir / f"{projection_key}_latest_orders.json") or []
            latest_orders = latest_orders or []
            pipeline = str(summary.get("metadata", {}).get("pipeline", "unknown"))
            mode = str(summary.get("mode", state.get("mode", "unknown")))
            views[name] = PaperStrategyView(
                name=name,
                pipeline=pipeline,
                mode=mode,
                summary=summary,
                state=state,
                history=self._build_history_frame(state, summary),
                positions=self._build_positions_frame(state, summary),
                target_weights=self._build_weights_frame(summary),
                latest_orders=self._build_orders_frame(latest_orders),
                diagnostics=summary.get("diagnostics", {}),
                page_name=f"strategy_{self._slug(name)}.html",
            )
        return views

    def _aggregate_history(self, strategy_views: dict[str, PaperStrategyView]) -> pd.DataFrame:
        index_values: set[pd.Timestamp] = set()
        for view in strategy_views.values():
            index_values.update(pd.DatetimeIndex(view.history.index).tolist())
        if not index_values:
            return pd.DataFrame()

        index = pd.DatetimeIndex(sorted(index_values))
        aggregate = pd.DataFrame(index=index)
        total_initial_cash = 0.0

        for name, view in strategy_views.items():
            slug = self._slug(name)
            initial_cash = self._safe_float(view.state.get("initial_cash"), self._safe_float(view.summary.get("equity_after")))
            total_initial_cash += initial_cash
            history = view.history

            if history.empty:
                aggregate[f"equity__{slug}"] = initial_cash
                aggregate[f"cash__{slug}"] = initial_cash
                aggregate[f"gross__{slug}"] = 0.0
                aggregate[f"positions__{slug}"] = 0.0
                aggregate[f"daily_pnl__{slug}"] = 0.0
                aggregate[f"rebalance__{slug}"] = 0.0
                aggregate[f"turnover__{slug}"] = 0.0
                aggregate[f"trades__{slug}"] = 0.0
                continue

            equity = history["equity_after"].reindex(index).ffill().fillna(initial_cash)
            cash = history.get("cash_after", pd.Series(index=history.index, dtype=float)).reindex(index).ffill().fillna(initial_cash)
            gross = history.get("gross_exposure_notional", pd.Series(index=history.index, dtype=float)).reindex(index).ffill().fillna(0.0)
            positions = history.get("position_count", pd.Series(index=history.index, dtype=float)).reindex(index).ffill().fillna(0.0)
            daily_pnl = history.get("daily_pnl", pd.Series(index=history.index, dtype=float)).reindex(index).fillna(0.0)
            rebalance = history.get("rebalance_cost_pnl", pd.Series(index=history.index, dtype=float)).reindex(index).fillna(0.0)
            turnover = history.get("turnover_notional", pd.Series(index=history.index, dtype=float)).reindex(index).fillna(0.0)
            trades = history.get("trade_count", pd.Series(index=history.index, dtype=float)).reindex(index).fillna(0.0)

            aggregate[f"equity__{slug}"] = equity
            aggregate[f"cash__{slug}"] = cash
            aggregate[f"gross__{slug}"] = gross
            aggregate[f"positions__{slug}"] = positions
            aggregate[f"daily_pnl__{slug}"] = daily_pnl
            aggregate[f"rebalance__{slug}"] = rebalance
            aggregate[f"turnover__{slug}"] = turnover
            aggregate[f"trades__{slug}"] = trades

        aggregate["total_equity"] = aggregate.filter(like="equity__").sum(axis=1)
        aggregate["total_cash"] = aggregate.filter(like="cash__").sum(axis=1)
        aggregate["total_gross_exposure"] = aggregate.filter(like="gross__").sum(axis=1)
        aggregate["total_positions"] = aggregate.filter(like="positions__").sum(axis=1)
        aggregate["total_daily_pnl"] = aggregate.filter(like="daily_pnl__").sum(axis=1)
        aggregate["total_rebalance_cost"] = aggregate.filter(like="rebalance__").sum(axis=1)
        aggregate["total_turnover"] = aggregate.filter(like="turnover__").sum(axis=1)
        aggregate["total_trade_count"] = aggregate.filter(like="trades__").sum(axis=1)
        aggregate["total_gross_ratio"] = np.where(
            aggregate["total_equity"].abs() > 1e-9,
            aggregate["total_gross_exposure"] / aggregate["total_equity"],
            0.0,
        )
        aggregate["total_net_change"] = aggregate["total_equity"].diff().fillna(aggregate["total_equity"] - total_initial_cash)
        aggregate.attrs["total_initial_cash"] = total_initial_cash
        return aggregate

    def _plot_combined_equity(self, aggregate: pd.DataFrame, strategy_views: dict[str, PaperStrategyView]) -> Path:
        fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, height_ratios=[2.4, 1.6])
        fig.patch.set_facecolor(self.colors["surface"])

        if aggregate.empty:
            self._draw_placeholder(
                axes[0],
                title="No paper history yet",
                body="Run a paper batch first. Once the ledgers have at least one saved snapshot, this chart will show total fake-money equity.",
            )
            axes[1].axis("off")
            return self._save_figure(fig, "combined_equity.png")

        axes[0].plot(aggregate.index, aggregate["total_equity"], color=self.colors["blue"], linewidth=2.8, label="Combined Equity")
        axes[0].fill_between(aggregate.index, aggregate["total_equity"], aggregate.attrs.get("total_initial_cash", aggregate["total_equity"].iloc[0]), color="#cfe3ff", alpha=0.28)
        axes[0].axhline(aggregate.attrs.get("total_initial_cash", aggregate["total_equity"].iloc[0]), color="#9fb3c9", linestyle="--", linewidth=1.0)
        axes[0].set_title("Combined Fake-Money Equity", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Equity ($)")

        for index, view in enumerate(strategy_views.values()):
            slug = self._slug(view.name)
            column = f"equity__{slug}"
            if column not in aggregate.columns:
                continue
            initial_cash = self._safe_float(view.state.get("initial_cash"), max(aggregate[column].iloc[0], 1.0))
            normalized = aggregate[column] / max(initial_cash, 1e-9)
            axes[1].plot(aggregate.index, normalized, linewidth=1.8, label=view.name)
        axes[1].axhline(1.0, color="#9fb3c9", linestyle="--", linewidth=1.0)
        axes[1].set_title("Sleeve Indices", loc="left", fontsize=13, color=self.colors["ink"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Index")
        axes[1].legend(frameon=False, loc="upper left", ncol=2)
        return self._save_figure(fig, "combined_equity.png")

    def _plot_leaderboard(self, batch_summary: dict[str, Any]) -> Path:
        fig, ax = plt.subplots(figsize=(13, 5.8))
        fig.patch.set_facecolor(self.colors["surface"])

        leaderboard = pd.DataFrame(batch_summary.get("leaderboard", []))
        if leaderboard.empty:
            self._draw_placeholder(ax, title="No leaderboard yet", body="A leaderboard appears after the first paper batch completes.")
            return self._save_figure(fig, "leaderboard.png")

        leaderboard = leaderboard.sort_values("net_return_since_inception", ascending=True)
        colors = [self.colors["green"] if value >= 0.0 else self.colors["red"] for value in leaderboard["net_return_since_inception"]]
        ax.barh(leaderboard["strategy"], leaderboard["net_return_since_inception"] * 100.0, color=colors, alpha=0.92)
        ax.axvline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        for y_pos, (_, row) in enumerate(leaderboard.iterrows()):
            ax.text(
                row["net_return_since_inception"] * 100.0,
                y_pos,
                f"  {row['equity_after']:,.0f}",
                va="center",
                color=self.colors["ink"],
                fontsize=9,
            )
        ax.set_title("Current Sleeve Leaderboard", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
        self._apply_axis_style(ax, ylabel=None)
        ax.set_xlabel("Return Since Inception (%)", color=self.colors["muted"], fontsize=10)
        return self._save_figure(fig, "leaderboard.png")

    def _plot_capital_timeline(self, aggregate: pd.DataFrame) -> Path:
        fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, height_ratios=[1.6, 1.8])
        fig.patch.set_facecolor(self.colors["surface"])

        if aggregate.empty:
            self._draw_placeholder(axes[0], title="No capital timeline yet", body="Run a paper batch to populate the cash, exposure, and turnover timeline.")
            axes[1].axis("off")
            return self._save_figure(fig, "capital_timeline.png")

        axes[0].plot(aggregate.index, aggregate["total_cash"], color=self.colors["teal"], linewidth=2.2, label="Cash")
        axes[0].plot(aggregate.index, aggregate["total_gross_exposure"], color=self.colors["gold"], linewidth=2.0, label="Gross Exposure")
        axes[0].set_title("Cash And Gross Exposure", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Dollars")
        axes[0].legend(frameon=False, loc="upper left")

        axes[1].bar(aggregate.index, aggregate["total_turnover"], color="#bfdbfe", label="Turnover")
        turnover_rolling = aggregate["total_turnover"].rolling(5, min_periods=1).mean()
        axes[1].plot(aggregate.index, turnover_rolling, color=self.colors["blue"], linewidth=2.0, label="5-Run Mean Turnover")
        trades_axis = axes[1].twinx()
        trades_axis.plot(aggregate.index, aggregate["total_trade_count"], color=self.colors["red"], linewidth=1.6, linestyle="--", label="Trade Count")
        trades_axis.tick_params(colors=self.colors["muted"], labelsize=9)
        trades_axis.spines["top"].set_visible(False)
        trades_axis.spines["right"].set_color(self.colors["line"])
        trades_axis.set_ylabel("Trades", color=self.colors["muted"], fontsize=10)
        axes[1].set_title("Rebalance Activity", loc="left", fontsize=13, color=self.colors["ink"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Turnover ($)")
        handles, labels = axes[1].get_legend_handles_labels()
        extra_handles, extra_labels = trades_axis.get_legend_handles_labels()
        axes[1].legend(handles + extra_handles, labels + extra_labels, frameon=False, loc="upper left", ncol=3)
        return self._save_figure(fig, "capital_timeline.png")

    def _plot_latest_money_flow(self, batch_summary: dict[str, Any]) -> Path:
        fig, axes = plt.subplots(2, 1, figsize=(13.5, 8), height_ratios=[1.2, 1.8])
        fig.patch.set_facecolor(self.colors["surface"])

        rows = []
        for name, summary in batch_summary.get("strategies", {}).items():
            rows.append(
                {
                    "strategy": name,
                    "daily_pnl": self._safe_float(summary.get("daily_pnl")),
                    "rebalance_cost_pnl": self._safe_float(summary.get("rebalance_cost_pnl")),
                    "equity_after": self._safe_float(summary.get("equity_after")),
                    "starting_equity": self._safe_float(summary.get("equity_before")) - self._safe_float(summary.get("daily_pnl")),
                }
            )
        latest = pd.DataFrame(rows)
        if latest.empty:
            self._draw_placeholder(axes[0], title="No money-flow view yet", body="A money-flow bridge appears after the first batch finishes.")
            axes[1].axis("off")
            return self._save_figure(fig, "latest_money_flow.png")

        y = np.arange(len(latest))
        axes[0].barh(y - 0.18, latest["daily_pnl"], height=0.34, color=np.where(latest["daily_pnl"] >= 0.0, self.colors["green"], self.colors["red"]), label="Market Move")
        axes[0].barh(y + 0.18, latest["rebalance_cost_pnl"], height=0.34, color=self.colors["orange"], label="Rebalance Cost")
        axes[0].axvline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        axes[0].set_yticks(y)
        axes[0].set_yticklabels(latest["strategy"])
        axes[0].set_title("Latest Contribution By Sleeve", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
        self._apply_axis_style(axes[0], ylabel=None)
        axes[0].set_xlabel("Dollars", color=self.colors["muted"], fontsize=10)
        axes[0].legend(frameon=False, loc="lower right")

        previous_close = latest["starting_equity"].sum()
        market_move = latest["daily_pnl"].sum()
        rebalance_cost = latest["rebalance_cost_pnl"].sum()
        ending_equity = latest["equity_after"].sum()
        bridge_labels = ["Previous Close", "Market Move", "Rebalance Cost", "Ending Equity"]
        bridge_values = [previous_close, market_move, rebalance_cost, ending_equity]
        bridge_bottoms = [0.0, previous_close, previous_close + market_move, 0.0]
        bridge_colors = [self.colors["blue"], self.colors["green"] if market_move >= 0 else self.colors["red"], self.colors["orange"], self.colors["ink"]]
        axes[1].bar(bridge_labels, bridge_values, bottom=bridge_bottoms, color=bridge_colors, alpha=0.92)
        for idx, value in enumerate(bridge_values):
            top = bridge_bottoms[idx] + value
            axes[1].text(idx, top, f"${value:,.0f}", ha="center", va="bottom", fontsize=9, color=self.colors["ink"])
        axes[1].set_title("Latest Money Bridge", loc="left", fontsize=13, color=self.colors["ink"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Dollars")
        return self._save_figure(fig, "latest_money_flow.png")

    def _plot_strategy_equity(self, view: PaperStrategyView) -> Path:
        fig, axes = plt.subplots(2, 1, figsize=(13.5, 7.5), sharex=True, height_ratios=[2.3, 1.1])
        fig.patch.set_facecolor(self.colors["surface"])

        if view.history.empty:
            self._draw_placeholder(
                axes[0],
                title=f"{view.name}: no history yet",
                body="This sleeve has not saved enough paper-ledger history to plot equity.",
            )
            axes[1].axis("off")
            return self._save_figure(fig, f"{self._slug(view.name)}_equity.png")

        equity = view.history["equity_after"].copy()
        initial_cash = self._safe_float(view.state.get("initial_cash"), equity.iloc[0])
        drawdown = equity / equity.cummax() - 1.0

        axes[0].plot(view.history.index, equity, color=self.colors["blue"], linewidth=2.6)
        axes[0].fill_between(view.history.index, equity, initial_cash, color="#dbeafe", alpha=0.26)
        axes[0].axhline(initial_cash, color="#9fb3c9", linestyle="--", linewidth=1.0)
        axes[0].set_title(f"{view.name}: Equity", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Equity ($)")

        axes[1].fill_between(view.history.index, drawdown * 100.0, 0.0, color="#fecaca", alpha=0.72)
        axes[1].plot(view.history.index, drawdown * 100.0, color=self.colors["berry"], linewidth=1.8)
        axes[1].axhline(0.0, color="#9fb3c9", linestyle="--", linewidth=1.0)
        axes[1].set_title("Drawdown", loc="left", fontsize=13, color=self.colors["ink"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Drawdown (%)")
        return self._save_figure(fig, f"{self._slug(view.name)}_equity.png")

    def _plot_strategy_activity(self, view: PaperStrategyView) -> Path:
        fig, axes = plt.subplots(2, 1, figsize=(13.5, 7.5), sharex=True, height_ratios=[1.4, 1.6])
        fig.patch.set_facecolor(self.colors["surface"])

        if view.history.empty:
            self._draw_placeholder(
                axes[0],
                title=f"{view.name}: no activity yet",
                body="This chart appears after the sleeve saves at least one rebalance.",
            )
            axes[1].axis("off")
            return self._save_figure(fig, f"{self._slug(view.name)}_activity.png")

        pnl = view.history.get("daily_pnl", pd.Series(index=view.history.index, dtype=float)).fillna(0.0)
        colors = np.where(pnl >= 0.0, self.colors["green"], self.colors["red"])
        axes[0].bar(view.history.index, pnl, color=colors, alpha=0.88)
        rolling = pnl.rolling(5, min_periods=1).mean()
        axes[0].plot(view.history.index, rolling, color=self.colors["gold"], linewidth=1.8, label="5-Run Mean")
        axes[0].axhline(0.0, color="#9fb3c9", linestyle="--", linewidth=1.0)
        axes[0].set_title("Market Move Before Rebalance", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
        self._apply_axis_style(axes[0], ylabel="PnL ($)")
        axes[0].legend(frameon=False, loc="upper left")

        turnover = view.history.get("turnover_notional", pd.Series(index=view.history.index, dtype=float)).fillna(0.0)
        trades = view.history.get("trade_count", pd.Series(index=view.history.index, dtype=float)).fillna(0.0)
        axes[1].bar(view.history.index, turnover, color="#bfdbfe", alpha=0.92, label="Turnover")
        trade_axis = axes[1].twinx()
        trade_axis.plot(view.history.index, trades, color=self.colors["orange"], linewidth=2.0, linestyle="--", label="Trade Count")
        trade_axis.tick_params(colors=self.colors["muted"], labelsize=9)
        trade_axis.spines["top"].set_visible(False)
        trade_axis.spines["right"].set_color(self.colors["line"])
        trade_axis.set_ylabel("Trades", color=self.colors["muted"], fontsize=10)
        axes[1].set_title("Rebalance Activity", loc="left", fontsize=13, color=self.colors["ink"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Turnover ($)")
        handles, labels = axes[1].get_legend_handles_labels()
        extra_handles, extra_labels = trade_axis.get_legend_handles_labels()
        axes[1].legend(handles + extra_handles, labels + extra_labels, frameon=False, loc="upper left")
        return self._save_figure(fig, f"{self._slug(view.name)}_activity.png")

    def _plot_strategy_book(self, view: PaperStrategyView) -> Path:
        fig, axes = plt.subplots(2, 1, figsize=(13.5, 8), height_ratios=[1.6, 1.4])
        fig.patch.set_facecolor(self.colors["surface"])

        positions = view.positions.head(10)
        if positions.empty:
            self._draw_placeholder(
                axes[0],
                title=f"{view.name}: no open positions",
                body="This sleeve is currently flat, so there are no live fake-money holdings to chart.",
            )
        else:
            colors = np.where(positions["market_value"] >= 0.0, self.colors["green"], self.colors["red"])
            axes[0].barh(positions["instrument"], positions["market_value"], color=colors, alpha=0.92)
            axes[0].axvline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
            axes[0].set_title("Current Position Market Values", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
            self._apply_axis_style(axes[0], ylabel=None)
            axes[0].set_xlabel("Market Value ($)", color=self.colors["muted"], fontsize=10)

        weights = view.target_weights.head(10)
        if weights.empty:
            self._draw_placeholder(
                axes[1],
                title="No target weights",
                body="This sleeve did not request any target positions on the latest run.",
            )
        else:
            colors = np.where(weights["target_weight"] >= 0.0, self.colors["blue"], self.colors["orange"])
            axes[1].barh(weights["instrument"], weights["target_weight"] * 100.0, color=colors, alpha=0.92)
            axes[1].axvline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
            axes[1].set_title("Latest Target Weights", loc="left", fontsize=13, color=self.colors["ink"], pad=8)
            self._apply_axis_style(axes[1], ylabel=None)
            axes[1].set_xlabel("Target Weight (%)", color=self.colors["muted"], fontsize=10)

        return self._save_figure(fig, f"{self._slug(view.name)}_book.png")

    def _plot_strategy_capital(self, view: PaperStrategyView) -> Path:
        fig, axes = plt.subplots(2, 1, figsize=(13.5, 7.5), sharex=True, height_ratios=[1.6, 1.2])
        fig.patch.set_facecolor(self.colors["surface"])

        if view.history.empty:
            self._draw_placeholder(
                axes[0],
                title=f"{view.name}: no capital history",
                body="This chart appears after the sleeve has at least one saved paper snapshot.",
            )
            axes[1].axis("off")
            return self._save_figure(fig, f"{self._slug(view.name)}_capital.png")

        cash = view.history.get("cash_after", pd.Series(index=view.history.index, dtype=float)).fillna(0.0)
        gross = view.history.get("gross_exposure_notional", pd.Series(index=view.history.index, dtype=float)).fillna(0.0)
        axes[0].plot(view.history.index, cash, color=self.colors["teal"], linewidth=2.2, label="Cash")
        axes[0].plot(view.history.index, gross, color=self.colors["gold"], linewidth=2.0, label="Gross Exposure")
        axes[0].set_title("Cash And Gross Exposure", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Dollars")
        axes[0].legend(frameon=False, loc="upper left")

        ratio = view.history.get("gross_exposure_ratio", pd.Series(index=view.history.index, dtype=float)).fillna(0.0)
        positions = view.history.get("position_count", pd.Series(index=view.history.index, dtype=float)).fillna(0.0)
        axes[1].plot(view.history.index, ratio, color=self.colors["blue"], linewidth=2.2, label="Gross Ratio")
        positions_axis = axes[1].twinx()
        positions_axis.bar(view.history.index, positions, color="#dbeafe", alpha=0.82, label="Positions")
        positions_axis.tick_params(colors=self.colors["muted"], labelsize=9)
        positions_axis.spines["top"].set_visible(False)
        positions_axis.spines["right"].set_color(self.colors["line"])
        positions_axis.set_ylabel("Positions", color=self.colors["muted"], fontsize=10)
        axes[1].set_title("Exposure Ratio And Position Count", loc="left", fontsize=13, color=self.colors["ink"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Gross Ratio")
        handles, labels = axes[1].get_legend_handles_labels()
        extra_handles, extra_labels = positions_axis.get_legend_handles_labels()
        axes[1].legend(handles + extra_handles, labels + extra_labels, frameon=False, loc="upper left")
        return self._save_figure(fig, f"{self._slug(view.name)}_capital.png")

    def _plot_strategy_orders(self, view: PaperStrategyView) -> Path:
        fig, ax = plt.subplots(figsize=(13.5, 5.8))
        fig.patch.set_facecolor(self.colors["surface"])

        if view.latest_orders.empty:
            self._draw_placeholder(
                ax,
                title=f"{view.name}: no fresh orders",
                body="The latest paper run did not need new trades for this sleeve, so the book remained in place.",
            )
            return self._save_figure(fig, f"{self._slug(view.name)}_orders.png")

        orders = view.latest_orders.head(12).copy()
        colors = np.where(orders.get("side", pd.Series(index=orders.index, dtype=str)).astype(str).str.lower() == "buy", self.colors["green"], self.colors["red"])
        ax.barh(orders["instrument"], orders["notional"], color=colors, alpha=0.92)
        for idx, commission in enumerate(orders.get("commission", pd.Series(0.0, index=orders.index)).fillna(0.0)):
            ax.text(float(orders["notional"].iloc[idx]), idx, f"  fee {commission:.2f}", va="center", fontsize=8.5, color=self.colors["muted"])
        ax.set_title("Latest Simulated Orders", loc="left", fontsize=16, color=self.colors["ink"], pad=10)
        self._apply_axis_style(ax, ylabel=None)
        ax.set_xlabel("Notional ($)", color=self.colors["muted"], fontsize=10)
        return self._save_figure(fig, f"{self._slug(view.name)}_orders.png")

    def _styles(self) -> str:
        return """
  <style>
    :root {
      --ink: #10233f;
      --blue: #1260cc;
      --teal: #0f766e;
      --green: #1f8a47;
      --gold: #b7791f;
      --orange: #d97706;
      --red: #c2410c;
      --berry: #9f1239;
      --muted: #526581;
      --line: #d7e0ec;
      --panel: #ffffff;
      --surface: #f4f7fb;
      --surface-2: #eaf3ff;
      --radius: 22px;
      --shadow: 0 24px 60px rgba(16, 35, 63, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Aptos", "Segoe UI Variable Text", "Trebuchet MS", sans-serif;
      background:
        radial-gradient(circle at top right, rgba(18, 96, 204, 0.10), transparent 28%),
        radial-gradient(circle at left 15%, rgba(15, 118, 110, 0.09), transparent 22%),
        var(--surface);
      color: var(--ink);
    }
    .page {
      width: min(100% - 28px, 1440px);
      margin: 28px auto 44px;
    }
    .topbar {
      position: sticky;
      top: 14px;
      z-index: 20;
      backdrop-filter: blur(10px);
      background: rgba(244, 247, 251, 0.82);
      padding: 12px;
      border: 1px solid rgba(215, 224, 236, 0.9);
      border-radius: 20px;
      box-shadow: 0 18px 40px rgba(16, 35, 63, 0.06);
      margin-bottom: 22px;
    }
    .nav-row {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }
    .nav-link {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      text-decoration: none;
      color: var(--muted);
      border: 1px solid transparent;
      background: rgba(255, 255, 255, 0.55);
      font-size: 0.94rem;
      font-weight: 700;
    }
    .nav-link:hover {
      border-color: var(--line);
      color: var(--ink);
    }
    .nav-link.active {
      color: #ffffff;
      background: linear-gradient(135deg, var(--blue), #0f4f9d);
      box-shadow: 0 12px 28px rgba(18, 96, 204, 0.24);
    }
    .hero {
      display: grid;
      grid-template-columns: minmax(0, 1.65fr) minmax(320px, 1fr);
      gap: 18px;
      margin-bottom: 22px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid rgba(215, 224, 236, 0.95);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }
    .hero-card {
      padding: 28px;
      position: relative;
      overflow: hidden;
    }
    .hero-card::after {
      content: "";
      position: absolute;
      inset: auto -42px -64px auto;
      width: 220px;
      height: 220px;
      border-radius: 999px;
      background: linear-gradient(135deg, rgba(18, 96, 204, 0.17), rgba(15, 118, 110, 0.11));
      filter: blur(8px);
    }
    .eyebrow {
      margin: 0 0 10px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      font-size: 0.72rem;
      color: var(--blue);
      font-weight: 800;
    }
    h1, h2, h3 {
      margin: 0;
      color: var(--ink);
      font-family: Georgia, "Times New Roman", serif;
    }
    h1 {
      font-size: clamp(2rem, 3vw, 3rem);
      line-height: 1.04;
      margin-bottom: 10px;
    }
    h2 {
      font-size: 1.45rem;
      margin-bottom: 10px;
    }
    h3 {
      font-size: 1.02rem;
      margin-bottom: 8px;
    }
    .lede {
      margin: 0;
      color: var(--muted);
      line-height: 1.65;
      max-width: 66ch;
      font-size: 1rem;
    }
    .hero-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      position: relative;
      z-index: 1;
    }
    .hero-kpi {
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(245,249,255,0.98));
    }
    .hero-kpi .label {
      margin: 0 0 6px;
      color: var(--muted);
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-weight: 700;
    }
    .hero-kpi .value {
      margin: 0;
      color: var(--ink);
      font-size: 1.28rem;
      font-weight: 800;
    }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
      margin-bottom: 22px;
    }
    .metric-card {
      padding: 18px 18px 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(244,247,251,1));
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 14px 36px rgba(16, 35, 63, 0.05);
    }
    .metric-label {
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    .metric-value {
      margin: 0;
      font-size: 1.45rem;
      color: var(--ink);
      font-weight: 800;
    }
    .metric-caption {
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.5;
    }
    .section {
      padding: 24px;
      margin-bottom: 22px;
    }
    .section-header {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 18px;
      margin-bottom: 16px;
    }
    .section-copy {
      margin: 0;
      color: var(--muted);
      max-width: 76ch;
      line-height: 1.6;
    }
    .chart-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
      gap: 16px;
    }
    .chart-card {
      padding: 18px;
      border-radius: 20px;
      border: 1px solid var(--line);
      background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(244,247,251,0.92));
    }
    .chart-header p {
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }
    .chart-card img {
      width: 100%;
      display: block;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: #ffffff;
      margin-top: 12px;
    }
    .strategy-card-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
    }
    .strategy-card {
      padding: 20px;
      border-radius: 20px;
      background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(244,247,251,1));
      border: 1px solid var(--line);
    }
    .strategy-card p {
      margin: 0 0 8px;
      color: var(--muted);
      line-height: 1.55;
    }
    .pill-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 12px 0 16px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      background: #eef5ff;
      color: var(--blue);
      border: 1px solid #d2e3fb;
      font-size: 0.82rem;
      font-weight: 700;
    }
    .strategy-link {
      display: inline-flex;
      padding: 10px 14px;
      border-radius: 999px;
      background: var(--ink);
      color: #ffffff;
      text-decoration: none;
      font-weight: 700;
    }
    .two-column {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }
    .tab-group {
      border: 1px solid var(--line);
      border-radius: 22px;
      overflow: hidden;
      background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(244,247,251,0.96));
    }
    .tab-list {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
      background: rgba(234, 243, 255, 0.72);
    }
    .tab-button {
      appearance: none;
      border: 1px solid transparent;
      background: rgba(255,255,255,0.9);
      color: var(--muted);
      padding: 10px 14px;
      border-radius: 999px;
      font-weight: 700;
      cursor: pointer;
    }
    .tab-button.is-active {
      background: linear-gradient(135deg, var(--blue), #0f4f9d);
      color: #ffffff;
      box-shadow: 0 12px 28px rgba(18, 96, 204, 0.22);
    }
    .tab-panels {
      padding: 18px;
    }
    .tab-panel[hidden] {
      display: none;
    }
    .table-shell {
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: #ffffff;
    }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    th, td {
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      font-size: 0.92rem;
    }
    th {
      color: var(--ink);
      background: #f8fbff;
      font-weight: 800;
    }
    td {
      color: var(--muted);
    }
    tr:last-child th, tr:last-child td {
      border-bottom: 0;
    }
    .compact th {
      width: 34%;
    }
    .table-note, .empty-state {
      margin: 10px 2px 0;
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.5;
    }
    .explain-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }
    .explain-card {
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: #ffffff;
    }
    .explain-card p {
      margin: 0;
      color: var(--muted);
      line-height: 1.55;
    }
    details {
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      background: #ffffff;
    }
    summary {
      cursor: pointer;
      padding: 14px 16px;
      font-weight: 800;
      color: var(--ink);
      background: #f8fbff;
    }
    pre {
      margin: 0;
      padding: 16px;
      overflow: auto;
      background: #10233f;
      color: #dde8f5;
      font-size: 0.86rem;
      line-height: 1.55;
    }
    .footer {
      margin-top: 26px;
      text-align: center;
      color: var(--muted);
      font-size: 0.9rem;
    }
    @media (max-width: 980px) {
      .page { width: min(100% - 20px, 1440px); margin: 20px auto 30px; }
      .hero, .two-column { grid-template-columns: 1fr; }
      .hero-grid { grid-template-columns: 1fr 1fr; }
      .chart-grid, .strategy-card-grid { grid-template-columns: 1fr; }
    }
  </style>
"""

    def _script(self) -> str:
        return """
  <script>
    document.addEventListener("click", function (event) {
      const button = event.target.closest("[data-tab-target]");
      if (!button) {
        return;
      }
      const container = button.closest("[data-tab-group]");
      if (!container) {
        return;
      }
      const target = button.getAttribute("data-tab-target");
      container.querySelectorAll("[data-tab-target]").forEach((item) => {
        const active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", active ? "true" : "false");
      });
      container.querySelectorAll("[data-tab-panel]").forEach((panel) => {
        panel.hidden = panel.getAttribute("data-tab-panel") !== target;
      });
    });
  </script>
"""

    def _navigation(self, active_page: str, strategy_views: dict[str, PaperStrategyView]) -> str:
        links = [
            ("overview", "Overview", "index.html"),
            ("capital", "Capital Flow", "capital_flow.html"),
            ("glossary", "Glossary", "glossary.html"),
        ]
        html_parts = []
        for key, label, href in links:
            active = " active" if active_page == key else ""
            html_parts.append(f"<a class='nav-link{active}' href='{escape(href)}'>{escape(label)}</a>")
        for view in strategy_views.values():
            active = " active" if active_page == view.page_name else ""
            html_parts.append(f"<a class='nav-link{active}' href='{escape(view.page_name)}'>{escape(view.name)}</a>")
        return "<nav class='topbar panel'><div class='nav-row'>" + "".join(html_parts) + "</div></nav>"

    def _page_shell(
        self,
        *,
        title: str,
        hero_title: str,
        hero_body: str,
        hero_metrics: list[tuple[str, str]],
        active_page: str,
        body_html: str,
        strategy_views: dict[str, PaperStrategyView],
        footer_note: str,
    ) -> str:
        hero_kpis = "".join(
            "<div class='hero-kpi'>"
            f"<p class='label'>{escape(label)}</p>"
            f"<p class='value'>{escape(value)}</p>"
            "</div>"
            for label, value in hero_metrics
        )
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(title)}</title>
  {self._styles()}
</head>
<body>
  <div class="page">
    {self._navigation(active_page, strategy_views)}
    <section class="hero">
      <article class="hero-card panel">
        <p class="eyebrow">Shadow Paper Dashboard</p>
        <h1>{escape(hero_title)}</h1>
        <p class="lede">{escape(hero_body)}</p>
      </article>
      <article class="hero-card panel">
        <p class="eyebrow">At A Glance</p>
        <div class="hero-grid">
          {hero_kpis}
        </div>
      </article>
    </section>
    {body_html}
    <p class="footer">{escape(footer_note)}</p>
  </div>
  {self._script()}
</body>
</html>
"""

    def _strategy_card(self, view: PaperStrategyView) -> str:
        summary = view.summary
        return (
            "<article class='strategy-card'>"
            f"<h3>{escape(view.name)}</h3>"
            f"<p>{escape(PIPELINE_EXPLANATIONS.get(view.pipeline, 'Paper-traded research sleeve.'))}</p>"
            "<div class='pill-row'>"
            f"<span class='pill'>{escape(self._format_label(view.pipeline))}</span>"
            f"<span class='pill'>{escape(self._format_label(view.mode))}</span>"
            f"<span class='pill'>{escape(self._format_value('trade_count', summary.get('trade_count')))} trades</span>"
            "</div>"
            f"<p>Ending equity: <strong>{escape(self._format_value('equity_after', summary.get('equity_after')))}</strong></p>"
            f"<p>Latest move: <strong>{escape(self._format_value('daily_pnl', summary.get('daily_pnl')))}</strong></p>"
            f"<p>Gross exposure: <strong>{escape(self._format_value('gross_exposure_ratio', summary.get('gross_exposure_ratio')))}</strong></p>"
            f"<a class='strategy-link' href='{escape(view.page_name)}'>Open sleeve page</a>"
            "</article>"
        )

    def _build_overview_page(
        self,
        batch_summary: dict[str, Any],
        strategy_views: dict[str, PaperStrategyView],
        aggregate: pd.DataFrame,
        charts: dict[str, Path],
    ) -> Path:
        latest_total_equity = aggregate["total_equity"].iloc[-1] if not aggregate.empty else 0.0
        latest_total_change = aggregate["total_net_change"].iloc[-1] if not aggregate.empty else 0.0
        latest_positions = int(round(aggregate["total_positions"].iloc[-1])) if not aggregate.empty else 0
        total_strategies = len(strategy_views)
        hero_metrics = [
            ("Combined Equity", self._format_value("equity_after", latest_total_equity)),
            ("Latest Change", self._format_value("daily_pnl", latest_total_change)),
            ("Open Positions", self._format_value("position_count", latest_positions)),
            ("Live Sleeves", self._format_value("count", total_strategies)),
        ]

        leaderboard = pd.DataFrame(batch_summary.get("leaderboard", []))
        if not leaderboard.empty:
            leaderboard = leaderboard.rename(columns={"net_return_since_inception": "return_since_inception"})
        leaderboard_html = self._render_table(leaderboard)

        explain_cards = "".join(
            "<article class='explain-card'>"
            f"<h3>{escape(title)}</h3>"
            f"<p>{escape(body)}</p>"
            "</article>"
            for title, body in (
                (
                    "What feels live here",
                    "This dashboard updates every time the paper batch runs. It shows the latest saved fake-money ledger state, not tick-by-tick intraday streaming.",
                ),
                (
                    "Where the money moved",
                    "Start with combined equity. Then check latest market move, rebalance cost, and the sleeve pages for positions and orders.",
                ),
                (
                    "How to read today's change",
                    "Previous close plus market move plus rebalance cost equals ending equity. The Capital Flow page visualizes that formula directly.",
                ),
                (
                    "Why some sleeves look synthetic",
                    "Residual stat-arb is still tracked as a component shadow book. Its PnL is real within the simulation, but it is not broker-routed leg by leg yet.",
                ),
            )
        )

        body_html = f"""
    <section class="metric-grid">
      {self._metric_card('Total Cash', self._format_value('cash_after', aggregate['total_cash'].iloc[-1] if not aggregate.empty else 0.0), 'Unallocated capital across all sleeves after the latest rebalance.')}
      {self._metric_card('Total Gross Exposure', self._format_value('gross_exposure_notional', aggregate['total_gross_exposure'].iloc[-1] if not aggregate.empty else 0.0), 'Absolute capital currently deployed across longs and shorts.')}
      {self._metric_card('Gross Ratio', self._format_value('gross_exposure_ratio', aggregate['total_gross_ratio'].iloc[-1] if not aggregate.empty else 0.0), 'A quick leverage-style view across the paper deployment.')}
      {self._metric_card('Latest Trade Count', self._format_value('trade_count', aggregate['total_trade_count'].iloc[-1] if not aggregate.empty else 0.0), 'How many simulated orders were needed on the latest run.')}
    </section>

    <section class="panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Overview</p>
          <h2>Where the fake money sits now</h2>
          <p class="section-copy">Open this page when you want the current picture quickly: total equity, who is making or losing money, and which sleeves currently hold risk.</p>
        </div>
      </div>
      <div class="chart-grid">
        {self._chart_card('Combined Equity', 'All sleeve ledgers added together so you can see the fake-money total over time.', charts['combined_equity'])}
        {self._chart_card('Leaderboard', 'The cleanest side-by-side comparison of which sleeve is currently ahead.', charts['leaderboard'])}
      </div>
    </section>

    <section class="panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Understand It</p>
          <h2>How To Read The Money</h2>
        </div>
      </div>
      {self._tab_group(
          'overview-tabs',
          [
              ('what-is-live', 'What Is Live', "<div class='explain-grid'>" + explain_cards + "</div>"),
              ('leaderboard-table', 'Leaderboard Table', leaderboard_html),
              ('sleeve-cards', 'Sleeve Pages', "<div class='strategy-card-grid'>" + ''.join(self._strategy_card(view) for view in strategy_views.values()) + "</div>"),
          ],
      )}
    </section>
"""

        html = self._page_shell(
            title="Shadow Paper Dashboard - Overview",
            hero_title="Paper capital overview",
            hero_body="This is the quickest place to see what the fake money is doing right now. Every number here comes from the saved paper ledgers and updates when the paper batch runs.",
            hero_metrics=hero_metrics,
            active_page="overview",
            body_html=body_html,
            strategy_views=strategy_views,
            footer_note=f"Generated from {batch_summary.get('artifact_dir', self.output_dir)} using ledger state at {batch_summary.get('state_dir', '')}.",
        )
        path = self.output_dir / "index.html"
        path.write_text(html, encoding="utf-8")
        return path

    def _build_capital_flow_page(
        self,
        batch_summary: dict[str, Any],
        strategy_views: dict[str, PaperStrategyView],
        aggregate: pd.DataFrame,
        charts: dict[str, Path],
    ) -> Path:
        previous_close = 0.0
        latest_market_move = 0.0
        latest_rebalance_cost = 0.0
        latest_end_equity = 0.0
        for summary in batch_summary.get("strategies", {}).values():
            previous_close += self._safe_float(summary.get("equity_before")) - self._safe_float(summary.get("daily_pnl"))
            latest_market_move += self._safe_float(summary.get("daily_pnl"))
            latest_rebalance_cost += self._safe_float(summary.get("rebalance_cost_pnl"))
            latest_end_equity += self._safe_float(summary.get("equity_after"))

        hero_metrics = [
            ("Previous Close", self._format_value("equity_before", previous_close)),
            ("Market Move", self._format_value("daily_pnl", latest_market_move)),
            ("Rebalance Cost", self._format_value("rebalance_cost_pnl", latest_rebalance_cost)),
            ("Ending Equity", self._format_value("equity_after", latest_end_equity)),
        ]

        exposure_snapshot = {
            "total_cash": aggregate["total_cash"].iloc[-1] if not aggregate.empty else 0.0,
            "total_gross_exposure": aggregate["total_gross_exposure"].iloc[-1] if not aggregate.empty else 0.0,
            "total_gross_ratio": aggregate["total_gross_ratio"].iloc[-1] if not aggregate.empty else 0.0,
            "total_turnover": aggregate["total_turnover"].iloc[-1] if not aggregate.empty else 0.0,
            "total_trade_count": aggregate["total_trade_count"].iloc[-1] if not aggregate.empty else 0.0,
        }

        formula_html = """
<div class='explain-grid'>
  <article class='explain-card'>
    <h3>Step 1: previous close</h3>
    <p>This is the last saved equity before the newest market move happened.</p>
  </article>
  <article class='explain-card'>
    <h3>Step 2: market move</h3>
    <p><code>daily_pnl</code> shows what the open positions made or lost before new trades were applied.</p>
  </article>
  <article class='explain-card'>
    <h3>Step 3: rebalance cost</h3>
    <p><code>rebalance_cost_pnl</code> captures the immediate effect of simulated execution costs, including slippage and commissions.</p>
  </article>
  <article class='explain-card'>
    <h3>Step 4: ending equity</h3>
    <p>That final figure is the new fake-money balance carried into the next paper run.</p>
  </article>
</div>
"""

        body_html = f"""
    <section class="panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Capital Flow</p>
          <h2>Follow the money step by step</h2>
          <p class="section-copy">Use this page when you want to answer: what changed, why it changed, and how much of the move came from the market versus the rebalance itself.</p>
        </div>
      </div>
      <div class="chart-grid">
        {self._chart_card('Capital Timeline', 'Cash, gross exposure, turnover, and trading intensity over the saved paper history.', charts['capital_timeline'])}
        {self._chart_card('Latest Money Flow', 'A direct view of where the latest fake-money move came from.', charts['latest_money_flow'])}
      </div>
    </section>

    <section class="panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Explain</p>
          <h2>Money Formula</h2>
        </div>
      </div>
      {self._tab_group(
          'capital-tabs',
          [
              ('formula', 'Formula', formula_html),
              ('exposure', 'Latest Exposure', self._render_key_value(exposure_snapshot)),
              ('batch', 'Latest Batch JSON', "<details open><summary>Latest batch summary</summary>" + self._json_block(batch_summary) + "</details>"),
          ],
      )}
    </section>
"""

        html = self._page_shell(
            title="Shadow Paper Dashboard - Capital Flow",
            hero_title="Capital flow and rebalance impact",
            hero_body="This page breaks the fake-money movement into parts so you can see whether the book moved because the market moved, because the strategy traded, or both.",
            hero_metrics=hero_metrics,
            active_page="capital",
            body_html=body_html,
            strategy_views=strategy_views,
            footer_note="Use this page to audit the latest paper rebalance before trusting the headline equity number.",
        )
        path = self.output_dir / "capital_flow.html"
        path.write_text(html, encoding="utf-8")
        return path

    def _build_glossary_page(
        self,
        batch_summary: dict[str, Any],
        strategy_views: dict[str, PaperStrategyView],
    ) -> Path:
        glossary_rows = pd.DataFrame(
            [
                {"field": field, "label": title, "meaning": description}
                for field, (title, description) in FIELD_GLOSSARY.items()
            ]
        )
        mode_rows = pd.DataFrame(
            [{"mode": mode, "meaning": text} for mode, text in MODE_EXPLANATIONS.items()]
        )
        pipeline_rows = pd.DataFrame(
            [{"pipeline": pipeline, "meaning": text} for pipeline, text in PIPELINE_EXPLANATIONS.items()]
        )

        body_html = f"""
    <section class="panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Glossary</p>
          <h2>What each field means</h2>
          <p class="section-copy">This page is the reference sheet. If a number in the dashboard is unclear, the meaning is defined here in plain language.</p>
        </div>
      </div>
      {self._tab_group(
          'glossary-tabs',
          [
              ('fields', 'Ledger Fields', self._render_table(glossary_rows, max_rows=20)),
              ('modes', 'Book Modes', self._render_table(mode_rows, max_rows=8)),
              ('pipelines', 'Sleeve Types', self._render_table(pipeline_rows, max_rows=12)),
              ('live-note', 'What "Live" Means', "<div class='explain-grid'><article class='explain-card'><h3>Current implementation</h3><p>The paper dashboard is live at the cadence of the paper run. If the automation runs nightly, the dashboard refreshes nightly. It is not a streaming brokerage blotter yet.</p></article><article class='explain-card'><h3>Where to look first</h3><p>Open the stable latest dashboard path, then use the Overview page for total fake money and the sleeve page for positions and orders.</p></article><article class='explain-card'><h3>Why this is still valuable</h3><p>Even without streaming, this is the right first control layer because it preserves attribution, costs, and state per sleeve.</p></article></div>"),
          ],
      )}
    </section>

    <section class="panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Reference</p>
          <h2>Current Strategy Map</h2>
        </div>
      </div>
      <div class="strategy-card-grid">
        {''.join(self._strategy_card(view) for view in strategy_views.values())}
      </div>
    </section>
"""

        html = self._page_shell(
            title="Shadow Paper Dashboard - Glossary",
            hero_title="Dashboard glossary and field guide",
            hero_body="This page explains what the numbers mean so you do not have to reverse-engineer the ledger schema from raw JSON files.",
            hero_metrics=[
                ("As-Of Date", str(batch_summary.get("asof_date", "N/A"))),
                ("Run Timestamp", str(batch_summary.get("run_timestamp_utc", "N/A"))),
                ("Strategies", self._format_value("count", len(strategy_views))),
                ("State Folder", str(batch_summary.get("state_dir", ""))),
            ],
            active_page="glossary",
            body_html=body_html,
            strategy_views=strategy_views,
            footer_note="If a dashboard number is ever unclear, this page should be the source of truth.",
        )
        path = self.output_dir / "glossary.html"
        path.write_text(html, encoding="utf-8")
        return path

    def _build_strategy_page(
        self,
        view: PaperStrategyView,
        strategy_views: dict[str, PaperStrategyView],
        charts: dict[str, Path],
    ) -> Path:
        summary = view.summary
        hero_metrics = [
            ("Ending Equity", self._format_value("equity_after", summary.get("equity_after"))),
            ("Market Move", self._format_value("daily_pnl", summary.get("daily_pnl"))),
            ("Rebalance Cost", self._format_value("rebalance_cost_pnl", summary.get("rebalance_cost_pnl"))),
            ("Gross Ratio", self._format_value("gross_exposure_ratio", summary.get("gross_exposure_ratio"))),
        ]

        scalar_diagnostics = {
            key: value
            for key, value in view.diagnostics.items()
            if not isinstance(value, (dict, list))
        }
        diagnostics_tabs: list[tuple[str, str, str]] = [
            (
                "book",
                "Current Book",
                "<div class='two-column'>"
                f"<div><h3>Positions</h3>{self._render_table(view.positions, max_rows=12)}</div>"
                f"<div><h3>Target Weights</h3>{self._render_table(view.target_weights, max_rows=12)}</div>"
                "</div>",
            ),
            (
                "orders",
                "Latest Orders",
                "<div>"
                "<p class='section-copy'>These are the orders the paper engine simulated on the latest run for this sleeve.</p>"
                f"{self._render_table(view.latest_orders, max_rows=16)}"
                "</div>",
            ),
            (
                "diagnostics",
                "Diagnostics",
                "<div class='two-column'>"
                f"<div><h3>Scalar Diagnostics</h3>{self._render_key_value(scalar_diagnostics)}</div>"
                f"<div><h3>Raw Diagnostics</h3><details open><summary>Open JSON</summary>{self._json_block(view.diagnostics)}</details></div>"
                "</div>",
            ),
            (
                "explainer",
                "Explainer",
                "<div class='explain-grid'>"
                f"<article class='explain-card'><h3>Pipeline</h3><p>{escape(PIPELINE_EXPLANATIONS.get(view.pipeline, 'Paper-traded research sleeve.'))}</p></article>"
                f"<article class='explain-card'><h3>Book Mode</h3><p>{escape(MODE_EXPLANATIONS.get(view.mode, 'Paper-traded ledger mode.'))}</p></article>"
                "<article class='explain-card'><h3>What to inspect first</h3><p>Start with equity and activity charts, then compare positions against target weights, then look at latest orders if the book changed.</p></article>"
                "<article class='explain-card'><h3>What a flat book means</h3><p>If positions and orders are empty, the sleeve is alive but currently sees no approved trade to hold.</p></article>"
                "</div>",
            ),
        ]

        if isinstance(view.diagnostics.get("strategy_summaries"), list):
            diagnostics_tabs.insert(
                2,
                (
                    "components",
                    "Component Diagnostics",
                    self._render_table(pd.DataFrame(view.diagnostics.get("strategy_summaries", [])), max_rows=12),
                ),
            )
        elif isinstance(view.diagnostics.get("selected_pairs"), list):
            diagnostics_tabs.insert(
                2,
                (
                    "components",
                    "Selected Pairs",
                    self._render_table(pd.DataFrame(view.diagnostics.get("selected_pairs", [])), max_rows=12),
                ),
            )

        body_html = f"""
    <section class="metric-grid">
      {self._metric_card('Open Positions', self._format_value('position_count', summary.get('position_count')), 'Count of holdings currently in the fake-money book.')}
      {self._metric_card('Cash After', self._format_value('cash_after', summary.get('cash_after')), 'Residual cash after the latest rebalance.')}
      {self._metric_card('Turnover', self._format_value('turnover_notional', summary.get('turnover_notional')), 'Absolute traded notional on the latest simulated rebalance.')}
      {self._metric_card('Trade Count', self._format_value('trade_count', summary.get('trade_count')), 'Number of simulated orders placed on the latest run.')}
    </section>

    <section class="panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Sleeve Visuals</p>
          <h2>{escape(view.name)}</h2>
          <p class="section-copy">{escape(PIPELINE_EXPLANATIONS.get(view.pipeline, 'Paper-traded research sleeve.'))} {escape(MODE_EXPLANATIONS.get(view.mode, ''))}</p>
        </div>
      </div>
      <div class="chart-grid">
        {self._chart_card('Equity And Drawdown', 'How this sleeve has compounded and how deep it has fallen from prior peaks.', charts['equity'])}
        {self._chart_card('Activity', 'Market PnL before rebalance, plus turnover and order count.', charts['activity'])}
        {self._chart_card('Book Snapshot', 'Current holdings and requested target weights.', charts['book'])}
        {self._chart_card('Capital Structure', 'Cash, gross exposure, exposure ratio, and number of held positions.', charts['capital'])}
        {self._chart_card('Latest Orders', 'The most recent simulated rebalance orders for this sleeve.', charts['orders'])}
      </div>
    </section>

    <section class="panel section">
      <div class="section-header">
        <div>
          <p class="eyebrow">Explain</p>
          <h2>Book, Orders, And Diagnostics</h2>
        </div>
      </div>
      {self._tab_group(f"tabs-{self._slug(view.name)}", diagnostics_tabs)}
    </section>
"""

        html = self._page_shell(
            title=f"Shadow Paper Dashboard - {view.name}",
            hero_title=view.name,
            hero_body="This page is the live operating view for a single sleeve. Use it to understand current fake-money holdings, what changed on the latest run, and why the strategy looks the way it does.",
            hero_metrics=hero_metrics,
            active_page=view.page_name,
            body_html=body_html,
            strategy_views=strategy_views,
            footer_note=f"Sleeve type: {view.pipeline} | Mode: {view.mode}",
        )
        path = self.output_dir / view.page_name
        path.write_text(html, encoding="utf-8")
        return path

    def create_dashboard(
        self,
        *,
        batch_summary: dict[str, Any],
        state_dir: str | Path,
        allow_legacy: bool = False,
    ) -> dict[str, Any]:
        state_path = Path(state_dir)
        strategy_views = self._load_strategy_views(batch_summary, state_path, allow_legacy=allow_legacy)
        aggregate = self._aggregate_history(strategy_views)

        chart_manifest = {
            "combined_equity": self._plot_combined_equity(aggregate, strategy_views),
            "leaderboard": self._plot_leaderboard(batch_summary),
            "capital_timeline": self._plot_capital_timeline(aggregate),
            "latest_money_flow": self._plot_latest_money_flow(batch_summary),
        }

        strategy_pages: dict[str, Path] = {}
        for view in strategy_views.values():
            strategy_chart_manifest = {
                "equity": self._plot_strategy_equity(view),
                "activity": self._plot_strategy_activity(view),
                "book": self._plot_strategy_book(view),
                "capital": self._plot_strategy_capital(view),
                "orders": self._plot_strategy_orders(view),
            }
            strategy_pages[view.name] = self._build_strategy_page(view, strategy_views, strategy_chart_manifest)
            chart_manifest.update({f"{self._slug(view.name)}_{key}": value for key, value in strategy_chart_manifest.items()})

        pages = {
            "overview": self._build_overview_page(batch_summary, strategy_views, aggregate, chart_manifest),
            "capital_flow": self._build_capital_flow_page(batch_summary, strategy_views, aggregate, chart_manifest),
            "glossary": self._build_glossary_page(batch_summary, strategy_views),
            "strategy_pages": strategy_pages,
        }
        return {"output_dir": self.output_dir, "pages": pages, "charts": chart_manifest}
