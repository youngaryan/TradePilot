import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Archive, Share2, Upload, UserCheck } from "lucide-react";

import {
  archiveMarketplaceListing,
  createMarketplaceListing,
  listBacktestJobs,
  listMarketplaceListings,
  listMarketplacePublications,
  listMarketplaceSubscriptions,
  listUserStrategies,
  publishMarketplaceListing,
  subscribeMarketplaceListing,
  unsubscribeMarketplaceListing,
} from "../../api/client";
import type {
  BacktestJob,
  MarketplaceListing,
  MarketplaceSubscription,
  UserStrategyRecord,
} from "../../api/types";
import type { Gate } from "../../access/model";
import {
  AccessNotice,
  Button,
  Card,
  ConfirmDialog,
  DataGrid,
  Disclosure,
  EmptyPanel,
  InlineNotice,
  LoadingBlock,
  SectionTitle,
  Tag,
  TextInput,
  type GridColumn,
} from "../../ui";
import { formatDateTime, formatNumber } from "../../utils/format";

export interface StrategyCommunityProps {
  activeOrgId: string | null;
  marketplaceEnabled: boolean;
  publishGate: Gate;
}

interface Robustness {
  runs: number;
  checksPassed: number;
  checksTotal: number;
  pbo: number | null;
  folds: number;
  score: number;
  label: string;
  tone: "good" | "warn" | "bad" | "neutral";
  publishable: boolean;
}

/**
 * Community strategies.
 *
 * A listing's credibility comes from its validation record, not its headline
 * return, so every row leads with checks passed, out-of-sample breadth, and the
 * overfitting estimate. Publishing is gated on a completed, validated run.
 */
