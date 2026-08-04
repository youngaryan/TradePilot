/**
 * Meridian design system — public surface.
 *
 * Screens import from here rather than from individual files so the component
 * inventory stays discoverable and swappable.
 */
export {
  Button,
  IconButton,
  Tag,
  StatusIndicator,
  Chip,
  SegmentedControl,
  Tabs,
  cx,
} from "./primitives";
export type { ButtonProps, ButtonVariant, IconButtonProps, SegmentOption, TabOption, Tone } from "./primitives";

export { Field, TextInput, TextArea, SelectInput, CheckboxInput, SwitchInput, FilterBar } from "./forms";

export { PageHeader, SectionTitle, Card, MetricGrid, Metric, Explain, Disclosure, Stack, Split } from "./layout";

export {
  InlineNotice,
  EmptyPanel,
  SkeletonBlock,
  LoadingBlock,
  ProgressBar,
  JobState,
  jobPhaseFor,
  AccessNotice,
  GateHint,
  ErrorBoundary,
} from "./feedback";
export type { JobPhase } from "./feedback";

export { Dialog, ConfirmDialog, Drawer, useDismissable } from "./overlays";

export { DataGrid, Pagination } from "./datagrid";
export type { GridColumn } from "./datagrid";

export { BrandMark, BrandWord } from "./brand";
