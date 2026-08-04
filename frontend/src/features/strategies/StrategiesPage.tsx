import { useNavigate } from "react-router";

import type { AccessContext } from "../../access/model";
import type { StrategyCatalogItem, WorkspacePayload } from "../../api/types";
import { Button, InlineNotice, PageHeader, Tabs } from "../../ui";
import { StrategyBuilder } from "./StrategyBuilder";
import { StrategyCommunity } from "./StrategyCommunity";
import { StrategyLibrary } from "./StrategyLibrary";

export type StrategyTab = "library" | "builder" | "community";

export interface StrategiesPageProps {
  tab: StrategyTab;
  access: AccessContext;
  activeOrgId: string | null;
  workspace: WorkspacePayload | null;
  catalog: StrategyCatalogItem[] | null;
  catalogLoading: boolean;
  catalogError: string | null;
  onCatalogChange: (catalog: StrategyCatalogItem[]) => void;
  onRefresh: () => void;
}

const TAB_PATH: Record<StrategyTab, string> = {
  library: "/strategies",
  builder: "/strategies/builder",
  community: "/strategies/community",
};

const TAB_DESCRIPTION: Record<StrategyTab, string> = {
  library:
    "Every strategy available to this workspace, grouped by where it came from. Open one to read its rules, parameters, and stated limitations before validating it.",
  builder:
    "Describe a rule in plain English. The builder asks clarifying questions, then produces a specification you review and approve before it enters the library.",
  community:
    "Strategies published by workspace members, ranked by their validation record rather than by simulated returns.",
};

/**
 * Strategy experience: discovery, authoring, and community publication in one
 * place, so moving from "an idea" to "something validated" is a single path.
 */
export function StrategiesPage({
  tab,
  access,
  activeOrgId,
  workspace,
  catalog,
  catalogLoading,
  catalogError,
  onCatalogChange,
  onRefresh,
}: StrategiesPageProps) {
  const navigate = useNavigate();

  return (
    <>
      <PageHeader
        eyebrow="Strategies"
        title="Strategy library and builder"
        description={TAB_DESCRIPTION[tab]}
        meta={
          <>
            <span>
              {catalog == null ? "Library not loaded" : `${catalog.length} available in this workspace`}
            </span>
            <span>
              Builder mode: {access.strategyBuilderMode === "llm" ? "AI-assisted" : "deterministic rules"}
            </span>
            <span>Marketplace: {access.marketplaceEnabled ? "enabled" : "disabled for this deployment"}</span>
          </>
        }
        actions={
          <Button variant="secondary" onClick={onRefresh}>Refresh library</Button>
        }
      />

      <Tabs
        label="Strategy sections"
        value={tab}
        onChange={(next) => navigate(TAB_PATH[next])}
        options={[
          { value: "library", label: "Library", count: catalog?.length ?? null },
          { value: "builder", label: "Builder" },
          { value: "community", label: "Community" },
        ]}
      />

      {tab === "library" ? (
        <StrategyLibrary
          catalog={catalog}
          loading={catalogLoading}
          error={catalogError}
          onRetry={onRefresh}
        />
      ) : null}

      {tab === "builder" ? (
        <StrategyBuilder
          activeOrgId={activeOrgId}
          capabilities={workspace?.capabilities ?? null}
          gate={access.useStrategyBuilder}
          onApproved={(item) => {
            onCatalogChange([...(catalog ?? []), item]);
            onRefresh();
          }}
        />
      ) : null}

      {tab === "community" ? (
        <StrategyCommunity
          activeOrgId={activeOrgId}
          marketplaceEnabled={access.marketplaceEnabled}
          publishGate={access.publishToMarketplace}
        />
      ) : null}

      <InlineNotice tone="info" compact>
        A strategy specification describes rules. It says nothing about whether those rules work — that is what the
        backtest is for, and a strong historical result can still be the product of overfitting.
      </InlineNotice>
    </>
  );
}

export default StrategiesPage;
