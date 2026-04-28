import { Compass, Database, LineChart, Play, ShieldCheck, Terminal } from "lucide-react";

const tutorialCards = [
  {
    title: "1. Start the backend website",
    icon: <Terminal size={18} />,
    body:
      "The backend is the FastAPI service. It reads paper ledgers, runs paper batches, and exposes strategy documentation to the frontend.",
    command: ".\\.venv\\Scripts\\python.exe -m uvicorn pairs_trading.backend.app:app --reload --host 127.0.0.1 --port 8000",
    note: "Then open http://127.0.0.1:8000/docs to see the interactive API website."
  },
  {
    title: "2. Start the dashboard website",
    icon: <LineChart size={18} />,
    body:
      "The frontend is the operator console. It shows capital, live paper ledgers, latest simulated orders, diagnostics, tutorials, and the strategy catalog.",
    command: "cd frontend\nnpm.cmd run dev",
    note: "Open http://127.0.0.1:5173. The Vite server proxies /api to the backend."
  },
  {
    title: "3. Read the money panel first",
    icon: <Database size={18} />,
    body:
      "Total Equity is cash plus marked positions. Daily PnL is the change since the last paper run. Rebalance Cost PnL captures simulated slippage and commissions.",
    command: "",
    note: "If nothing moves, run a new batch or confirm the strategy has enough price history for the selected as-of date."
  },
  {
    title: "4. Launch a backtest agent",
    icon: <Play size={18} />,
    body:
      "The Backtests tab starts a backend job, returns a job id, polls status, and then shows summary, validation, visuals, and artifact paths.",
    command: "POST http://127.0.0.1:8000/api/backtests/run",
    note: "Use this for research experiments before adding anything to the paper deployment config."
  },
  {
    title: "5. Deploy fake-money agents",
    icon: <Play size={18} />,
    body:
      "The Live Trading tab lets you deploy several paper agents at once. Configure method, symbols, timeframe, date mode, execution costs, and optional sentiment/news settings before launching.",
    command: "POST http://127.0.0.1:8000/api/paper/run-job",
    note: "Use single date for today-style shadow execution, or date range replay to simulate multiple business days in sequence."
  },
  {
    title: "6. Inspect every strategy",
    icon: <Compass size={18} />,
    body:
      "Use the Strategies tab to drill into one sleeve. Target Weights show desired exposure; Positions show simulated holdings; Diagnostics show model internals.",
    command: "",
    note: "Synthetic stat-arb components are paper-priced internally so the ledger can still track capital through time."
  },
  {
    title: "7. Keep it experimental",
    icon: <ShieldCheck size={18} />,
    body:
      "This is still shadow trading. Treat results as research until execution, broker reconciliation, data quality, risk controls, and live incident handling are production-grade.",
    command: ".\\.venv\\Scripts\\python.exe -m unittest discover -s tests -v\ncd frontend\nnpm.cmd run build",
    note: "Run tests before changing strategies or deployment configs."
  }
];

const pageExplanations = [
  {
    name: "Overview",
    explanation: "Fast health check: total capital, equity/PnL charts, exposure, capital allocation, leaderboard, and selected strategy detail."
  },
  {
    name: "Live Trading",
    explanation: "Shadow paper execution control room. Configure multiple agents, symbols, timeframes, sentiment overlays, replay dates, fake broker costs, and job progress."
  },
  {
    name: "Strategies",
    explanation: "Detailed view for one strategy at a time, including target weights, open positions, and per-strategy capital history."
  },
  {
    name: "Orders",
    explanation: "Shows the latest simulated orders produced by the paper broker, including notional, commission, and execution price."
  },
  {
    name: "Diagnostics",
    explanation: "Raw model diagnostics and backend payloads for debugging selection, ranking, weights, and strategy metadata."
  },
  {
    name: "Backtests",
    explanation: "Interactive workbench for launching backtest agents, polling job status, and reviewing validation outputs."
  },
  {
    name: "Catalog",
    explanation: "Plain-English strategy explanations with CLI commands and paper deployment snippets."
  },
  {
    name: "Tutorials",
    explanation: "Step-by-step guide for starting the websites, interpreting the money, and safely operating the research app."
  }
];

