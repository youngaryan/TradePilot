from __future__ import annotations

from datetime import UTC, datetime
from html import escape
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ..engines.backtesting import ExperimentResult, json_ready


CHART_DESCRIPTIONS = {
    "equity_curve": ("Equity Curve", "Gross versus net equity, with periodic net returns for context."),
    "drawdown": ("Drawdown Profile", "Underwater curve plus time spent below prior equity highs."),
    "exposure_costs": ("Exposure And Cost Profile", "Portfolio exposure, turnover, and cumulative gross or net deployment context."),
    "execution_diagnostics": ("Execution Diagnostics", "Cost decomposition together with risk scaling and participation assumptions."),
    "fold_metrics": ("Fold Metrics", "Per-fold return, Sharpe, and hit-rate behavior across the walk-forward."),
    "strategy_weights": ("Component Weight Heatmap", "Top deployed sleeves or strategies over time."),
    "component_summary": ("Component Summary", "Average absolute allocation and final signed weights by sleeve."),
    "sentiment_overlay": ("Sentiment Overlay", "Portfolio-level sentiment strength, confidence, and composite overlay."),
    "validation_snapshot": ("Validation Snapshot", "PSR, DSR, and overfitting resistance, with trial-selection concentration when available."),
    "latest_pair_selection": ("Latest Pair Selection", "Most recent classic-pairs sleeve ranking on the latest fold."),
}


