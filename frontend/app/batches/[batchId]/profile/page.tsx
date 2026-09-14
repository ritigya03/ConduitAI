"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { ColumnDef } from "@tanstack/react-table";
import { api, reportUrl } from "@/lib/api";
import type { ProfileColumn } from "@/lib/types";
import { DataTable } from "@/components/data-table";
import { Button } from "@/components/ui/button";

const columns: ColumnDef<ProfileColumn>[] = [
  {
    accessorKey: "column_name",
    header: "Column",
    cell: ({ row }) => <span className="font-mono text-sm">{row.original.column_name}</span>,
  },
  {
    id: "null_pct",
    header: "Null %",
    cell: ({ row }) => <span className="font-mono text-sm">{(row.original.stats.null_fraction * 100).toFixed(1)}%</span>,
  },
  {
    id: "distinct",
    header: "Distinct",
    cell: ({ row }) => <span className="font-mono text-sm">{row.original.stats.distinct_count}</span>,
  },
  {
    id: "samples",
    header: "Sample values",
    cell: ({ row }) => (
      <code className="text-xs text-muted-foreground">
        {row.original.stats.sample_values.slice(0, 4).join(", ") || "—"}
      </code>
    ),
  },
];

export default function ProfilePage() {
  const { batchId } = useParams<{ batchId: string }>();
  const [data, setData] = useState<ProfileColumn[] | null>(null);
  const [reportPath, setReportPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    // POST /profile is idempotent -- safe to recompute and render on
    // every page load, per Day 2's design.
    api
      .profile(batchId)
      .then((response) => {
        if (cancelled) return;
        setData(response.columns);
        setReportPath(response.report_path);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to profile batch");
      });
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Column profile</h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{batchId}</p>
        </div>
        <div className="flex items-center gap-2">
          {reportPath && (
            <Button asChild variant="outline" size="sm">
              <a href={reportUrl(batchId)} target="_blank" rel="noreferrer">
                Full report
              </a>
            </Button>
          )}
          <Button asChild size="sm">
            <Link href={`/batches/${batchId}/mapping`}>Generate mapping</Link>
          </Button>
        </div>
      </div>

      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}

      {data ? (
        <DataTable columns={columns} data={data} emptyMessage="No columns found for this batch." />
      ) : (
        !error && <p className="text-sm text-muted-foreground">Profiling batch…</p>
      )}
    </div>
  );
}
