import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import {
  AlertTriangle,
  CheckCircle2,
  MessageSquare,
  Send,
  Sparkles,
} from "lucide-react";

import { approveStrategySpec, chatStrategyBuilder } from "../../api/client";
import type {
  StrategyBuilderMessage,
  StrategyBuilderResponse,
  StrategyCatalogItem,
  StrategySpec,
  WorkspacePayload,
} from "../../api/types";
import type { Gate } from "../../access/model";
import {
  AccessNotice,
  Button,
  Card,
  Disclosure,
  InlineNotice,
  SectionTitle,
  Tag,
  TextArea,
} from "../../ui";
import { boundedBuilderMessages } from "./strategyHelpers";

export interface StrategyBuilderProps {
  activeOrgId: string | null;
  capabilities: WorkspacePayload["capabilities"] | null;
  /** Whether the workspace may run the builder-backed approval workflow. */
  gate: Gate;
  onApproved: (item: StrategyCatalogItem) => void;
}

const EXAMPLE_PROMPTS = [
  "Go long SPY when the 50-day moving average is above the 200-day, and exit when it crosses back below.",
  "Buy QQQ when RSI(14) drops under 30 and sell when it recovers above 55. Cap any single position at 20% of the book.",
  "Rank SPY sector ETFs by 6-month momentum, hold the top three equally weighted, and rebalance monthly.",
];

const RISK_TONE = { low: "good", medium: "warn", high: "bad" } as const;

const DISPOSITION_TONE = {
  implemented: "good",
  normalized: "info",
  unsupported: "bad",
  missing: "warn",
} as const;

/**
 * Natural-language strategy builder.
 *
 * The conversation, the produced specification, the validation result, and the
 * approval step are separate regions so the user always knows whether they are
 * still describing an idea or looking at something the server has validated.
 * Nothing is added to the workspace catalog until the user explicitly approves.
 */
