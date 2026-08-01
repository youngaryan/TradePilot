import type { PaperStrategy } from "../../api/types";
import { StatusBadge } from "../../components/StatusBadge";
import { StrategyDetail } from "./StrategyDetail";
import { pipelineLabel } from "./paperUtils";

function StrategySelector({
  strategies,
  selectedName,
  onSelect
}: {
  strategies: PaperStrategy[];
  selectedName: string | null;
  onSelect: (strategyName: string) => void;
}) {
  return (
    <aside className="strategy-rail" aria-label="Strategies">
      {strategies.map((strategy) => (
        <button
          key={strategy.name}
          type="button"
          className={strategy.name === selectedName ? "strategy-rail__item strategy-rail__item--active" : "strategy-rail__item"}
          onClick={() => onSelect(strategy.name)}
        >
          <span>
            <strong>{strategy.name}</strong>
            <small>{pipelineLabel(strategy.pipeline)}</small>
          </span>
          <StatusBadge label={strategy.mode} tone={strategy.mode === "synthetic" ? "warning" : "neutral"} />
        </button>
      ))}
    </aside>
  );
}

export function StrategiesPage({
  strategies,
  selectedStrategy,
  onSelect
}: {
  strategies: PaperStrategy[];
  selectedStrategy: PaperStrategy | null;
  onSelect: (strategyName: string) => void;
}) {
  return (
    <div className="split-view">
      <StrategySelector strategies={strategies} selectedName={selectedStrategy?.name ?? null} onSelect={onSelect} />
      {selectedStrategy ? <StrategyDetail strategy={selectedStrategy} /> : null}
    </div>
  );
}
