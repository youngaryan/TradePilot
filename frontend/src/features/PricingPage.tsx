import { useEffect, useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, CreditCard, LockKeyhole, ShieldCheck } from "lucide-react";

import { getBillingStatus, getPricing, openBillingPortal, startBillingCheckout, syncBillingSubscription } from "../api/client";
import type { BillingStatusPayload, PricingPayload, PricingPlan, WorkspacePayload } from "../api/types";
import { Badge } from "../components/Badge";
import { Explainer, MetricCard, Panel, SectionHeader } from "../components/Cards";
import { formatCurrency } from "../utils/format";

function planTone(plan: PricingPlan, currentPlan?: string | null) {
  if (currentPlan === plan.id || (currentPlan === "pro_trial" && plan.id === "pro")) return "good";
  return plan.recommended ? "warn" : "neutral";
}

export function PricingPage({
  workspace,
  reason,
  isAdminAccess,
  onRefresh
}: {
  workspace: WorkspacePayload | null;
  reason?: string | null;
  isAdminAccess?: boolean;
  onRefresh: () => Promise<void>;
}) {
  const [pricing, setPricing] = useState<PricingPayload | null>(null);
  const [billing, setBilling] = useState<BillingStatusPayload | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const [nextPricing, nextBilling] = await Promise.all([
        getPricing(),
        getBillingStatus().catch(() => null)
      ]);
      setPricing(nextPricing);
      setBilling(nextBilling);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load pricing.");
    }
  }

  useEffect(() => {
    void load();
  }, [workspace?.organization_id]);

  const subscription = billing?.subscription ?? workspace?.subscription ?? pricing?.subscription ?? null;
  const plans = useMemo(() => pricing?.plans ?? billing?.pricing ?? [], [pricing?.plans, billing?.pricing]);
  const isPremium = billing?.premium ?? (
    String(subscription?.status ?? "") === "active" &&
    String(subscription?.plan ?? "free") !== "free"
  );
  const hasAccess = Boolean(isAdminAccess || isPremium);
  const accessLabel = isAdminAccess || billing?.access === "admin" ? "Admin access" : isPremium ? "Premium active" : "Free access";
  const premiumDetail = isAdminAccess || billing?.access === "admin" ? "Unlocked by admin role" : "Checked server-side on premium APIs";

  async function handleCheckout(plan: PricingPlan) {
    if (!plan.premium) return;
    setIsBusy(true);
    setNotice(null);
    setError(null);
    try {
      const response = await startBillingCheckout({ plan: plan.id });
      const url = response.checkout_url;
      setNotice(response.message ?? `Checkout started for ${plan.name}.`);
      if (url) window.open(url, "_blank", "noopener,noreferrer");
      await onRefresh();
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start checkout.");
    } finally {
      setIsBusy(false);
    }
  }

  async function handlePortal() {
    setIsBusy(true);
    setNotice(null);
    setError(null);
    try {
      const response = await openBillingPortal(window.location.href);
      const url = response.portal_url;
      setNotice(response.message ?? "Subscription portal opened.");
      if (url) window.open(url, "_blank", "noopener,noreferrer");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open subscription portal.");
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <div className="pricing-page">
      <SectionHeader eyebrow="Pricing and Access" title="Choose the plan that unlocks the research workflow">
        <Badge label={accessLabel} tone={hasAccess ? "good" : "warn"} />
      </SectionHeader>

      {reason && !isAdminAccess ? (
        <section className="payment-wall-banner">
          <LockKeyhole size={20} />
          <div>
            <strong>Upgrade needed for this action</strong>
            <span>{reason}</span>
          </div>
        </section>
      ) : null}

      {notice ? <section className="alert-card alert-card--good"><CheckCircle2 size={18} /><span>{notice}</span></section> : null}
      {error ? <section className="alert-card"><LockKeyhole size={18} /><span>{error}</span></section> : null}

      <div className="metric-grid">
        <MetricCard label="Current plan" value={subscription?.plan ?? "free"} detail={subscription?.status ?? "No paid subscription"} icon={<CreditCard size={18} />} />
        <MetricCard label="Premium access" value={hasAccess ? "Enabled" : "Locked"} detail={premiumDetail} tone={hasAccess ? "good" : "warn"} icon={<ShieldCheck size={18} />} />
        <MetricCard label="Billing period" value={subscription?.current_period_end_utc ? "Synced" : "Not synced"} detail={subscription?.current_period_end_utc ?? "Stripe webhook has not set a period yet"} />
      </div>

      <div className="pricing-grid">
        {plans.map((plan) => (
          <article key={plan.id} className={plan.recommended ? "pricing-card pricing-card--featured" : "pricing-card"}>
            <div className="pricing-card__top">
              <Badge label={plan.recommended ? "Recommended" : plan.premium ? "Premium" : "Starter"} tone={planTone(plan, subscription?.plan)} />
              <span>{plan.premium ? "Paid tier" : "Free tier"}</span>
            </div>
            <h3>{plan.name}</h3>
            <strong>{plan.price_monthly === 0 ? "$0" : formatCurrency(plan.price_monthly).replace(".00", "")}<small>/month</small></strong>
            <p>{plan.description}</p>
            <ul>
              {plan.features.map((feature) => (
                <li key={feature}><CheckCircle2 size={16} />{feature}</li>
              ))}
            </ul>
            <button
              type="button"
              className={plan.premium ? "primary-button" : "secondary-button"}
              onClick={() => void handleCheckout(plan)}
              disabled={isBusy || !plan.premium || subscription?.plan === plan.id}
            >
              {subscription?.plan === plan.id ? "Current plan" : plan.cta}
              {plan.premium ? <ArrowRight size={16} /> : null}
            </button>
          </article>
        ))}
      </div>

      <Panel title="Manage billing" subtitle="Subscription status is read from the backend and premium APIs re-check it before running compute.">
        <div className="button-cluster">
          <button type="button" className="primary-button" onClick={() => void handlePortal()} disabled={isBusy}>
            <CreditCard size={16} />
            Manage subscription
          </button>
          <button type="button" className="ghost-button" onClick={() => void (async () => {
            setIsBusy(true);
            setError(null);
            setNotice(null);
            try {
              await syncBillingSubscription();
              setNotice("Billing subscription synced from Stripe.");
              await load();
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "Could not sync billing subscription.");
            } finally {
              setIsBusy(false);
            }
          })()} disabled={isBusy}>
            Sync billing status
          </button>
        </div>
        <Explainer
          icon={<ShieldCheck size={17} />}
          title="Why the payment wall is server-side"
          body="The frontend explains access, but the backend enforces it. Backtest, sentiment, paper-trading, and refresh run endpoints return payment_required unless the workspace has an active paid subscription or the current user has admin access."
        />
      </Panel>
    </div>
  );
}
