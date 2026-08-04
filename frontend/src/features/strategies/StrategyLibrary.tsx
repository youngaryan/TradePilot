import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { FlaskConical, Layers, Search } from "lucide-react";

import type { StrategyCatalogItem } from "../../api/types";
import {
  Button,
  Card,
  Chip,
  Disclosure,
  EmptyPanel,
  FilterBar,
  InlineNotice,
  LoadingBlock,
  SectionTitle,
  SelectInput,
  Tag,
  TextInput,
} from "../../ui";
import { formatNumber } from "../../utils/format";
import { STRATEGY_ORIGIN_META, riskTone, strategyOrigin, type StrategyOrigin } from "./strategyHelpers";

export interface StrategyLibraryProps {
  catalog: StrategyCatalogItem[] | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

/**
 * Strategy library.
 *
 * Grouped by provenance so a vetted built-in rule is never visually equivalent
 * to an unvalidated community listing. Selecting a strategy opens a detail panel
 * with how it works, what it is good for, what to watch out for, its parameters,
 * and a direct route into validation.
 */
export function StrategyLibrary({ catalog, loading, error, onRetry }: StrategyLibraryProps) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("all");
  const [origin, setOrigin] = useState<StrategyOrigin | "all">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const items = catalog ?? [];

  const families = useMemo(
    () => Array.from(new Set(items.map((item) => item.family).filter(Boolean))).sort(),
    [items],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((item) => {
      if (family !== "all" && item.family !== family) return false;
      if (origin !== "all" && strategyOrigin(item) !== origin) return false;
      if (!needle) return true;
      return (
        item.name.toLowerCase().includes(needle)
        || item.summary.toLowerCase().includes(needle)
        || item.family.toLowerCase().includes(needle)
        || (item.key_parameters ?? []).some((parameter) => parameter.toLowerCase().includes(needle))
      );
    });
  }, [items, query, family, origin]);

  const grouped = useMemo(
    () => STRATEGY_ORIGIN_META
      .map((group) => ({ ...group, items: filtered.filter((item) => strategyOrigin(item) === group.key) }))
      .filter((group) => group.items.length > 0),
    [filtered],
  );

  const selected = items.find((item) => item.id === selectedId) ?? null;

  if (loading) {
    return (
      <Card title="Strategy library">
        <LoadingBlock label="Loading the strategy library" lines={5} />
      </Card>
    );
  }

  if (error) {
    return (
      <InlineNotice
        tone="bad"
        title="Strategy library unavailable"
        actions={<Button variant="secondary" size="sm" onClick={onRetry}>Retry</Button>}
      >
        {error}
      </InlineNotice>
    );
  }

  if (items.length === 0) {
    return (
      <EmptyPanel
        icon={<Layers size={18} />}
        title="Nothing in the library yet"
        body="No strategies are available in this workspace."
        actions={
          <Button variant="primary" onClick={() => navigate("/strategies/builder")}>
            Describe a strategy
          </Button>
        }
      />
    );
  }

  return (
    <div className="ui-stack">
      <FilterBar label="Strategy filters">
        <TextInput
          label="Search"
          value={query}
          onChange={setQuery}
          placeholder="Name, summary, or parameter"
          type="search"
        />
        <SelectInput label="Category" value={family} onChange={setFamily}>
          <option value="all">All categories</option>
          {families.map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </SelectInput>
        <div className="ui-field">
          <span className="ui-field__label" id="origin-filter-label">Source</span>
          <div className="ui-chip-row" role="group" aria-labelledby="origin-filter-label">
            <Chip active={origin === "all"} onClick={() => setOrigin("all")}>All</Chip>
            {STRATEGY_ORIGIN_META.map((group) => (
              <Chip key={group.key} active={origin === group.key} onClick={() => setOrigin(group.key)} title={group.note}>
                {group.label.replace(" strategies", "")}
              </Chip>
            ))}
          </div>
        </div>
      </FilterBar>

      <p className="ui-card__subtitle">
        {formatNumber(filtered.length, 0)} of {formatNumber(items.length, 0)} strategies shown.
      </p>

      {filtered.length === 0 ? (
        <EmptyPanel
          icon={<Search size={18} />}
          title="No strategy matches these filters"
          body="Clear the search or widen the category and source filters."
          actions={
            <Button
              variant="secondary"
              onClick={() => {
                setQuery("");
                setFamily("all");
                setOrigin("all");
              }}
            >
              Clear filters
            </Button>
          }
        />
      ) : (
        grouped.map((group) => (
          <section key={group.key} aria-labelledby={`group-${group.key}`}>
            <SectionTitle title={group.label} id={`group-${group.key}`}>
              <span className="ui-card__subtitle">{group.note}</span>
            </SectionTitle>
            <div className="content-grid">
              {group.items.map((item) => (
                <article className="template-card" key={item.id}>
                  <div className="agent-card__header">
                    <strong>{item.name}</strong>
                    {item.risk_level ? (
                      <Tag tone={riskTone(item.risk_level)} title="Relative risk level assigned by the server">
                        {item.risk_level} risk
                      </Tag>
                    ) : (
                      <Tag tone="neutral" title="Difficulty">{item.difficulty || "Strategy"}</Tag>
                    )}
                  </div>
                  <p>{item.summary}</p>
                  <div className="badge-detail-tags">
                    <span className="badge-detail-tag">{item.family}</span>
                    {item.risk_level ? <span className="badge-detail-tag">{item.difficulty}</span> : null}
                    <span className="badge-detail-tag">
                      {(item.key_parameters?.length ?? 0)} parameter{(item.key_parameters?.length ?? 0) === 1 ? "" : "s"}
                    </span>
                    {item.generation_label ? <span className="badge-detail-tag badge-detail-tag--signal">{item.generation_label}</span> : null}
                  </div>
                  <div className="button-row--compact">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setSelectedId(selectedId === item.id ? null : item.id)}
                      aria-expanded={selectedId === item.id}
                    >
                      {selectedId === item.id ? "Hide details" : "Details"}
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon={<FlaskConical size={13} />}
                      onClick={() => navigate(`/backtests?strategy=${encodeURIComponent(item.pipeline)}`)}
                    >
                      Validate
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        ))
      )}

      {selected ? (
        <Card
          title={selected.name}
          subtitle={`${selected.family} · ${selected.difficulty || "difficulty not stated"}`}
          actions={
            <>
              <Button
                variant="primary"
                size="sm"
                icon={<FlaskConical size={13} />}
                onClick={() => navigate(`/backtests?strategy=${encodeURIComponent(selected.pipeline)}`)}
              >
                Validate with a backtest
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setSelectedId(null)}>Close</Button>
            </>
          }
        >
          <div className="explain-grid">
            <div className="hint-card">
              <strong>How it works</strong>
              <span>{selected.how_it_works}</span>
            </div>
            <div className="hint-card">
              <strong>Best for</strong>
              <span>{selected.best_for}</span>
            </div>
            <div className="hint-card">
              <strong>Watch out for</strong>
              <span>{selected.watch_out}</span>
            </div>
          </div>
          {selected.key_parameters?.length ? (
            <>
              <span className="eyebrow">Key parameters</span>
              <div className="ui-chip-row">
                {selected.key_parameters.map((parameter) => (
                  <span className="badge-detail-tag" key={parameter}><code>{parameter}</code></span>
                ))}
              </div>
            </>
          ) : null}
          <Disclosure summary="Example configuration used for defaults">
            <pre className="ui-code">{JSON.stringify(selected.paper_config_example ?? {}, null, 2)}</pre>
          </Disclosure>
          {selected.required_train_bars ? (
            <p className="ui-card__subtitle">
              Needs at least {formatNumber(selected.required_train_bars, 0)} training bars, so pick a date range long
              enough to cover the training window plus the out-of-sample folds.
            </p>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

export default StrategyLibrary;