const backtestTerms = [
  {
    name: "Agent template",
    explanation: "A saved starting point for a research run, including pipeline, symbols, dates, and sensible parameters."
  },
  {
    name: "Train bars",
    explanation: "Historical bars used to fit each walk-forward fold before the model is tested out-of-sample."
  },
  {
    name: "Test bars",
    explanation: "Out-of-sample bars used to evaluate the fitted fold. These bars should behave like unseen future data."
  },
  {
    name: "Purge bars",
    explanation: "A safety gap between train and test windows. It reduces leakage when signals or returns overlap through time."
  },
  {
    name: "DSR",
    explanation: "Deflated Sharpe Ratio. It is stricter than raw Sharpe because it accounts for multiple testing and noisy returns."
  },
  {
    name: "PBO",
    explanation: "Probability of Backtest Overfitting. Lower is better; high PBO means the best-looking setup may be curve-fit."
  },
  {
    name: "Decision panel",
    explanation: "A research gate that combines Sharpe, DSR, PBO, drawdown, turnover, and fold count. Passing means paper-test next, not real-money trading."
  },
  {
    name: "Artifacts",
    explanation: "Saved files under the backtest artifact directory: summary, validation, fold metrics, equity curve, diagnostics, and visual reports."
  }
];

const liveTradingTerms = [
  {
    name: "Shadow live",
    explanation: "A fake-money rehearsal. The app runs current signals and simulated execution, but it does not send orders to a real broker."
  },
  {
    name: "Run control",
    explanation: "The panel where you choose single-date or date-range replay, set fake broker assumptions, and launch the paper live job."
  },
  {
    name: "Agent deployment builder",
    explanation: "The area where each fake-money agent gets a method, symbols or sector map, timeframe, lookback, and strategy parameters."
  },
  {
    name: "Date range replay",
    explanation: "Runs every business day between the selected start and end dates, updating ledgers sequentially like a rehearsal of live operations."
  },
  {
    name: "Sentiment/news overlay",
    explanation: "Optional news settings passed to news-aware pipelines, including local daily sentiment files, provider names, news files, topics, and FinBERT scoring."
  },
  {
    name: "Deployment charts",
    explanation: "Charts that show configured methods, sentiment coverage, symbol coverage, fake broker assumptions, equity trail, exposure, order notional, and risk/return."
  },
  {
    name: "Execution progress",
    explanation: "Shows whether the backend is loading config, building signals, simulating orders, or saving ledgers."
  },
  {
    name: "Ledger",
    explanation: "A saved fake-money account per strategy with cash, positions, equity, latest orders, and history."
  },
  {
    name: "Latest orders",
    explanation: "The simulated broker orders from the most recent run. They include notional, quantity, commission, and execution price."
  }
];

const promotionSteps = [
  "Run the simplest benchmark first, usually buy_and_hold or ETF trend.",
  "Run the candidate strategy with conservative costs and purged validation.",
  "Review the equity curve, drawdown, DSR, PBO, turnover, and fold count.",
  "Change nearby parameters and rerun. A good strategy should not collapse immediately.",
  "Only then add it to the paper deployment config for fake-money shadow trading.",
  "Keep real-money broker integration separate until data, execution, reconciliation, and risk controls are production-grade."
];

export function Tutorials() {
  return (
    <div className="tutorial-shell">
      <section className="panel tutorial-hero">
        <p className="eyebrow">Operator Guide</p>
        <h2>How to use the websites</h2>
        <p>
          The app has two websites: the React dashboard for operating paper trading, and the FastAPI docs site for testing backend
          endpoints directly. Use the dashboard for daily work and the API docs when you want to inspect raw responses.
        </p>
      </section>

      <section className="tutorial-grid">
        {tutorialCards.map((card) => (
          <article key={card.title} className="tutorial-card">
            <div className="tutorial-card__icon">{card.icon}</div>
            <h3>{card.title}</h3>
            <p>{card.body}</p>
            {card.command ? <pre>{card.command}</pre> : null}
            <small>{card.note}</small>
          </article>
        ))}
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>What Each Page Means</h2>
          <span>{pageExplanations.length} pages</span>
        </div>
        <div className="page-guide">
          {pageExplanations.map((page) => (
            <div key={page.name} className="page-guide__row">
              <strong>{page.name}</strong>
              <span>{page.explanation}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>Backtest Agent Glossary</h2>
          <span>{backtestTerms.length} terms</span>
        </div>
        <div className="page-guide">
          {backtestTerms.map((term) => (
            <div key={term.name} className="page-guide__row">
              <strong>{term.name}</strong>
              <span>{term.explanation}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>Live Trading Glossary</h2>
          <span>{liveTradingTerms.length} terms</span>
        </div>
        <div className="page-guide">
          {liveTradingTerms.map((term) => (
            <div key={term.name} className="page-guide__row">
              <strong>{term.name}</strong>
              <span>{term.explanation}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <div className="panel__header">
          <h2>Safe Promotion Path</h2>
          <span>Research to paper</span>
        </div>
        <ol className="tutorial-steps">
          {promotionSteps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ol>
      </section>
    </div>
  );
}
