import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, Layers, RefreshCw, Search } from "lucide-react";

import { getStockUniverse, getUniverseGroups } from "../api/client";
import type { StockUniverseItem, StockUniverseResponse } from "../api/types";
import { Panel } from "../components/Cards";

interface StockUniversePanelProps {
  selectedTickers: string[];
  onSelectionChange: (tickers: string[]) => void;
  pairMode: boolean;
  onPairChange: (pair: string) => void;
}

export function StockUniversePanel({ selectedTickers, onSelectionChange, pairMode, onPairChange }: StockUniversePanelProps) {
  const [universe, setUniverse] = useState<StockUniverseResponse | null>(null);
  const [groups, setGroups] = useState<{ sectors: Array<{ name: string; count: number }>; countries: Array<{ name: string; count: number }>; exchanges: Array<{ name: string; count: number }> } | null>(null);
  const [activeGroup, setActiveGroup] = useState<"sector" | "country" | "exchange">("sector");
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadUniverse() {
    setLoading(true);
    setError(null);
    try {
      const [u, g] = await Promise.all([getStockUniverse(), getUniverseGroups()]);
      setUniverse(u);
      setGroups(g);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to load stock universe.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void loadUniverse(); }, []);

  const groupsData = useMemo(() => {
    if (!groups) return [];
    if (activeGroup === "sector") return groups.sectors;
    if (activeGroup === "country") return groups.countries;
    return groups.exchanges;
  }, [groups, activeGroup]);

  const filteredStocks = useMemo(() => {
    if (!universe) return [];
    if (!expandedGroup) return [];
    let stocks: StockUniverseItem[];
    if (activeGroup === "sector") stocks = universe.stocks.filter((s) => s.sector === expandedGroup);
    else if (activeGroup === "country") stocks = universe.stocks.filter((s) => s.country === expandedGroup);
    else stocks = universe.stocks.filter((s) => s.exchange === expandedGroup);

    if (searchTerm) {
      const term = searchTerm.toUpperCase();
      stocks = stocks.filter((s) => s.ticker.includes(term) || s.company_name.toUpperCase().includes(term));
    }
    return stocks;
  }, [universe, activeGroup, expandedGroup, searchTerm]);

  function toggleTicker(ticker: string) {
    if (pairMode) {
      if (selectedTickers.includes(ticker)) {
        const next = selectedTickers.filter((item) => item !== ticker);
        onSelectionChange(next);
        onPairChange(next.length === 2 ? `${next[0]},${next[1]}` : "");
      } else if (selectedTickers.length === 0) {
        onSelectionChange([ticker]);
        onPairChange("");
      } else if (selectedTickers.length === 1) {
        const next = [selectedTickers[0], ticker];
        onSelectionChange(next);
        onPairChange(`${next[0]},${next[1]}`);
      } else {
        const next = [selectedTickers[0], ticker];
        onSelectionChange(next);
        onPairChange(`${next[0]},${next[1]}`);
      }
    } else {
      const next = selectedTickers.includes(ticker)
        ? selectedTickers.filter((t) => t !== ticker)
        : [...selectedTickers, ticker];
      onSelectionChange(next);
    }
  }

  return (
    <Panel title="Stock Universe" subtitle={`${universe?.total_stocks ?? 0} stocks across global markets`}>
      {error ? (
        <div className="inline-error"><AlertTriangle size={16} />{error}</div>
      ) : null}
      <div className="button-row">
        <div className="group-tabs">
          {(["sector", "country", "exchange"] as const).map((g) => (
            <button
              key={g}
              type="button"
              className={`ghost-button ${activeGroup === g ? "ghost-button--active" : ""}`}
              onClick={() => { setActiveGroup(g); setExpandedGroup(null); }}
            >
              <Layers size={14} />
              {g.charAt(0).toUpperCase() + g.slice(1)}
            </button>
          ))}
        </div>
        <button type="button" className="ghost-button" onClick={() => void loadUniverse()} disabled={loading}>
          <RefreshCw size={14} />
        </button>
      </div>

      <div className="universe-group-list">
        {groupsData.map((g) => (
          <div key={g.name} className={`universe-group ${expandedGroup === g.name ? "universe-group--expanded" : ""}`}>
            <button
              type="button"
              className="universe-group-header"
              onClick={() => setExpandedGroup(expandedGroup === g.name ? null : g.name)}
            >
              <span><strong>{g.name}</strong> <span className="universe-group-count">({g.count})</span></span>
              <ChevronDown size={14} className={`chevron ${expandedGroup === g.name ? "chevron--open" : ""}`} />
            </button>
            {expandedGroup === g.name ? (
              <div className="universe-group-content">
                <div className="search-row">
                  <Search size={14} />
                  <input
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    placeholder="Search ticker or name..."
                  />
                </div>
                <div className="universe-stock-list">
                  {filteredStocks.slice(0, 50).map((s) => (
                    <label key={s.ticker} className={`universe-stock-item ${selectedTickers.includes(s.ticker) ? "universe-stock-item--selected" : ""}`}>
                      <input
                        type="checkbox"
                        name={pairMode ? "pair-ticker" : "ticker-select"}
                        checked={selectedTickers.includes(s.ticker)}
                        onChange={() => toggleTicker(s.ticker)}
                      />
                      <div className="universe-stock-info">
                        <strong>{s.ticker}</strong>
                        <span>{s.company_name || s.sector}</span>
                        <small>{s.country} / {s.exchange} / {s.currency}{s.market_cap_category ? ` / ${s.market_cap_category}` : ""}</small>
                      </div>
                    </label>
                  ))}
                  {filteredStocks.length > 50 ? (
                    <div className="empty-state">Showing 50 of {filteredStocks.length} stocks - refine search</div>
                  ) : null}
                  {filteredStocks.length === 0 ? (
                    <div className="empty-state">No stocks match this filter</div>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        ))}
      </div>

      {selectedTickers.length > 0 ? (
        <div className="selected-tickers-bar">
          <strong>{pairMode ? "Pair:" : "Selected:"}</strong>
          <div className="selected-ticker-tags">
            {selectedTickers.map((t) => (
              <span key={t} className="ticker-tag">
                {t}
                <button type="button" onClick={() => toggleTicker(t)}>x</button>
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </Panel>
  );
}
