import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button, cx } from "./primitives";

export interface GridColumn<T> {
  key: string;
  header: ReactNode;
  /** Short label used when the row collapses to a stacked card on mobile. */
  label?: string;
  render: (row: T, index: number) => ReactNode;
  align?: "left" | "right";
  /** Hide this column below the tablet breakpoint. */
  secondary?: boolean;
}

/**
 * Financial data grid.
 *
 * - Numeric columns are right-aligned with tabular figures.
 * - A caption/summary gives the table an accessible description.
 * - `stackOnMobile` re-renders each row as a label/value card below 720px so
 *   dense tables stay comprehensible on a 390px viewport.
 */
export function DataGrid<T>({
  rows,
  columns,
  getKey,
  caption,
  summary,
  toolbar,
  footer,
  empty,
  stackOnMobile = true,
  className,
}: {
  rows: T[];
  columns: Array<GridColumn<T>>;
  getKey: (row: T, index: number) => string;
  /** Accessible name for the table. */
  caption: string;
  /** Optional visible description above the grid. */
  summary?: ReactNode;
  toolbar?: ReactNode;
  footer?: ReactNode;
  empty?: ReactNode;
  stackOnMobile?: boolean;
  className?: string;
}) {
  return (
    <div className={cx("ui-table-shell", className)}>
      {toolbar ? <div className="ui-table-toolbar">{toolbar}</div> : null}
      {rows.length === 0 ? (
        <div className="ui-table-note">{empty ?? "No rows to display."}</div>
      ) : (
        <>
          <div className="ui-table-scroll">
            <table className={cx("ui-table", stackOnMobile && "ui-table--stack")}>
              <caption className="ui-sr-only">{caption}</caption>
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th
                      key={column.key}
                      scope="col"
                      data-align={column.align === "right" ? "right" : undefined}
                    >
                      {column.header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={getKey(row, index)}>
                    {columns.map((column) => (
                      <td
                        key={column.key}
                        data-align={column.align === "right" ? "right" : undefined}
                        data-label={column.label ?? (typeof column.header === "string" ? column.header : column.key)}
                      >
                        {column.render(row, index)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {summary ? <div className="ui-table-note">{summary}</div> : null}
          {footer}
        </>
      )}
    </div>
  );
}

export function Pagination({ page, pageCount, onChange, itemLabel, total }: {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
  itemLabel: string;
  total?: number;
}) {
  if (pageCount <= 1) {
    return total != null ? (
      <div className="ui-pagination">
        <span>
          {total} {itemLabel}
        </span>
      </div>
    ) : null;
  }
  return (
    <nav className="ui-pagination" aria-label={`${itemLabel} pagination`}>
      <span>
        Page <span className="ui-num">{page}</span> of <span className="ui-num">{pageCount}</span>
        {total != null ? ` · ${total} ${itemLabel}` : null}
      </span>
      <div className="ui-pagination__controls">
        <Button
          size="sm"
          variant="secondary"
          icon={<ChevronLeft size={14} />}
          disabled={page <= 1}
          onClick={() => onChange(Math.max(1, page - 1))}
        >
          Previous
        </Button>
        <Button
          size="sm"
          variant="secondary"
          iconEnd={<ChevronRight size={14} />}
          disabled={page >= pageCount}
          onClick={() => onChange(Math.min(pageCount, page + 1))}
        >
          Next
        </Button>
      </div>
    </nav>
  );
}
