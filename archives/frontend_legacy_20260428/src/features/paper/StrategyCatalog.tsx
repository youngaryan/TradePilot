import { useMemo, useState } from "react";
import { BookOpen, Search } from "lucide-react";

import type { StrategyCatalogItem } from "../../api/types";
import { StatusBadge } from "../../components/StatusBadge";

const difficultyTone: Record<string, "positive" | "warning" | "negative" | "neutral"> = {
  Basic: "positive",
  Intermediate: "warning",
  Advanced: "negative"
};

function ExampleBlock({ label, children }: { label: string; children: string }) {
  return (
    <div className="example-block">
      <span>{label}</span>
      <code>{children}</code>
    </div>
  );
}

export function StrategyCatalog({ catalog }: { catalog: StrategyCatalogItem[] }) {
  const [query, setQuery] = useState("");
  const [family, setFamily] = useState("All");

  const families = useMemo(() => ["All", ...Array.from(new Set(catalog.map((item) => item.family))).sort()], [catalog]);
  const filteredCatalog = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return catalog.filter((item) => {
      const matchesFamily = family === "All" || item.family === family;
      const haystack = [item.name, item.id, item.summary, item.best_for, item.difficulty, item.family].join(" ").toLowerCase();
      return matchesFamily && (!normalizedQuery || haystack.includes(normalizedQuery));
    });
  }, [catalog, family, query]);

  return (
    <section className="catalog-shell">
      <div className="panel catalog-hero">
        <div>
          <p className="eyebrow">Research Library</p>
          <h2>Strategy catalog</h2>
          <p>
            Each strategy has a plain-English explanation, operational caveats, CLI example, and paper-trading config shape. Use
            this as the bridge between the website and the quant engine.
          </p>
        </div>
        <BookOpen size={44} />
      </div>

      <div className="catalog-toolbar">
        <label className="search-box">
          <Search size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search strategy, risk, or use case" />
        </label>
        <div className="filter-pills" aria-label="Strategy families">
          {families.map((item) => (
            <button
              key={item}
              type="button"
              className={item === family ? "filter-pill filter-pill--active" : "filter-pill"}
              onClick={() => setFamily(item)}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      <div className="catalog-grid">
        {filteredCatalog.map((item) => (
          <article key={item.id} className="strategy-card">
            <div className="strategy-card__topline">
              <span>{item.family}</span>
              <StatusBadge label={item.difficulty} tone={difficultyTone[item.difficulty] ?? "neutral"} />
            </div>
            <h3>{item.name}</h3>
            <p>{item.summary}</p>

            <dl className="strategy-explainer">
              <dt>How it works</dt>
              <dd>{item.how_it_works}</dd>
              <dt>Best for</dt>
              <dd>{item.best_for}</dd>
              <dt>Watch out</dt>
              <dd>{item.watch_out}</dd>
            </dl>

            <div className="parameter-list">
              {item.key_parameters.map((parameter) => (
                <code key={parameter}>{parameter}</code>
              ))}
            </div>

            <ExampleBlock label="CLI">{item.example_cli}</ExampleBlock>
            <ExampleBlock label="Paper config">{JSON.stringify(item.paper_config_example, null, 2)}</ExampleBlock>
          </article>
        ))}
      </div>

      {!filteredCatalog.length ? <div className="empty-state">No strategy matched that search.</div> : null}
    </section>
  );
}