export function StrategyCommunity({ activeOrgId, marketplaceEnabled, publishGate }: StrategyCommunityProps) {
  const [listings, setListings] = useState<MarketplaceListing[]>([]);
  const [publications, setPublications] = useState<MarketplaceListing[]>([]);
  const [subscriptions, setSubscriptions] = useState<MarketplaceSubscription[]>([]);
  const [userStrategies, setUserStrategies] = useState<UserStrategyRecord[]>([]);
  const [backtestJobs, setBacktestJobs] = useState<BacktestJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [publishTarget, setPublishTarget] = useState<UserStrategyRecord | null>(null);
  const [publishSummary, setPublishSummary] = useState("");
  const [archiveTarget, setArchiveTarget] = useState<MarketplaceListing | null>(null);
  const version = useRef(0);

  const reload = useCallback(async () => {
    const current = ++version.current;
    setLoading(true);
    const [strategiesResult, jobsResult, listingsResult, publicationsResult, subscriptionsResult] =
      await Promise.allSettled([
        listUserStrategies(),
        listBacktestJobs(),
        marketplaceEnabled ? listMarketplaceListings() : Promise.resolve([]),
        marketplaceEnabled ? listMarketplacePublications() : Promise.resolve([]),
        marketplaceEnabled ? listMarketplaceSubscriptions() : Promise.resolve([]),
      ]);
    if (current !== version.current) return;

    setUserStrategies(strategiesResult.status === "fulfilled" ? strategiesResult.value : []);
    setBacktestJobs(jobsResult.status === "fulfilled" ? jobsResult.value : []);
    setListings(listingsResult.status === "fulfilled" ? listingsResult.value : []);
    setPublications(publicationsResult.status === "fulfilled" ? publicationsResult.value : []);
    setSubscriptions(subscriptionsResult.status === "fulfilled" ? subscriptionsResult.value : []);

    const failed = [strategiesResult, listingsResult, publicationsResult, subscriptionsResult]
      .some((result) => result.status === "rejected");
    setError(failed ? "Some community data could not be loaded. Retry to restore the full view." : null);
    setLoading(false);
  }, [marketplaceEnabled]);

  useEffect(() => {
    version.current += 1;
    setListings([]);
    setPublications([]);
    setSubscriptions([]);
    setUserStrategies([]);
    void reload();
    return () => {
      version.current += 1;
    };
  }, [activeOrgId, reload]);

  const robustnessFor = useCallback((strategyId: string): Robustness => {
    const pipeline = `user_strategy:${strategyId}`;
    const runs = backtestJobs.filter(
      (job) => job.status === "completed" && (job.request as { pipeline?: string })?.pipeline === pipeline,
    );
    const best = runs
      .map((job) => {
        const summary = (job.result?.summary ?? {}) as Record<string, unknown>;
        const decision = job.result?.decision;
        return {
          checksPassed: decision?.passed_checks ?? 0,
          checksTotal: decision?.total_checks ?? 0,
          pbo: typeof summary.pbo === "number" ? summary.pbo * 100 : null,
          folds: typeof summary.folds === "number" ? summary.folds : 0,
        };
      })
      .sort((a, b) => b.checksPassed - a.checksPassed)[0];

    if (!best) {
      return { runs: 0, checksPassed: 0, checksTotal: 0, pbo: null, folds: 0, score: 0, label: "Unvalidated", tone: "neutral", publishable: false };
    }
    const checkRatio = best.checksTotal ? best.checksPassed / best.checksTotal : 0;
    const foldCredit = Math.min(1, best.folds / 12);
    const pboPenalty = best.pbo == null ? 0.25 : Math.min(1, best.pbo / 100);
    const score = Math.round(Math.max(0, checkRatio * 60 + foldCredit * 25 + (1 - pboPenalty) * 15));
    return {
      runs: runs.length,
      checksPassed: best.checksPassed,
      checksTotal: best.checksTotal,
      pbo: best.pbo,
      folds: best.folds,
      score,
      label: score >= 70 ? "Robust" : score >= 45 ? "Moderate" : "Weak evidence",
      tone: score >= 70 ? "good" : score >= 45 ? "warn" : "bad",
      publishable: best.checksTotal > 0 && checkRatio >= 0.5 && best.folds >= 3,
    };
  }, [backtestJobs]);

  const publicationBySource = useMemo(
    () => new Map(publications.filter((listing) => listing.source_strategy_id).map((listing) => [listing.source_strategy_id as string, listing])),
    [publications],
  );
  const subscriptionByListing = useMemo(
    () => new Map(subscriptions.map((subscription) => [subscription.listing_id, subscription])),
    [subscriptions],
  );

  const run = useCallback(async (key: string, operation: () => Promise<unknown>) => {
    setBusyKey(key);
    setError(null);
    try {
      await operation();
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The marketplace operation failed.");
    } finally {
      setBusyKey(null);
    }
  }, [reload]);

  if (!marketplaceEnabled) {
    return (
      <AccessNotice
        reason="configuration"
        feature="Community strategy marketplace"
        whatItDoes="Lets workspace members publish a validated strategy specification for other workspaces to subscribe to, with the validation evidence attached to the listing."
        unlockedBy="The marketplace capability being enabled for this deployment by an administrator."
        alternative="Workspace strategies you author are still available in the library and can be exported as JSON from the builder."
      />
    );
  }

  const ranked = [...userStrategies]
    .map((strategy) => ({ strategy, robustness: robustnessFor(strategy.id) }))
    .sort((a, b) => b.robustness.score - a.robustness.score || a.strategy.name.localeCompare(b.strategy.name));

  const listingColumns: Array<GridColumn<MarketplaceListing>> = [
    {
      key: "title",
      header: "Listing",
      render: (listing) => (
        <span className="stacked-cell">
          <strong>{listing.title}</strong>
          <span>{listing.summary}</span>
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (listing) => (
        <Tag tone={listing.status === "published" ? "good" : listing.status === "archived" ? "neutral" : "warn"}>
          {listing.status}
        </Tag>
      ),
    },
    {
      key: "risk",
      header: "Risk",
      render: (listing) => listing.risk_level ?? "Not stated",
    },
    {
      key: "validation",
      header: "Validation",
      render: (listing) => (
        <span className="stacked-cell">
          <span>{listing.validation_summary.validated ? "Validated" : "Not validated"}</span>
          <span>
            {listing.validation_summary.warning_count} warning
            {listing.validation_summary.warning_count === 1 ? "" : "s"}
            {listing.validation_summary.dry_run_status ? ` · dry run ${listing.validation_summary.dry_run_status}` : ""}
          </span>
        </span>
      ),
    },
    {
      key: "action",
      header: "Subscription",
      render: (listing) => {
        const subscription = subscriptionByListing.get(listing.id);
        const isActive = subscription?.status === "active";
        return (
          <Button
            variant={isActive ? "secondary" : "primary"}
            size="sm"
            icon={<UserCheck size={13} />}
            disabled={busyKey === listing.id}
            onClick={() => void run(listing.id, () => (
              isActive ? unsubscribeMarketplaceListing(listing.id) : subscribeMarketplaceListing(listing.id)
            ))}
          >
            {busyKey === listing.id ? "Working…" : isActive ? "Unsubscribe" : "Subscribe"}
          </Button>
        );
      },
    },
  ];

  return (
    <div className="ui-stack">
      {error ? (
        <InlineNotice tone="warn" title="Partial community data" actions={<Button variant="secondary" size="sm" onClick={() => void reload()}>Retry</Button>}>
          {error}
        </InlineNotice>
      ) : null}

      <section aria-labelledby="community-listings">
        <SectionTitle title="Published listings" id="community-listings">
          <span className="ui-card__subtitle">Subscribing pins a specific listing version, so an upstream change cannot alter a running strategy.</span>
        </SectionTitle>
        {loading ? (
          <LoadingBlock label="Loading marketplace listings" lines={4} />
        ) : listings.length === 0 ? (
          <EmptyPanel
            icon={<Share2 size={18} />}
            title="No listings published yet"
            body="Nothing has been published to the marketplace for this deployment. Validated workspace strategies can be published below."
          />
        ) : (
          <DataGrid
            rows={listings}
            columns={listingColumns}
            caption="Marketplace listings with status, risk level, validation summary, and subscription action"
            getKey={(listing) => listing.id}
          />
        )}
      </section>

      <section aria-labelledby="community-mine">
        <SectionTitle title="Your workspace strategies" id="community-mine">
          <span className="ui-card__subtitle">Ranked by validation evidence, not by simulated returns.</span>
        </SectionTitle>
        {loading ? (
          <LoadingBlock label="Loading workspace strategies" lines={3} />
        ) : ranked.length === 0 ? (
          <EmptyPanel
            icon={<Upload size={18} />}
            title="No workspace strategies yet"
            body="Strategies you approve in the builder appear here, along with the validation record that decides whether they can be published."
          />
        ) : (
          <DataGrid
            rows={ranked}
            columns={[
              {
                key: "name",
                header: "Strategy",
                render: ({ strategy }) => (
                  <span className="stacked-cell">
                    <strong>{strategy.name}</strong>
                    <span>Version {strategy.version} · {strategy.status} · {strategy.risk_level} risk</span>
                  </span>
                ),
              },
              {
                key: "evidence",
                header: "Validation evidence",
                render: ({ robustness }) => (
                  <span className="stacked-cell">
                    <Tag tone={robustness.tone}>{robustness.label}</Tag>
                    <span>
                      {robustness.checksTotal
                        ? `${robustness.checksPassed}/${robustness.checksTotal} checks · ${robustness.folds} folds`
                        : "No completed backtest"}
                      {robustness.pbo != null ? ` · PBO ${Math.round(robustness.pbo)}%` : ""}
                    </span>
                  </span>
                ),
              },
              {
                key: "runs",
                header: "Backtests",
                align: "right",
                render: ({ robustness }) => formatNumber(robustness.runs, 0),
              },
              {
                key: "updated",
                header: "Updated",
                align: "right",
                render: ({ strategy }) => formatDateTime(strategy.updated_at_utc),
              },
              {
                key: "publish",
                header: "Marketplace",
                render: ({ strategy, robustness }) => {
                  const listing = publicationBySource.get(strategy.id);
                  if (listing) {
                    return (
                      <span className="button-row--compact">
                        <Tag tone={listing.status === "published" ? "good" : "neutral"}>{listing.status}</Tag>
                        {listing.status === "draft" ? (
                          <Button
                            variant="primary"
                            size="sm"
                            disabled={busyKey === listing.id || !publishGate.allowed}
                            onClick={() => void run(listing.id, () => publishMarketplaceListing(listing.id))}
                          >
                            Publish
                          </Button>
                        ) : null}
                        {listing.status !== "archived" ? (
                          <Button
                            variant="ghost"
                            size="sm"
                            icon={<Archive size={13} />}
                            disabled={busyKey === listing.id}
                            onClick={() => setArchiveTarget(listing)}
                          >
                            Archive
                          </Button>
                        ) : null}
                      </span>
                    );
                  }
                  if (!robustness.publishable) {
                    return (
                      <span className="ui-table__muted">
                        Needs a completed backtest with a majority of checks passed and at least 3 out-of-sample folds.
                      </span>
                    );
                  }
                  return (
                    <Button
                      variant="secondary"
                      size="sm"
                      icon={<Share2 size={13} />}
                      disabled={!publishGate.allowed}
                      onClick={() => {
                        setPublishTarget(strategy);
                        setPublishSummary(strategy.spec?.summary ?? "");
                      }}
                    >
                      Create listing
                    </Button>
                  );
                },
              },
            ]}
            caption="Workspace strategies with validation evidence, backtest count, and marketplace status"
            getKey={({ strategy }) => strategy.id}
            summary="Robustness weights checks passed, out-of-sample fold breadth, and the overfitting estimate. It is a validation summary, not a performance forecast."
          />
        )}
        {!publishGate.allowed && publishGate.reason ? (
          <InlineNotice tone="warn" compact>
Publishing is unavailable: {publishGate.reason === "configuration"
              ? "the marketplace capability is disabled for this deployment."
              : "you need to be a member of this workspace."} Your strategies and their validation records remain available.
          </InlineNotice>
        ) : null}
      </section>

      {subscriptions.length ? (
        <Disclosure summary={`Your subscriptions (${subscriptions.length})`}>
          <ul className="principle-list">
            {subscriptions.map((subscription) => (
              <li key={subscription.id}>
                <strong style={{ color: "var(--text-primary)" }}>{subscription.listing_title ?? subscription.listing_id}</strong>
                {" · "}
                {subscription.status}
                {subscription.version != null ? ` · pinned to version ${subscription.version}` : ""}
                {subscription.execution_access === false ? " · execution access not granted" : ""}
              </li>
            ))}
          </ul>
        </Disclosure>
      ) : null}

      <Card title="How publishing works" inset>
        <ul className="principle-list">
          <li>A listing carries the validation record of the run behind it, so subscribers can judge the evidence.</li>
          <li>Subscriptions pin a version. Publishing a new version never changes what an existing subscriber runs.</li>
          <li>Archiving stops new subscriptions. Existing pinned subscriptions are unaffected.</li>
          <li>Nothing published here places orders. Subscribers still have to validate and deploy it themselves.</li>
        </ul>
      </Card>

      <ConfirmDialog
        open={publishTarget != null}
        onClose={() => setPublishTarget(null)}
        title={`Create a listing for “${publishTarget?.name ?? ""}”`}
        confirmLabel="Create listing"
        busy={busyKey === "create"}
        body={
          <>
            <p>
              A draft listing is created first. It is only visible to other workspaces once you publish it, and it
              carries this strategy's validation record.
            </p>
            <TextInput
              label="Listing summary"
              value={publishSummary}
              onChange={setPublishSummary}
              hint="Describe what the strategy does and what it was validated on."
            />
          </>
        }
        onConfirm={() => {
          const target = publishTarget;
          if (!target) return;
          setPublishTarget(null);
          void run("create", () => createMarketplaceListing({
            source_strategy_id: target.id,
            title: target.name,
            summary: publishSummary.trim() || target.spec?.summary || target.name,
          }));
        }}
      />

      <ConfirmDialog
        open={archiveTarget != null}
        onClose={() => setArchiveTarget(null)}
        title={`Archive “${archiveTarget?.title ?? ""}”?`}
        confirmLabel="Archive listing"
        destructive
        busy={busyKey === archiveTarget?.id}
        body={
          <p>
            Archiving removes the listing from the marketplace and prevents new subscriptions. Workspaces that have
            already pinned a version keep running it. This cannot be undone from this screen.
          </p>
        }
        onConfirm={() => {
          const target = archiveTarget;
          if (!target) return;
          setArchiveTarget(null);
          void run(target.id, () => archiveMarketplaceListing(target.id));
        }}
      />
    </div>
  );
}

export default StrategyCommunity;
