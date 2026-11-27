/** Sortable table (TanStack Table). Click a header to sort; click a row if onRowClick is set. */
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useState } from "react";

export function DataTable<T>({
  data, columns, onRowClick, initialSort = [], selectedId, rowId, empty = "Nothing to show.",
}: {
  data: T[];
  columns: ColumnDef<T, any>[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  onRowClick?: (row: T) => void;
  initialSort?: SortingState;
  selectedId?: string | null;
  rowId?: (row: T) => string;
  empty?: string;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  const table = useReactTable({
    data, columns, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    getRowId: rowId ? (r) => rowId(r) : undefined,
  });
  return (
    <table className="data">
      <thead>
        {table.getHeaderGroups().map((hg) => (
          <tr key={hg.id}>
            {hg.headers.map((h) => {
              const sorted = h.column.getIsSorted();
              const align = (h.column.columnDef.meta as { align?: string } | undefined)?.align;
              return (
                <th
                  key={h.id}
                  className={[h.column.getCanSort() ? "sortable" : "", align === "right" ? "right" : ""].join(" ")}
                  onClick={h.column.getToggleSortingHandler()}
                  aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"}
                >
                  {flexRender(h.column.columnDef.header, h.getContext())}
                  {sorted === "asc" ? " ▲" : sorted === "desc" ? " ▼" : ""}
                </th>
              );
            })}
          </tr>
        ))}
      </thead>
      <tbody>
        {table.getRowModel().rows.map((row) => (
          <tr
            key={row.id}
            className={[onRowClick ? "clickable" : "", selectedId && row.id === selectedId ? "selected" : ""].join(" ")}
            onClick={() => onRowClick?.(row.original)}
          >
            {row.getVisibleCells().map((cell) => {
              const align = (cell.column.columnDef.meta as { align?: string } | undefined)?.align;
              return (
                <td key={cell.id} className={align === "right" ? "right num" : ""}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              );
            })}
          </tr>
        ))}
        {!table.getRowModel().rows.length && (
          <tr>
            <td colSpan={columns.length} className="muted">{empty}</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

/** Inline bar for a 0–100 value next to its number. */
export function BarCell({ pct, label }: { pct: number; label?: string }) {
  return (
    <div className="bar-cell">
      <div className="track">
        <div className="fill" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
      </div>
      <span className="num">{label ?? `${pct.toFixed(0)}%`}</span>
    </div>
  );
}
