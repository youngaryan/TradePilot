import { MemoryRouter } from "react-router";

import type { WorkspacePayload } from "../api/types";
import { OverviewPage } from "./overview/OverviewPage";

/**
 * Compatibility module.
 *
 * The former single-file dashboard has been decomposed into the application
 * shell (`shell/AppShell`), the overview screen (`features/overview`), the
 * strategy experience (`features/strategies`), sentiment helpers
 * (`features/sentiment/sentimentData`), and shared job polling (`utils/jobs`).
 *
 * This module is retained so existing imports and tests keep resolving. It
 * renders the redesigned overview standalone — its own router is supplied so it
 * can still be mounted outside the application shell.
 */

export { JobPollingTimeoutError, pollJobUntilTerminal } from "../utils/jobs";
export {
  buildProductionSentimentRequest,
  buildSentimentNewsMatrix,
  sentimentDatasetAnchorDate,
  sentimentHeadlineKey,
  sentimentWindowCutoff,
} from "./sentiment/sentimentData";
export type { SentimentNewsWindow } from "./sentiment/sentimentData";
export { boundedBuilderMessages, catalogBacktestDefaults } from "./strategies/strategyHelpers";

export interface ApolloDashboardProps {
  /** Signed-in identity supplied by the authenticated application shell. */
  userName?: string;
  userInitials?: string;
  workspaceLabel?: string;
  organizations?: Array<{ id: string; name: string }>;
  activeOrgId?: string | null;
  onSwitchOrg?: (id: string) => void;
  /** undefined = do not show the badge; true/false = backend health. */
  backendOnline?: boolean;
  hasPremium?: boolean;
  onLogout?: () => void;
  userId?: string;
  userEmail?: string;
  userRole?: string;
  planName?: string;
  planStatus?: string | null;
  capabilities?: WorkspacePayload["capabilities"];
}

export function ApolloDashboard(props: ApolloDashboardProps = {}) {
  return (
    <MemoryRouter>
      <OverviewPage
        activeOrgId={props.activeOrgId ?? null}
        hasPremium={props.hasPremium}
        displayName={props.userName}
        workspaceLabel={props.workspaceLabel}
        backendOnline={props.backendOnline}
      />
    </MemoryRouter>
  );
}

export default ApolloDashboard;