class ExperimentVisualizer:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.colors = {
            "navy": "#0f172a",
            "blue": "#155eef",
            "teal": "#0f766e",
            "green": "#16a34a",
            "gold": "#ca8a04",
            "orange": "#ea580c",
            "red": "#dc2626",
            "maroon": "#991b1b",
            "slate": "#475569",
            "light": "#e2e8f0",
            "ink": "#111827",
            "cloud": "#f8fafc",
        }

    def _save_figure(self, fig: plt.Figure, filename: str) -> Path:
        path = self.output_dir / filename
        fig.tight_layout()
        fig.savefig(path, dpi=170, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        return path

    def _apply_axis_style(self, ax: plt.Axes, *, ylabel: str | None = None) -> None:
        ax.set_facecolor("#ffffff")
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.spines["left"].set_color("#cbd5e1")
        ax.spines["bottom"].set_color("#cbd5e1")
        ax.grid(True, axis="y", color="#dbe4f0", linewidth=0.8, alpha=0.7)
        ax.tick_params(colors=self.colors["slate"], labelsize=9)
        if ylabel:
            ax.set_ylabel(ylabel, color=self.colors["slate"], fontsize=10)

    def _format_dates(self, ax: plt.Axes) -> None:
        locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
        formatter = mdates.ConciseDateFormatter(locator)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)

    def _prepared_frame(self, result: ExperimentResult) -> pd.DataFrame:
        frame = result.equity_curve.sort_index().copy()
        if frame.empty:
            return frame

        if "net_return" in frame.columns and "equity_curve" not in frame.columns:
            frame["equity_curve"] = (1.0 + pd.to_numeric(frame["net_return"], errors="coerce").fillna(0.0)).cumprod()

        if "gross_return" in frame.columns and "gross_equity_curve" not in frame.columns:
            frame["gross_equity_curve"] = (1.0 + pd.to_numeric(frame["gross_return"], errors="coerce").fillna(0.0)).cumprod()

        if "equity_curve" not in frame.columns:
            frame["equity_curve"] = 1.0

        equity = pd.to_numeric(frame["equity_curve"], errors="coerce").ffill().fillna(1.0)
        frame["equity_curve"] = equity
        frame["drawdown"] = equity / equity.cummax() - 1.0

        if "net_return" not in frame.columns:
            frame["net_return"] = equity.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)

        underwater_duration = []
        current_duration = 0
        for value in frame["drawdown"].fillna(0.0):
            if value < 0.0:
                current_duration += 1
            else:
                current_duration = 0
            underwater_duration.append(current_duration)
        frame["underwater_duration"] = underwater_duration
        return frame

    @staticmethod
    def _latest_diagnostics(result: ExperimentResult) -> dict[str, Any]:
        if not result.diagnostics:
            return {}
        latest = result.diagnostics[-1]
        if isinstance(latest, dict) and "diagnostics" in latest:
            return latest.get("diagnostics", {})
        return latest if isinstance(latest, dict) else {}

    @staticmethod
    def _weight_columns(frame: pd.DataFrame) -> list[str]:
        return [column for column in frame.columns if column.startswith("weight_")]

    @staticmethod
    def _display_name(value: str) -> str:
        return value.replace("_", " ").replace("/", " / ").title()

    def _format_value(self, key: str, value: Any) -> str:
        if value is None:
            return "N/A"
        if isinstance(value, (np.integer, int)):
            return f"{int(value):,}"
        if isinstance(value, (np.floating, float)):
            percent_like = (
                "return" in key
                or "drawdown" in key
                or "vol" in key
                or key in {"psr", "dsr", "pbo", "hit_rate", "avg_turnover", "mean_risk_scale"}
                or key.endswith("_cost")
                or "exposure" in key
                or "participation" in key
            )
            if percent_like:
                return f"{float(value):.2%}"
            return f"{float(value):.3f}"
        if isinstance(value, pd.Timestamp):
            return value.strftime("%Y-%m-%d")
        if isinstance(value, (list, tuple)):
            if not value:
                return "[]"
            if len(value) > 8:
                preview = ", ".join(str(item) for item in value[:8])
                return f"{preview}, +{len(value) - 8} more"
            return ", ".join(str(item) for item in value)
        if isinstance(value, dict):
            return json.dumps(json_ready(value), indent=2)
        return str(value)

    def _metric_card(self, label: str, value: Any, caption: str = "") -> str:
        return (
            "<article class='metric-card'>"
            f"<p class='metric-label'>{escape(label)}</p>"
            f"<p class='metric-value'>{escape(value)}</p>"
            f"<p class='metric-caption'>{escape(caption)}</p>"
            "</article>"
        )

    def _render_key_value_table(self, mapping: dict[str, Any], *, table_class: str = "key-value") -> str:
        if not mapping:
            return "<p class='empty-state'>No details available.</p>"
        rows = "".join(
            "<tr>"
            f"<th>{escape(self._display_name(str(key)))}</th>"
            f"<td>{escape(self._format_value(str(key), value))}</td>"
            "</tr>"
            for key, value in mapping.items()
        )
        return f"<table class='{escape(table_class)}'><tbody>{rows}</tbody></table>"

    def _render_dataframe_table(self, frame: pd.DataFrame, *, index: bool = False, max_rows: int = 12) -> str:
        if frame.empty:
            return "<p class='empty-state'>No rows available.</p>"

        preview = frame.head(max_rows).copy()
        preview = preview.replace({np.nan: ""})
        headers = []
        if index:
            headers.append("<th>Index</th>")
        headers.extend(f"<th>{escape(self._display_name(str(column)))}</th>" for column in preview.columns)

        body_rows = []
        for idx, row in preview.iterrows():
            cells = []
            if index:
                cells.append(f"<td>{escape(self._format_value('index', idx))}</td>")
            for column, value in row.items():
                cells.append(f"<td>{escape(self._format_value(str(column), value))}</td>")
            body_rows.append("<tr>" + "".join(cells) + "</tr>")

        return (
            "<div class='table-shell'>"
            "<table class='data-table'>"
            f"<thead><tr>{''.join(headers)}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table>"
            "</div>"
        )

    def plot_equity_curve(self, result: ExperimentResult) -> Path:
        frame = self._prepared_frame(result)
        fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, height_ratios=[3.0, 1.2])
        fig.patch.set_facecolor("#f8fafc")

        axes[0].plot(frame.index, frame["equity_curve"], color=self.colors["blue"], linewidth=2.6, label="Net Equity")
        if "gross_equity_curve" in frame.columns:
            axes[0].plot(
                frame.index,
                frame["gross_equity_curve"],
                color=self.colors["teal"],
                linewidth=1.8,
                linestyle="--",
                label="Gross Equity",
            )
        axes[0].axhline(1.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        axes[0].fill_between(frame.index, frame["equity_curve"], 1.0, color="#bfdbfe", alpha=0.18)
        axes[0].set_title("Walk-Forward Equity Curve", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Equity")
        axes[0].legend(frameon=False, loc="upper left")

        bar_colors = np.where(frame["net_return"].fillna(0.0) >= 0.0, "#16a34a", "#dc2626")
        axes[1].bar(frame.index, frame["net_return"].fillna(0.0) * 100.0, color=bar_colors, width=5)
        rolling = frame["net_return"].rolling(21, min_periods=3).mean().fillna(0.0) * 100.0
        axes[1].plot(frame.index, rolling, color=self.colors["gold"], linewidth=1.6, label="21-Bar Mean")
        axes[1].axhline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        axes[1].set_title("Periodic Net Returns", loc="left", fontsize=13, color=self.colors["navy"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Return (%)")
        axes[1].legend(frameon=False, loc="upper left")
        self._format_dates(axes[1])
        return self._save_figure(fig, "equity_curve.png")

    def plot_drawdown(self, result: ExperimentResult) -> Path:
        frame = self._prepared_frame(result)
        fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True, height_ratios=[2.4, 1.0])
        fig.patch.set_facecolor("#f8fafc")

        axes[0].fill_between(frame.index, frame["drawdown"] * 100.0, 0.0, color="#fca5a5", alpha=0.55)
        axes[0].plot(frame.index, frame["drawdown"] * 100.0, color=self.colors["maroon"], linewidth=1.8)
        axes[0].axhline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        axes[0].set_title("Drawdown", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Drawdown (%)")

        axes[1].plot(frame.index, frame["underwater_duration"], color=self.colors["orange"], linewidth=2.0)
        axes[1].fill_between(frame.index, frame["underwater_duration"], 0.0, color="#fed7aa", alpha=0.4)
        axes[1].set_title("Time Underwater", loc="left", fontsize=13, color=self.colors["navy"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Bars")
        self._format_dates(axes[1])
        return self._save_figure(fig, "drawdown.png")

    def plot_exposure_and_costs(self, result: ExperimentResult) -> Path:
        frame = self._prepared_frame(result)
        fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True, height_ratios=[2.2, 1.6])
        fig.patch.set_facecolor("#f8fafc")

        exposures = [
            ("gross_exposure", "Gross Exposure", self.colors["teal"]),
            ("short_exposure", "Short Exposure", self.colors["orange"]),
            ("risk_gross_exposure", "Risk Gross Exposure", self.colors["blue"]),
            ("risk_net_exposure", "Risk Net Exposure", self.colors["gold"]),
        ]
        any_exposure = False
        for column, label, color in exposures:
            if column in frame.columns:
                axes[0].plot(frame.index, pd.to_numeric(frame[column], errors="coerce").fillna(0.0), label=label, color=color, linewidth=2.0)
                any_exposure = True
        if "turnover" in frame.columns:
            turnover_ax = axes[0].twinx()
            turnover_ax.plot(frame.index, pd.to_numeric(frame["turnover"], errors="coerce").fillna(0.0), color=self.colors["slate"], linewidth=1.4, linestyle="--", label="Turnover")
            turnover_ax.set_ylabel("Turnover", color=self.colors["slate"], fontsize=10)
            turnover_ax.tick_params(colors=self.colors["slate"], labelsize=9)
            turnover_ax.spines["top"].set_visible(False)
            turnover_ax.spines["left"].set_visible(False)
            turnover_ax.spines["right"].set_color("#cbd5e1")
        axes[0].set_title("Exposure And Turnover", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Exposure")
        if any_exposure:
            handles, labels = axes[0].get_legend_handles_labels()
            if "turnover" in frame.columns:
                extra_handles, extra_labels = turnover_ax.get_legend_handles_labels()
                handles.extend(extra_handles)
                labels.extend(extra_labels)
            axes[0].legend(handles, labels, frameon=False, loc="upper left", ncol=3)

        cumulative_series = []
        for column, label, color in (
            ("execution_cost", "Execution", self.colors["blue"]),
            ("impact_cost", "Impact", self.colors["orange"]),
            ("latency_cost", "Latency", self.colors["gold"]),
            ("borrow_cost", "Borrow", self.colors["red"]),
            ("funding_cost", "Funding", self.colors["slate"]),
            ("strategy_cost", "Strategy", self.colors["teal"]),
            ("total_cost", "Total", self.colors["navy"]),
        ):
            if column in frame.columns:
                axes[1].plot(
                    frame.index,
                    pd.to_numeric(frame[column], errors="coerce").fillna(0.0).cumsum(),
                    label=label,
                    color=color,
                    linewidth=2.1 if column == "total_cost" else 1.6,
                    alpha=1.0 if column == "total_cost" else 0.9,
                )
                cumulative_series.append(column)
        if not cumulative_series:
            axes[1].text(0.02, 0.5, "No explicit cost series found in the equity frame.", transform=axes[1].transAxes, color=self.colors["slate"], fontsize=11)
        axes[1].set_title("Cumulative Cost Build-Up", loc="left", fontsize=13, color=self.colors["navy"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Cumulative Cost")
        if cumulative_series:
            axes[1].legend(frameon=False, loc="upper left", ncol=4)
        self._format_dates(axes[1])
        return self._save_figure(fig, "exposure_costs.png")

    def plot_execution_diagnostics(self, result: ExperimentResult) -> Path:
        frame = self._prepared_frame(result)
        fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True, height_ratios=[1.6, 1.8])
        fig.patch.set_facecolor("#f8fafc")

        risk_scale = pd.to_numeric(frame.get("risk_scale", pd.Series(1.0, index=frame.index)), errors="coerce").fillna(1.0)
        participation = pd.to_numeric(frame.get("participation_rate", pd.Series(0.0, index=frame.index)), errors="coerce").fillna(0.0)
        risk_flags = pd.to_numeric(frame.get("risk_flag", pd.Series(0.0, index=frame.index)), errors="coerce").fillna(0.0)

        axes[0].plot(frame.index, risk_scale, color=self.colors["teal"], linewidth=2.0, label="Risk Scale")
        axes[0].plot(frame.index, participation, color=self.colors["gold"], linewidth=1.6, label="Participation Rate")
        if risk_flags.abs().sum() > 0:
            axes[0].fill_between(frame.index, 0.0, risk_flags, color="#fecaca", alpha=0.35, step="pre", label="Risk Flag")
        axes[0].set_ylim(bottom=0.0)
        axes[0].set_title("Risk Controls And Participation", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Scale / Rate")
        axes[0].legend(frameon=False, loc="upper left", ncol=3)

        breakdown_columns = [
            ("execution_cost", "Execution", "#93c5fd"),
            ("impact_cost", "Impact", "#fdba74"),
            ("latency_cost", "Latency", "#fde68a"),
            ("borrow_cost", "Borrow", "#fca5a5"),
            ("funding_cost", "Funding", "#cbd5e1"),
            ("strategy_cost", "Strategy", "#99f6e4"),
        ]
        present = [(column, label, color) for column, label, color in breakdown_columns if column in frame.columns]
        if present:
            stack_values = [pd.to_numeric(frame[column], errors="coerce").fillna(0.0).values for column, _, _ in present]
            labels = [label for _, label, _ in present]
            colors = [color for _, _, color in present]
            axes[1].stackplot(frame.index, stack_values, labels=labels, colors=colors, alpha=0.85)
        else:
            axes[1].text(0.02, 0.5, "Execution breakdown unavailable.", transform=axes[1].transAxes, color=self.colors["slate"], fontsize=11)
        axes[1].set_title("Cost Breakdown Per Bar", loc="left", fontsize=13, color=self.colors["navy"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Cost")
        if present:
            axes[1].legend(frameon=False, loc="upper left", ncol=3)
        self._format_dates(axes[1])
        return self._save_figure(fig, "execution_diagnostics.png")

    def plot_strategy_weights(self, result: ExperimentResult) -> Path | None:
        frame = self._prepared_frame(result)
        weight_columns = self._weight_columns(frame)
        if not weight_columns:
            return None

        average_abs = frame[weight_columns].abs().mean().sort_values(ascending=False)
        top_columns = average_abs.head(16).index.tolist()
        weight_matrix = frame[top_columns].T
        if weight_matrix.empty:
            return None

        fig, ax = plt.subplots(figsize=(14, max(4.5, len(top_columns) * 0.55)))
        fig.patch.set_facecolor("#f8fafc")
        vmax = max(0.05, float(np.nanmax(np.abs(weight_matrix.values))))
        image = ax.imshow(weight_matrix.values, aspect="auto", cmap="RdYlBu_r", vmin=-vmax, vmax=vmax)
        positions = np.linspace(0, len(frame.index) - 1, num=min(8, len(frame.index)), dtype=int)
        ax.set_xticks(positions)
        ax.set_xticklabels([frame.index[i].strftime("%Y-%m-%d") for i in positions], rotation=30, ha="right", fontsize=9)
        ax.set_yticks(range(len(top_columns)))
        ax.set_yticklabels([column.replace("weight_", "") for column in top_columns], fontsize=9)
        ax.set_title("Top Component Weights", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        ax.set_facecolor("#ffffff")
        for spine in ax.spines.values():
            spine.set_visible(False)
        colorbar = fig.colorbar(image, ax=ax, fraction=0.025, pad=0.02)
        colorbar.outline.set_edgecolor("#cbd5e1")
        colorbar.set_label("Weight", color=self.colors["slate"])
        return self._save_figure(fig, "strategy_weights.png")

    def plot_component_summary(self, result: ExperimentResult) -> Path | None:
        frame = self._prepared_frame(result)
        weight_columns = self._weight_columns(frame)
        if not weight_columns:
            return None

        average_abs = frame[weight_columns].abs().mean().sort_values(ascending=True).tail(12)
        final_signed = frame[average_abs.index].iloc[-1]

        fig, axes = plt.subplots(1, 2, figsize=(15, max(5, len(average_abs) * 0.45)))
        fig.patch.set_facecolor("#f8fafc")

        labels = [column.replace("weight_", "") for column in average_abs.index]
        axes[0].barh(labels, average_abs.values, color="#7dd3fc")
        axes[0].set_title("Average Absolute Weight", loc="left", fontsize=15, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[0], ylabel=None)
        axes[0].tick_params(axis="y", labelsize=9)

        final_colors = ["#16a34a" if value >= 0 else "#dc2626" for value in final_signed.values]
        axes[1].barh(labels, final_signed.values, color=final_colors)
        axes[1].axvline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        axes[1].set_title("Final Signed Weight", loc="left", fontsize=15, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[1], ylabel=None)
        axes[1].tick_params(axis="y", labelsize=9)

        return self._save_figure(fig, "component_summary.png")

    def plot_sentiment_overlay(self, result: ExperimentResult) -> Path | None:
        frame = self._prepared_frame(result)
        if "sentiment_strength" not in frame.columns and "sentiment_confidence" not in frame.columns:
            return None

        fig, axes = plt.subplots(2, 1, figsize=(14, 6.6), sharex=True, height_ratios=[1.5, 1.1])
        fig.patch.set_facecolor("#f8fafc")

        if "sentiment_strength" in frame.columns:
            strength = pd.to_numeric(frame["sentiment_strength"], errors="coerce").fillna(0.0)
            axes[0].plot(frame.index, strength, color=self.colors["green"], linewidth=2.0, label="Strength")
            axes[0].fill_between(frame.index, strength, 0.0, color="#bbf7d0", alpha=0.35)
        if "sentiment_confidence" in frame.columns:
            confidence = pd.to_numeric(frame["sentiment_confidence"], errors="coerce").fillna(0.0)
            axes[0].plot(frame.index, confidence, color=self.colors["gold"], linewidth=1.7, label="Confidence")
        axes[0].axhline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        axes[0].set_title("Sentiment Overlay", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Score")
        axes[0].legend(frameon=False, loc="upper left")

        composite = pd.to_numeric(frame.get("sentiment_strength", 0.0), errors="coerce").fillna(0.0) * pd.to_numeric(
            frame.get("sentiment_confidence", 1.0), errors="coerce"
        ).fillna(1.0)
        axes[1].plot(frame.index, composite, color=self.colors["teal"], linewidth=2.0, label="Composite Overlay")
        axes[1].fill_between(frame.index, composite, 0.0, color="#99f6e4", alpha=0.35)
        axes[1].axhline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        axes[1].set_title("Strength x Confidence", loc="left", fontsize=13, color=self.colors["navy"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Composite")
        axes[1].legend(frameon=False, loc="upper left")
        self._format_dates(axes[1])
        return self._save_figure(fig, "sentiment_overlay.png")

    def plot_fold_sharpe_and_return(self, result: ExperimentResult) -> Path:
        metrics = result.fold_metrics.copy()
        if metrics.empty:
            metrics = pd.DataFrame({"fold": [], "total_return": [], "sharpe": []})

        fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True, height_ratios=[1.5, 1.4])
        fig.patch.set_facecolor("#f8fafc")

        x = np.arange(len(metrics))
        returns = pd.to_numeric(metrics.get("total_return", pd.Series(0.0, index=metrics.index)), errors="coerce").fillna(0.0) * 100.0
        drawdown = pd.to_numeric(metrics.get("max_drawdown", pd.Series(0.0, index=metrics.index)), errors="coerce").fillna(0.0) * 100.0
        return_colors = ["#16a34a" if value >= 0 else "#dc2626" for value in returns]
        axes[0].bar(x, returns, color=return_colors, width=0.55, label="Total Return")
        axes[0].plot(x, drawdown, color=self.colors["maroon"], linewidth=1.8, marker="o", label="Max Drawdown")
        axes[0].axhline(0.0, color="#94a3b8", linestyle="--", linewidth=1.0)
        axes[0].set_title("Per-Fold Returns And Drawdown", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[0], ylabel="Percent")
        axes[0].legend(frameon=False, loc="upper left")

        sharpe = pd.to_numeric(metrics.get("sharpe", pd.Series(0.0, index=metrics.index)), errors="coerce").fillna(0.0)
        hit_rate = pd.to_numeric(metrics.get("hit_rate", pd.Series(0.0, index=metrics.index)), errors="coerce").fillna(0.0) * 100.0
        sharpe_colors = ["#155eef" if value >= 0 else "#f97316" for value in sharpe]
        axes[1].bar(x, sharpe, color=sharpe_colors, width=0.55, label="Sharpe")
        hit_ax = axes[1].twinx()
        hit_ax.plot(x, hit_rate, color=self.colors["gold"], marker="o", linewidth=1.8, label="Hit Rate")
        hit_ax.set_ylabel("Hit Rate (%)", color=self.colors["gold"], fontsize=10)
        hit_ax.tick_params(colors=self.colors["gold"], labelsize=9)
        hit_ax.spines["top"].set_visible(False)
        hit_ax.spines["left"].set_visible(False)
        hit_ax.spines["right"].set_color("#cbd5e1")

        fold_labels = [f"Fold {int(value)}" for value in metrics.get("fold", pd.Series(dtype=int))]
        axes[1].set_xticks(x)
        axes[1].set_xticklabels(fold_labels, rotation=0, fontsize=9)
        axes[1].set_title("Per-Fold Sharpe And Hit Rate", loc="left", fontsize=13, color=self.colors["navy"], pad=8)
        self._apply_axis_style(axes[1], ylabel="Sharpe")
        handles, labels = axes[1].get_legend_handles_labels()
        extra_handles, extra_labels = hit_ax.get_legend_handles_labels()
        axes[1].legend(handles + extra_handles, labels + extra_labels, frameon=False, loc="upper left")
        return self._save_figure(fig, "fold_metrics.png")

    def plot_validation_snapshot(self, result: ExperimentResult) -> Path | None:
        validation = result.validation or {}
        psr = validation.get("psr", result.summary.get("psr"))
        dsr = validation.get("dsr", result.summary.get("dsr"))
        pbo = validation.get("pbo", result.summary.get("pbo"))
        if psr is None and dsr is None and pbo is None:
            return None

        pbo_detail = validation.get("pbo_detail") if isinstance(validation, dict) else None
        has_frequency = bool(isinstance(pbo_detail, dict) and pbo_detail.get("selected_frequency"))
        fig, axes = plt.subplots(2 if has_frequency else 1, 1, figsize=(13, 7 if has_frequency else 4.5), height_ratios=[1.3, 1.0] if has_frequency else None)
        if not isinstance(axes, np.ndarray):
            axes = np.array([axes])
        fig.patch.set_facecolor("#f8fafc")

        bars = []
        labels = []
        colors = []
        for label, value, color in (
            ("PSR", psr, "#155eef"),
            ("DSR", dsr, "#16a34a"),
            ("Overfit Resistance", None if pbo is None else 1.0 - float(pbo), "#ca8a04"),
        ):
            if value is not None:
                labels.append(label)
                bars.append(float(value))
                colors.append(color)

        axes[0].barh(labels, bars, color=colors, height=0.6)
        axes[0].set_xlim(0.0, 1.0)
        axes[0].set_title("Validation Snapshot", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        self._apply_axis_style(axes[0], ylabel=None)
        axes[0].grid(True, axis="x", color="#dbe4f0", linewidth=0.8, alpha=0.7)
        for idx, value in enumerate(bars):
            axes[0].text(min(value + 0.02, 0.98), idx, f"{value:.1%}", va="center", ha="left", color=self.colors["navy"], fontsize=10)

        if has_frequency:
            frequency = pd.Series(pbo_detail["selected_frequency"]).sort_values(ascending=False)
            axes[1].bar(frequency.index.astype(str), frequency.values, color="#93c5fd")
            axes[1].set_title("Trial Selection Frequency", loc="left", fontsize=13, color=self.colors["navy"], pad=8)
            self._apply_axis_style(axes[1], ylabel="Selections")
            axes[1].tick_params(axis="x", rotation=25, labelsize=9)

        return self._save_figure(fig, "validation_snapshot.png")

    def plot_latest_pair_selection(self, result: ExperimentResult) -> Path | None:
        latest = self._latest_diagnostics(result)
        selected_pairs = latest.get("selected_pairs", [])
        if not selected_pairs:
            return None

        frame = pd.DataFrame(selected_pairs)
        score_column = "Adjusted_Rank_Score" if "Adjusted_Rank_Score" in frame.columns else "Rank_Score"
        if score_column not in frame.columns:
            return None

        labels = [f"{row['Ticker_1']}/{row['Ticker_2']}" for _, row in frame.iterrows()]
        scores = pd.to_numeric(frame[score_column], errors="coerce").fillna(0.0)

        fig, ax = plt.subplots(figsize=(12, max(4.0, len(labels) * 0.65)))
        fig.patch.set_facecolor("#f8fafc")
        bars = ax.barh(labels, scores, color="#16a34a")
        ax.set_title(f"Latest Fold Selected Pairs ({score_column})", loc="left", fontsize=16, color=self.colors["navy"], pad=10)
        self._apply_axis_style(ax, ylabel=None)
        ax.grid(True, axis="x", color="#dbe4f0", linewidth=0.8, alpha=0.7)
        for bar, value in zip(bars, scores):
            ax.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2, f"{value:.2f}", va="center", color=self.colors["navy"], fontsize=9)
        return self._save_figure(fig, "latest_pair_selection.png")

    def _build_summary_cards(self, result: ExperimentResult) -> str:
        latest = self._latest_diagnostics(result)
        broker = latest.get("broker", {}) if isinstance(latest, dict) else {}
        reconciliation = broker.get("reconciliation", {}) if isinstance(broker, dict) else {}

        cards = [
            ("Total Return", self._format_value("total_return", result.summary.get("total_return", 0.0)), "Net compounded return."),
            ("Annualized Return", self._format_value("annualized_return", result.summary.get("annualized_return", 0.0)), "CAGR-like annualization."),
            ("Sharpe", self._format_value("sharpe", result.summary.get("sharpe", 0.0)), "Net-return Sharpe ratio."),
            ("Max Drawdown", self._format_value("max_drawdown", result.summary.get("max_drawdown", 0.0)), "Worst peak-to-trough decline."),
            ("DSR", self._format_value("dsr", result.summary.get("dsr", result.validation.get("dsr") if result.validation else None)), "Deflated Sharpe probability."),
            ("PBO", self._format_value("pbo", result.summary.get("pbo", result.validation.get("pbo") if result.validation else None)), "Probability of backtest overfitting."),
            ("Broker Cost", self._format_value("total_broker_cost", reconciliation.get("total_broker_cost", 0.0)), "Execution-layer simulated cost."),
            ("Turnover", self._format_value("total_turnover", reconciliation.get("total_turnover", result.summary.get("avg_turnover", 0.0))), "Observed portfolio turnover."),
        ]
        return "".join(self._metric_card(label, value, caption) for label, value, caption in cards)

    def _build_fold_table(self, result: ExperimentResult) -> str:
        metrics = result.fold_metrics.copy()
        if metrics.empty:
            return "<p class='empty-state'>No fold metrics available.</p>"

        ordered_columns = [
            column
            for column in [
                "fold",
                "train_start",
                "train_end",
                "test_start",
                "test_end",
                "total_return",
                "annualized_return",
                "annualized_vol",
                "sharpe",
                "max_drawdown",
                "avg_turnover",
                "hit_rate",
            ]
            if column in metrics.columns
        ]
        return self._render_dataframe_table(metrics[ordered_columns], max_rows=20)

    def _build_diagnostics_sections(self, result: ExperimentResult) -> str:
        latest = self._latest_diagnostics(result)
        if not latest:
            return "<section class='panel'><h2>Diagnostics</h2><p class='empty-state'>No diagnostics available.</p></section>"

        broker = latest.get("broker", {}) if isinstance(latest, dict) else {}
        reconciliation = broker.get("reconciliation", {}) if isinstance(broker, dict) else {}
        strategy_summaries = latest.get("strategy_summaries", [])
        selected_pairs = latest.get("selected_pairs", [])

        sections = [
            "<section class='panel'>"
            "<div class='section-header'><div><p class='eyebrow'>Diagnostics</p><h2>Latest Fold Details</h2></div></div>"
            "<div class='two-column'>"
            f"<div><h3>Execution & Risk</h3>{self._render_key_value_table(reconciliation)}</div>"
            f"<div><h3>Strategy Metadata</h3>{self._render_key_value_table({k: v for k, v in latest.items() if k not in {'broker', 'strategy_summaries', 'selected_pairs'}})}</div>"
            "</div>"
            "</section>"
        ]

        if strategy_summaries:
            strategy_frame = pd.DataFrame(strategy_summaries)
            preferred = [
                column
                for column in ["strategy", "mean_abs_forecast", "mean_cost_estimate", "fallback_vol"]
                if column in strategy_frame.columns
            ]
            extra = [column for column in strategy_frame.columns if column not in preferred]
            sections.append(
                "<section class='panel'>"
                "<div class='section-header'><div><p class='eyebrow'>Composition</p><h2>Strategy Summaries</h2></div></div>"
                f"{self._render_dataframe_table(strategy_frame[preferred + extra], max_rows=20)}"
                "</section>"
            )

        if selected_pairs:
            selected_frame = pd.DataFrame(selected_pairs)
            sections.append(
                "<section class='panel'>"
                "<div class='section-header'><div><p class='eyebrow'>Selection</p><h2>Selected Pairs</h2></div></div>"
                f"{self._render_dataframe_table(selected_frame, max_rows=20)}"
                "</section>"
            )

        raw = json.dumps(json_ready(latest), indent=2)
        sections.append(
            "<section class='panel'>"
            "<div class='section-header'><div><p class='eyebrow'>Raw</p><h2>Diagnostics JSON</h2></div></div>"
            f"<details class='details'><summary>Expand raw diagnostics</summary><pre>{escape(raw)}</pre></details>"
            "</section>"
        )
        return "".join(sections)

    def _build_chart_gallery(self, generated: dict[str, Path]) -> str:
        cards = []
        for key, path in generated.items():
            if key == "report":
                continue
            title, description = CHART_DESCRIPTIONS.get(key, (self._display_name(key), ""))
            cards.append(
                "<article class='chart-card'>"
                f"<div class='chart-header'><h3>{escape(title)}</h3><p>{escape(description)}</p></div>"
                f"<img src='{escape(path.name)}' alt='{escape(title)}' />"
                "</article>"
            )
        return "".join(cards)

    def build_html_report(self, result: ExperimentResult, generated: dict[str, Path]) -> Path:
        frame = self._prepared_frame(result)
        validation = result.validation or {}

        metadata = {
            "experiment_id": result.experiment_id,
            "strategy": result.summary.get("strategy"),
            "folds": result.summary.get("folds"),
            "bars": len(frame),
            "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        }
        if not frame.empty:
            metadata["start"] = frame.index.min()
            metadata["end"] = frame.index.max()

        validation_table = {
            "psr": validation.get("psr", result.summary.get("psr")),
            "dsr": validation.get("dsr", result.summary.get("dsr")),
            "pbo": validation.get("pbo", result.summary.get("pbo")),
            "benchmark_sharpe": validation.get("benchmark_sharpe"),
            "trial_count": validation.get("trial_count", result.summary.get("validation_trial_count")),
            "sample_size": validation.get("sample_size"),
        }

        report_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(result.experiment_id)} Dashboard</title>
  <style>
    :root {{
      --bg: #f4f8fc;
      --panel: rgba(255, 255, 255, 0.94);
      --ink: #0f172a;
      --muted: #526277;
      --line: #dbe5f0;
      --navy: #0f172a;
      --blue: #155eef;
      --teal: #0f766e;
      --green: #15803d;
      --gold: #a16207;
      --danger: #b91c1c;
      --shadow: 0 14px 40px rgba(15, 23, 42, 0.08);
      --radius: 18px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Aptos", "Segoe UI", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(21, 94, 239, 0.12), transparent 28%),
        radial-gradient(circle at top right, rgba(15, 118, 110, 0.12), transparent 28%),
        linear-gradient(180deg, #f8fbff 0%, var(--bg) 50%, #edf4fb 100%);
    }}
    .page {{
      width: min(1440px, calc(100% - 48px));
      margin: 0 auto;
      padding: 28px 0 44px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.8fr) minmax(320px, 1fr);
      gap: 20px;
      align-items: stretch;
      margin-bottom: 22px;
    }}
    .hero-panel, .panel, .chart-card, .metric-card {{
      background: var(--panel);
      border: 1px solid rgba(219, 229, 240, 0.95);
      box-shadow: var(--shadow);
      border-radius: var(--radius);
    }}
    .hero-panel {{
      padding: 28px;
      position: relative;
      overflow: hidden;
    }}
    .hero-panel::after {{
      content: "";
      position: absolute;
      inset: auto -40px -70px auto;
      width: 220px;
      height: 220px;
      border-radius: 999px;
      background: linear-gradient(135deg, rgba(21, 94, 239, 0.18), rgba(15, 118, 110, 0.12));
      filter: blur(6px);
    }}
    .eyebrow {{
      margin: 0 0 8px;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      font-size: 0.72rem;
      color: var(--blue);
      font-weight: 700;
    }}
    h1, h2, h3 {{
      font-family: Georgia, "Times New Roman", serif;
      margin: 0;
      color: var(--navy);
    }}
    h1 {{ font-size: clamp(2rem, 3vw, 3rem); line-height: 1.05; margin-bottom: 10px; }}
    h2 {{ font-size: 1.45rem; margin-bottom: 14px; }}
    h3 {{ font-size: 1.0rem; margin-bottom: 12px; }}
    .lede {{
      margin: 0;
      max-width: 64ch;
      color: var(--muted);
      line-height: 1.6;
      font-size: 0.98rem;
    }}
    .hero-meta {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .hero-kpi {{
      padding: 18px;
      border-radius: 14px;
      background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(241,246,252,0.98));
      border: 1px solid var(--line);
    }}
    .hero-kpi .label {{
      margin: 0 0 6px;
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .hero-kpi .value {{
      margin: 0;
      font-size: 1.35rem;
      color: var(--navy);
      font-weight: 700;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-bottom: 22px;
    }}
    .metric-card {{
      padding: 18px 18px 16px;
    }}
    .metric-label {{
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .metric-value {{
      margin: 0;
      font-size: 1.5rem;
      color: var(--navy);
      font-weight: 700;
    }}
    .metric-caption {{
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 0.88rem;
      line-height: 1.45;
    }}
    .panel {{
      padding: 22px;
      margin-bottom: 22px;
    }}
    .section-header {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 16px;
    }}
    .section-header p {{
      margin: 0;
      color: var(--muted);
      max-width: 70ch;
    }}
    .two-column {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 18px;
    }}
    .chart-card {{
      padding: 18px;
      overflow: hidden;
    }}
    .chart-header {{
      margin-bottom: 12px;
    }}
    .chart-header h3 {{
      margin-bottom: 6px;
    }}
    .chart-header p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.5;
      font-size: 0.92rem;
    }}
    .chart-card img {{
      width: 100%;
      display: block;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: #ffffff;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #ffffff;
      border-radius: 14px;
      overflow: hidden;
    }}
    .key-value th, .key-value td, .data-table th, .data-table td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 0.92rem;
      vertical-align: top;
    }}
    .key-value th, .data-table th {{
      background: #f7fafc;
      color: var(--navy);
      font-weight: 700;
    }}
    .key-value td, .data-table td {{
      color: var(--muted);
    }}
    .key-value tbody tr:last-child td,
    .key-value tbody tr:last-child th,
    .data-table tbody tr:last-child td,
    .data-table tbody tr:last-child th {{
      border-bottom: 0;
    }}
    .table-shell {{
      overflow-x: auto;
      border-radius: 14px;
      border: 1px solid var(--line);
      background: #ffffff;
    }}
    .empty-state {{
      margin: 0;
      color: var(--muted);
      font-style: italic;
    }}
    .details {{
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #f8fbff;
      overflow: hidden;
    }}
    .details summary {{
      cursor: pointer;
      padding: 14px 16px;
      font-weight: 700;
      color: var(--navy);
    }}
    pre {{
      margin: 0;
      padding: 16px;
      overflow: auto;
      background: #0f172a;
      color: #e2e8f0;
      font-size: 0.86rem;
      line-height: 1.55;
    }}
    .footer {{
      margin-top: 24px;
      color: var(--muted);
      font-size: 0.9rem;
      text-align: center;
    }}
    @media (max-width: 920px) {{
      .page {{ width: min(100% - 24px, 1440px); }}
      .hero, .two-column {{ grid-template-columns: 1fr; }}
      .chart-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <article class="hero-panel">
        <p class="eyebrow">Quant Research Dashboard</p>
        <h1>{escape(result.experiment_id)}</h1>
        <p class="lede">
          Professional walk-forward report with validation, execution, risk, sleeve diagnostics, and portfolio composition.
          Use this dashboard to judge edge quality, implementation realism, and operational readiness together.
        </p>
      </article>
      <article class="hero-panel">
        <div class="section-header">
          <div>
            <p class="eyebrow">Run Metadata</p>
            <h2>Overview</h2>
          </div>
        </div>
        <div class="hero-meta">
          <div class="hero-kpi"><p class="label">Strategy</p><p class="value">{escape(self._format_value("strategy", metadata.get("strategy")))}</p></div>
          <div class="hero-kpi"><p class="label">Folds</p><p class="value">{escape(self._format_value("folds", metadata.get("folds")))}</p></div>
          <div class="hero-kpi"><p class="label">Start</p><p class="value">{escape(self._format_value("start", metadata.get("start")))}</p></div>
          <div class="hero-kpi"><p class="label">End</p><p class="value">{escape(self._format_value("end", metadata.get("end")))}</p></div>
        </div>
      </article>
    </section>

    <section class="metric-grid">
      {self._build_summary_cards(result)}
    </section>

    <section class="panel">
      <div class="section-header">
        <div>
          <p class="eyebrow">Charts</p>
          <h2>Performance, Risk, And Validation</h2>
          <p>Charts are generated directly from the artifact frame so the visuals and the saved parquet outputs stay aligned.</p>
        </div>
      </div>
      <div class="chart-grid">
        {self._build_chart_gallery(generated)}
      </div>
    </section>

    <section class="panel">
      <div class="section-header">
        <div>
          <p class="eyebrow">Validation</p>
          <h2>Statistical Checks</h2>
        </div>
      </div>
      <div class="two-column">
        <div>{self._render_key_value_table(validation_table)}</div>
        <div>{self._render_key_value_table(metadata)}</div>
      </div>
    </section>

    <section class="panel">
      <div class="section-header">
        <div>
          <p class="eyebrow">Walk-Forward</p>
          <h2>Fold Metrics</h2>
          <p>Review consistency across folds before trusting headline performance.</p>
        </div>
      </div>
      {self._build_fold_table(result)}
    </section>

    {self._build_diagnostics_sections(result)}

    <p class="footer">Generated from saved experiment artifacts at {escape(str(result.artifact_dir))}</p>
  </div>
</body>
</html>
"""

        report_path = self.output_dir / "report.html"
        report_path.write_text(report_html, encoding="utf-8")
        return report_path

    def create_dashboard(self, result: ExperimentResult) -> dict[str, Path]:
        generated: dict[str, Path] = {}

        for name, method in (
            ("equity_curve", self.plot_equity_curve),
            ("drawdown", self.plot_drawdown),
            ("exposure_costs", self.plot_exposure_and_costs),
            ("execution_diagnostics", self.plot_execution_diagnostics),
            ("fold_metrics", self.plot_fold_sharpe_and_return),
        ):
            generated[name] = method(result)

        for name, method in (
            ("strategy_weights", self.plot_strategy_weights),
            ("component_summary", self.plot_component_summary),
            ("sentiment_overlay", self.plot_sentiment_overlay),
            ("validation_snapshot", self.plot_validation_snapshot),
            ("latest_pair_selection", self.plot_latest_pair_selection),
        ):
            path = method(result)
            if path is not None:
                generated[name] = path

        generated["report"] = self.build_html_report(result, generated)
        return generated