export function StrategyBuilder({ activeOrgId, capabilities, gate, onApproved }: StrategyBuilderProps) {
  const navigate = useNavigate();
  const [messages, setMessages] = useState<StrategyBuilderMessage[]>([]);
  const [input, setInput] = useState("");
  const [builderResp, setBuilderResp] = useState<StrategyBuilderResponse | null>(null);
  const [builderDraft, setBuilderDraft] = useState<StrategySpec | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approved, setApproved] = useState<StrategyCatalogItem | null>(null);
  const [specUpload, setSpecUpload] = useState("");
  const [specUploadBusy, setSpecUploadBusy] = useState(false);
  const [specUploadMessage, setSpecUploadMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const orgRef = useRef(activeOrgId);
  orgRef.current = activeOrgId;

  const isLlm = capabilities?.strategy_builder_mode === "llm";
  const generationLabel = isLlm ? "AI-assisted" : "rule-generated";
  const providerLabel = isLlm && capabilities?.strategy_builder_provider
    ? `${capabilities.strategy_builder_provider}${capabilities.strategy_builder_model ? ` · ${capabilities.strategy_builder_model}` : ""}`
    : "deterministic rules";

  // Switching workspace resets the conversation: a draft belongs to one tenant.
  useEffect(() => {
    setMessages([]);
    setBuilderResp(null);
    setBuilderDraft(null);
    setApproved(null);
    setError(null);
    setBusy(false);
    setSpecUpload("");
    setSpecUploadMessage(null);
  }, [activeOrgId]);

  const sendBuilderMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || busy) return;
    const nextMessages = boundedBuilderMessages(messages, { role: "user", content: text });
    setMessages(nextMessages);
    setInput("");
    setBusy(true);
    setError(null);
    setApproved(null);
    // Clear the previous turn's response first: stale clarification questions
    // must not appear to belong to the request that is still being evaluated.
    setBuilderResp(null);
    const orgAtStart = activeOrgId;
    try {
      const response = await chatStrategyBuilder(
        nextMessages,
        (builderDraft as unknown as Record<string, unknown> | null) ?? null,
      );
      if (orgRef.current !== orgAtStart) return;
      setBuilderResp(response);
      setBuilderDraft(response.draft_spec ?? (response.state === "needs_clarification" ? builderDraft : null));
      setMessages((previous) => boundedBuilderMessages(previous, { role: "assistant", content: response.assistant_message }));
    } catch (caught) {
      if (orgRef.current === orgAtStart) {
        setError(caught instanceof Error ? caught.message : "The strategy builder did not return a specification.");
      }
    } finally {
      if (orgRef.current === orgAtStart) setBusy(false);
    }
  }, [input, busy, messages, builderDraft, activeOrgId]);

  const approveBuilderDraft = useCallback(async () => {
    if (!builderDraft || busy) return;
    setBusy(true);
    setError(null);
    const orgAtStart = activeOrgId;
    try {
      const response = await approveStrategySpec(
        builderDraft as unknown as Record<string, unknown>,
        `Approved ${builderDraft.name} from the Meridian strategy builder.`,
        builderResp?.provenance_token,
      );
      if (orgRef.current !== orgAtStart) return;
      setApproved(response.catalog_item);
      onApproved(response.catalog_item);
      setMessages([]);
      setBuilderDraft(null);
      setBuilderResp(null);
    } catch (caught) {
      if (orgRef.current === orgAtStart) {
        setError(caught instanceof Error ? caught.message : "Approval failed.");
      }
    } finally {
      if (orgRef.current === orgAtStart) setBusy(false);
    }
  }, [builderDraft, busy, builderResp?.provenance_token, activeOrgId, onApproved]);

  const uploadSpec = useCallback(async () => {
    const raw = specUpload.trim();
    if (!raw || specUploadBusy) return;
    setSpecUploadBusy(true);
    setSpecUploadMessage(null);
    const orgAtStart = activeOrgId;
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const response = await approveStrategySpec(parsed, "Imported a strategy specification into this workspace.");
      if (orgRef.current !== orgAtStart) return;
      setSpecUploadMessage({
        ok: true,
        text: `Added “${response.catalog_item?.name ?? String(parsed.name ?? "strategy")}” to this workspace.`,
      });
      setSpecUpload("");
      onApproved(response.catalog_item);
    } catch (caught) {
      if (orgRef.current !== orgAtStart) return;
      setSpecUploadMessage({
        ok: false,
        text: caught instanceof SyntaxError
          ? "That is not valid JSON. Paste a complete strategy specification using schema strategy_spec/v1."
          : caught instanceof Error ? caught.message : "Import failed.",
      });
    } finally {
      if (orgRef.current === orgAtStart) setSpecUploadBusy(false);
    }
  }, [specUpload, specUploadBusy, activeOrgId, onApproved]);

  if (!gate.allowed && gate.reason) {
    return (
      <AccessNotice
        reason={gate.reason}
        feature="Strategy builder approval"
        whatItDoes="Turns a written description of a trading idea into a validated specification — indicators, entry and exit rules, position sizing, risk controls, and stated limitations — that can then be backtested."
        unlockedBy="Membership of a workspace. The builder itself is available on every plan, including free."
        alternative="Browse the strategy library and open any strategy to read its rules, parameters, and limitations."
      />
    );
  }

  const validation = builderResp?.validation;
  const intent = builderResp?.interpreted_intent;

  return (
    <div className="ui-stack">
      <div className="grid-two grid-two--wide-left">
        <section className="strategy-builder" aria-labelledby="builder-conversation">
          <SectionTitle title="Describe the idea" id="builder-conversation">
            <Tag tone={isLlm ? "brand" : "neutral"}>{generationLabel}</Tag>
          </SectionTitle>
          <p className="ui-card__subtitle">
            Write the rule in plain English. The builder asks for anything ambiguous, then returns a specification
            you can read and check before it is added to the workspace. Generation method: {providerLabel}.
          </p>

          <div className="strategy-builder__messages" aria-live="polite" aria-label="Builder conversation">
            {messages.length === 0 ? (
              <div>
                <strong>No conversation yet.</strong>
                <br />
                Start with a rule, a universe, and an exit condition. Example prompts are below.
              </div>
            ) : (
              messages.map((message, index) => (
                <div key={`${message.role}-${index}`}>
                  <span className="eyebrow">{message.role === "user" ? "You" : "Builder"}</span>
                  <br />
                  {message.content}
                </div>
              ))
            )}
          </div>

          <div className="strategy-builder__input">
            <TextArea
              label="Your description"
              value={input}
              onChange={setInput}
              rows={3}
              placeholder="Go long SPY when the 50-day average crosses above the 200-day…"
              hint="Press Ctrl+Enter to send."
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                  event.preventDefault();
                  void sendBuilderMessage();
                }
              }}
            />
            <Button
              variant="primary"
              icon={<Send size={14} />}
              disabled={busy || !input.trim()}
              onClick={() => void sendBuilderMessage()}
            >
              {busy ? (isLlm ? "Thinking…" : "Parsing…") : "Send"}
            </Button>
          </div>

          <div className="ui-chip-row">
            {EXAMPLE_PROMPTS.map((prompt, index) => (
              <button
                key={prompt}
                type="button"
                className="chip"
                onClick={() => setInput(prompt)}
                title={prompt}
              >
                <MessageSquare size={12} aria-hidden="true" />
                Example {index + 1}
              </button>
            ))}
          </div>

          {error ? <InlineNotice tone="bad" title="Builder request failed">{error}</InlineNotice> : null}

          {approved ? (
            <InlineNotice
              tone="good"
              title={`“${approved.name}” was added to this workspace`}
              actions={
                <Button variant="secondary" size="sm" onClick={() => navigate("/backtests")}>
                  Validate it with a backtest
                </Button>
              }
            >
              The strategy is now in the library and can be backtested. It has not been validated yet — a
              specification only describes the rules.
            </InlineNotice>
          ) : null}

          <p className="strategy-builder__disclaimer">
            The builder produces research configuration only. It does not place orders, and an approved
            specification is not a recommendation or an indication of future performance.
          </p>
        </section>

        <div className="ui-stack">
          {busy && !builderResp ? (
            <Card title="Working">
              <InlineNotice tone="neutral" role="status">
                {isLlm
                  ? "The builder is interpreting your description and drafting a specification."
                  : "The builder is parsing your description into deterministic rules."}
              </InlineNotice>
            </Card>
          ) : null}

          {!busy && !builderResp && !builderDraft ? (
            <Card title="What comes back" subtitle="Every response is reviewable before anything is saved.">
              <ol className="step-list">
                <li><strong style={{ color: "var(--text-primary)" }}>Clarifying questions</strong> if the description is
                  ambiguous — the builder will not guess a rule you did not state.</li>
                <li><strong style={{ color: "var(--text-primary)" }}>A requirement trace</strong> showing how each thing
                  you asked for was implemented, normalised, or refused.</li>
                <li><strong style={{ color: "var(--text-primary)" }}>A draft specification</strong> with indicators,
                  entry and exit rules, sizing, risk controls, and stated limitations.</li>
                <li><strong style={{ color: "var(--text-primary)" }}>Server-side validation</strong> against the strategy
                  schema and the execution engine, plus a pre-backtest risk analysis.</li>
                <li><strong style={{ color: "var(--text-primary)" }}>Your explicit approval</strong> — nothing enters the
                  library until you approve it, and approval does not validate performance.</li>
              </ol>
            </Card>
          ) : null}

          {builderResp?.questions?.length ? (
            <div className="strategy-builder__questions">
              <strong>Before a specification can be produced</strong>
              <ul style={{ margin: 0, paddingLeft: "18px" }}>
                {builderResp.questions.map((question) => (
                  <li key={question}>{question}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {validation ? (
            <Card title="Validation" subtitle="Checked by the server against the strategy schema and the execution engine.">
              <InlineNotice tone={validation.ok ? "good" : "bad"} compact>
                {validation.ok
                  ? "The specification is structurally valid and can be approved."
                  : "The specification cannot be approved until these problems are resolved."}
              </InlineNotice>
              {validation.errors.length ? (
                <ul className="warning-list">
                  {validation.errors.map((item) => (
                    <li key={item} className="warning-item-header" style={{ borderColor: "var(--negative-border)", background: "var(--negative-subtle)", color: "var(--negative-text)" }}>
                      <AlertTriangle size={13} aria-hidden="true" />
                      {item}
                    </li>
                  ))}
                </ul>
              ) : null}
              {validation.warnings.length ? (
                <ul className="warning-list warning-list--compact">
                  {validation.warnings.map((item) => (
                    <li key={item} className="warning-item-header">
                      <AlertTriangle size={13} aria-hidden="true" />
                      {item}
                    </li>
                  ))}
                </ul>
              ) : null}
            </Card>
          ) : null}

          {builderResp?.risk_analysis ? (
            <Card
              title="Risk analysis"
              subtitle="What is most likely to go wrong with this rule, and what to check first."
              actions={<Tag tone={RISK_TONE[builderResp.risk_analysis.overall_risk] ?? "warn"}>{builderResp.risk_analysis.overall_risk} risk</Tag>}
            >
              <p className="ui-card__subtitle">{builderResp.risk_analysis.overview}</p>
              {builderResp.risk_analysis.key_risks.length ? (
                <>
                  <span className="eyebrow">Key risks</span>
                  <ul className="principle-list">
                    {builderResp.risk_analysis.key_risks.map((risk) => <li key={risk}>{risk}</li>)}
                  </ul>
                </>
              ) : null}
              {builderResp.risk_analysis.validation_priorities.length ? (
                <Disclosure summary="What to validate first">
                  <ul className="principle-list">
                    {builderResp.risk_analysis.validation_priorities.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </Disclosure>
              ) : null}
              {builderResp.risk_analysis.mitigations.length ? (
                <Disclosure summary="Suggested mitigations">
                  <ul className="principle-list">
                    {builderResp.risk_analysis.mitigations.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </Disclosure>
              ) : null}
            </Card>
          ) : null}

          {intent ? (
            <Card title="How the AI interpreted this request" subtitle="Requirement-by-requirement, so nothing is silently dropped.">
              <p className="ui-card__subtitle">{intent.objective}</p>
              <div className="ui-table-scroll">
                <table className="ui-table ui-table--stack">
                  <caption className="ui-sr-only">Requirement trace: how each stated requirement was handled</caption>
                  <thead>
                    <tr>
                      <th scope="col">Requirement</th>
                      <th scope="col">Disposition</th>
                      <th scope="col">Handling</th>
                    </tr>
                  </thead>
                  <tbody>
                    {intent.requirement_trace.map((row) => (
                      <tr key={`${row.requirement}-${row.disposition}`}>
                        <td data-label="Requirement">{row.requirement}</td>
                        <td data-label="Disposition">
                          <Tag tone={DISPOSITION_TONE[row.disposition] ?? "neutral"}>{row.disposition}</Tag>
                        </td>
                        <td data-label="Handling">{row.handling}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {intent.unsupported_requirements.length ? (
                <InlineNotice tone="warn" title="Not supported by the engine" compact>
                  {intent.unsupported_requirements.join(" · ")}
                </InlineNotice>
              ) : null}
              {intent.missing_requirements.length ? (
                <InlineNotice tone="warn" title="Still missing" compact>
                  {intent.missing_requirements.join(" · ")}
                </InlineNotice>
              ) : null}
              <Disclosure summary="Assumptions and safe normalizations">
                <span className="eyebrow">Assumptions</span>
                <ul className="principle-list">
                  {intent.assumptions.length
                    ? intent.assumptions.map((item) => <li key={item}>{item}</li>)
                    : <li>None recorded.</li>}
                </ul>
                <span className="eyebrow">Safe normalizations</span>
                <ul className="principle-list">
                  {intent.safe_normalizations.length
                    ? intent.safe_normalizations.map((item) => <li key={item}>{item}</li>)
                    : <li>None recorded.</li>}
                </ul>
              </Disclosure>
            </Card>
          ) : null}
        </div>
      </div>

      {builderDraft ? (
        <StrategySpecReview
          draft={builderDraft}
          response={builderResp}
          busy={busy}
          onApprove={() => void approveBuilderDraft()}
        />
      ) : null}

      <Disclosure summary="Advanced — import a specification as JSON">
        <p>
          Paste a complete <code>strategy_spec/v1</code> document. It is validated by the same server-side
          approval endpoint used by the builder, so an invalid document is rejected rather than stored.
        </p>
        <TextArea
          label="Strategy specification (JSON)"
          value={specUpload}
          onChange={setSpecUpload}
          mono
          rows={8}
          placeholder='{"schema_version": "strategy_spec/v1", "name": "…"}'
        />
        {specUploadMessage ? (
          <InlineNotice tone={specUploadMessage.ok ? "good" : "bad"} compact>{specUploadMessage.text}</InlineNotice>
        ) : null}
        <Button variant="secondary" disabled={specUploadBusy || !specUpload.trim()} onClick={() => void uploadSpec()}>
          {specUploadBusy ? "Importing…" : "Validate and import"}
        </Button>
      </Disclosure>
    </div>
  );
}

function StrategySpecReview({ draft, response, busy, onApprove }: {
  draft: StrategySpec;
  response: StrategyBuilderResponse | null;
  busy: boolean;
  onApprove: () => void;
}) {
  const canApprove = response?.validation?.ok !== false && response?.state !== "rejected";
  return (
    <section className="strategy-spec-review" aria-labelledby="spec-review-heading">
      <div className="strategy-spec-review__top">
        <div>
          <span className="eyebrow">Draft specification</span>
          <h3 id="spec-review-heading" style={{ margin: "4px 0 0", fontSize: "var(--text-lg)" }}>{draft.name}</h3>
          <p className="ui-card__subtitle">{draft.summary}</p>
        </div>
        <div className="ui-btn-row">
          <Tag tone="neutral">{draft.side.replace(/_/g, " ")}</Tag>
          <Tag tone="neutral">{draft.timeframe}</Tag>
          <Button
            variant="primary"
            icon={<CheckCircle2 size={14} />}
            disabled={busy || !canApprove}
            onClick={onApprove}
          >
            {busy ? "Approving…" : "Approve and add to library"}
          </Button>
        </div>
      </div>

      {!canApprove ? (
        <InlineNotice tone="bad" compact>
          The server rejected this draft, so it cannot be approved. Refine the description and send it again.
        </InlineNotice>
      ) : null}

      <div className="strategy-spec-grid">
        <div className="hint-card">
          <strong>Universe</strong>
          <span>
            {draft.asset_universe.type}
            {draft.asset_universe.symbols.length ? `: ${draft.asset_universe.symbols.join(", ")}` : ""}
          </span>
        </div>
        <div className="hint-card">
          <strong>Entry logic</strong>
          <span>Requires {draft.entry_logic === "any" ? "any" : "all"} entry rule(s) to hold.</span>
        </div>
        <div className="hint-card">
          <strong>Exit logic</strong>
          <span>Requires {draft.exit_logic === "any" ? "any" : "all"} exit rule(s) to hold.</span>
        </div>
        <div className="hint-card">
          <strong>Indicators</strong>
          <span>
            {draft.required_indicators.length
              ? draft.required_indicators.map((indicator) => indicator.name).join(", ")
              : "None required"}
          </span>
        </div>
      </div>

      <div className="grid-two">
        <div className="ui-stack ui-stack--tight">
          <span className="eyebrow">Entry rules</span>
          <ul className="principle-list">
            {draft.entry_rules.length
              ? draft.entry_rules.map((rule, index) => (
                <li key={`${rule.kind}-${index}`}>
                  <strong style={{ color: "var(--text-primary)" }}>{rule.kind}</strong>
                  {rule.description ? ` — ${rule.description}` : ""}
                </li>
              ))
              : <li>No entry rules were produced.</li>}
          </ul>
        </div>
        <div className="ui-stack ui-stack--tight">
          <span className="eyebrow">Exit rules</span>
          <ul className="principle-list">
            {draft.exit_rules.length
              ? draft.exit_rules.map((rule, index) => (
                <li key={`${rule.kind}-${index}`}>
                  <strong style={{ color: "var(--text-primary)" }}>{rule.kind}</strong>
                  {rule.description ? ` — ${rule.description}` : ""}
                </li>
              ))
              : <li>No exit rules were produced.</li>}
          </ul>
        </div>
      </div>

      {draft.editable_parameters.length ? (
        <div className="ui-table-scroll">
          <table className="ui-table ui-table--stack">
            <caption className="ui-sr-only">Editable parameters with defaults and permitted ranges</caption>
            <thead>
              <tr>
                <th scope="col">Parameter</th>
                <th scope="col" data-align="right">Default</th>
                <th scope="col" data-align="right">Range</th>
                <th scope="col">Description</th>
              </tr>
            </thead>
            <tbody>
              {draft.editable_parameters.map((parameter) => (
                <tr key={parameter.name}>
                  <td data-label="Parameter"><code>{parameter.name}</code></td>
                  <td data-align="right" data-label="Default">{String(parameter.default ?? "—")}</td>
                  <td data-align="right" data-label="Range">
                    {parameter.min == null && parameter.max == null
                      ? "Unbounded"
                      : `${parameter.min ?? "−∞"} – ${parameter.max ?? "∞"}`}
                  </td>
                  <td data-label="Description">{parameter.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {draft.limitations.length || draft.assumptions.length ? (
        <div className="grid-two">
          {draft.assumptions.length ? (
            <Disclosure summary={`Assumptions (${draft.assumptions.length})`}>
              <ul className="principle-list">
                {draft.assumptions.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </Disclosure>
          ) : null}
          {draft.limitations.length ? (
            <Disclosure summary={`Stated limitations (${draft.limitations.length})`}>
              <ul className="principle-list">
                {draft.limitations.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </Disclosure>
          ) : null}
        </div>
      ) : null}

      <Disclosure summary="Raw specification (JSON)">
        <pre className="ui-code">{JSON.stringify(draft, null, 2)}</pre>
      </Disclosure>

      {response ? (
        <Disclosure summary="Generation provenance">
          <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "6px 16px", margin: 0 }}>
            <dt>Mode</dt>
            <dd style={{ margin: 0 }}>{response.generation_mode}</dd>
            <dt>Provider</dt>
            <dd style={{ margin: 0 }}>{response.provider}{response.model ? ` · ${response.model}` : ""}</dd>
            <dt>Prompt version</dt>
            <dd style={{ margin: 0 }}>{response.prompt_version}</dd>
            {response.generation_path ? (
              <>
                <dt>Generation path</dt>
                <dd style={{ margin: 0 }}>{response.generation_path}</dd>
              </>
            ) : null}
            {response.semantic_repair_count != null ? (
              <>
                <dt>Semantic repairs</dt>
                <dd style={{ margin: 0 }}>{response.semantic_repair_count}</dd>
              </>
            ) : null}
          </dl>
        </Disclosure>
      ) : null}

      <InlineNotice tone="info" compact>
        <Sparkles size={12} aria-hidden="true" style={{ verticalAlign: -2, marginRight: 4 }} />
        Approving adds the specification to this workspace's library. It still has to be validated with a
        backtest before it can be deployed to paper trading.
      </InlineNotice>
    </section>
  );
}

export default StrategyBuilder;
