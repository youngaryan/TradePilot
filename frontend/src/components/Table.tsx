import type { ReactNode } from "react";

export function DataTable<T>({
  rows,
  columns,
  empty,
  getKey
}: {
  rows: T[];
  columns: Array<{ key: string; header: string; render: (row: T) => ReactNode; align?: "right" | "left" }>;
  empty: string;
  getKey: (row: T, index: number) => string;
}) {
  if (!rows.length) return <div className="empty-state">{empty}</div>;

  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} className={column.align === "right" ? "numeric" : undefined}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={getKey(row, index)}>
              {columns.map((column) => (
                <td key={column.key} className={column.align === "right" ? "numeric" : undefined}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
