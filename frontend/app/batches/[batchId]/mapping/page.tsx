"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import type { ColumnDef } from "@tanstack/react-table";
import { api } from "@/lib/api";
import type { MappingSpecResponse, ProfileColumn, ProvenanceMethod } from "@/lib/types";
import { DataTable } from "@/components/data-table";
import { BucketBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const NONE_VALUE = "__none__";

const PROVENANCE_LABEL: Record<ProvenanceMethod, string> = {
  deterministic: "deterministic",
  llm: "llm",
  deterministic_fallback: "deterministic (fallback)",
  human: "human override",
};

interface Row {
  column: ProfileColumn;
  /** Canonical field currently assigned to this source column, if any. */
  assignedField: string | null;
  provenance: ProvenanceMethod | null;
}

export default function MappingReviewPage() {
  const { batchId } = useParams<{ batchId: string }>();
  const router = useRouter();

  const [columns, setColumns] = useState<ProfileColumn[] | null>(null);
  const [spec, setSpec] = useState<MappingSpecResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingColumns, setPendingColumns] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.profile(batchId), api.createMappingSpec(batchId)])
      .then(([profileResponse, specResponse]) => {
        if (cancelled) return;
        setColumns(profileResponse.columns);
        setSpec(specResponse);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to build mapping spec");
      });
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  // source_column -> currently assigned canonical field name + entry.
  // build_spec_entries assigns at most one canonical field per source
  // column, so this reverse index is safe to build 1:1.
  const assignmentByColumn = useMemo(() => {
    const map = new Map<string, { field: string; provenance: ProvenanceMethod }>();
    if (!spec) return map;
    for (const [field, entry] of Object.entries(spec.spec_json)) {
      map.set(entry.source_column, { field, provenance: entry.provenance.method });
    }
    return map;
  }, [spec]);

  const rows: Row[] = useMemo(() => {
    if (!columns) return [];
    return columns.map((column) => {
      const assignment = assignmentByColumn.get(column.column_name);
      return {
        column,
        assignedField: assignment?.field ?? null,
        provenance: assignment?.provenance ?? null,
      };
    });
  }, [columns, assignmentByColumn]);

  async function handleOverride(row: Row, newField: string | null) {
    if (!spec || newField === row.assignedField) return;
    const previousSpec = spec;

    const overrides: Record<string, { source_column: string } | null> = {};
    if (row.assignedField) overrides[row.assignedField] = null;
    if (newField) overrides[newField] = { source_column: row.column.column_name };

    // Optimistic update: reflect the override immediately, roll back to
    // `previousSpec` if the PATCH fails. The placeholder transform below
    // is never rendered (this UI only shows source_column/provenance) and
    // gets replaced by the server's real response on success either way.
    const optimisticJson = { ...spec.spec_json };
    if (row.assignedField) delete optimisticJson[row.assignedField];
    if (newField) {
      optimisticJson[newField] = {
        source_column: row.column.column_name,
        transform: { function: "trim", params: {} },
        provenance: { method: "human", model: null, confidence: null, reasoning: null },
      };
    }
    setSpec({ ...spec, spec_json: optimisticJson });
    setPendingColumns((prev) => new Set(prev).add(row.column.column_name));
    setError(null);

    try {
      const updated = await api.patchMappingSpec(spec.mapping_spec_id, overrides);
      setSpec(updated);
    } catch (err) {
      setSpec(previousSpec);
      setError(err instanceof Error ? err.message : "Failed to save override");
    } finally {
      setPendingColumns((prev) => {
        const next = new Set(prev);
        next.delete(row.column.column_name);
        return next;
      });
    }
  }

  async function handleConfirm() {
    if (!spec) return;
    setConfirming(true);
    setError(null);
    try {
      await api.confirmMappingSpec(spec.mapping_spec_id);
      router.push(`/batches/${batchId}/load`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to confirm mapping");
      setConfirming(false);
    }
  }

  const columnDefs: ColumnDef<Row>[] = [
    {
      id: "column_name",
      header: "Source column",
      cell: ({ row }) => <span className="font-mono text-sm">{row.original.column.column_name}</span>,
    },
    {
      id: "samples",
      header: "Sample values",
      cell: ({ row }) => (
        <code className="text-xs text-muted-foreground">
          {row.original.column.stats.sample_values.slice(0, 3).join(", ") || "—"}
        </code>
      ),
    },
    {
      id: "confidence",
      header: "Confidence",
      cell: ({ row }) => {
        const top = row.original.column.candidates[0];
        return <BucketBadge bucket={row.original.column.bucket} confidence={top?.confidence ?? 0} />;
      },
    },
    {
      id: "provenance",
      header: "Provenance",
      cell: ({ row }) => (
        <span className="text-xs text-muted-foreground">
          {row.original.provenance ? PROVENANCE_LABEL[row.original.provenance] : "—"}
        </span>
      ),
    },
    {
      id: "override",
      header: "Canonical field",
      cell: ({ row }) => {
        const isPending = pendingColumns.has(row.original.column.column_name);
        return (
          <Select
            value={row.original.assignedField ?? NONE_VALUE}
            disabled={isPending}
            onValueChange={(value) => handleOverride(row.original, value === NONE_VALUE ? null : value)}
          >
            <SelectTrigger className="w-56">
              <SelectValue placeholder="None of these" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE_VALUE}>None of these</SelectItem>
              {row.original.column.candidates.map((candidate) => (
                <SelectItem key={candidate.canonical_field} value={candidate.canonical_field}>
                  {candidate.canonical_field}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );
      },
    },
  ];

  const anyPending = pendingColumns.size > 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Mapping review</h1>
          <p className="mt-1 font-mono text-xs text-muted-foreground">{batchId}</p>
        </div>
        <Button onClick={handleConfirm} disabled={!spec || anyPending || confirming}>
          {confirming ? "Confirming…" : "Confirm mapping"}
        </Button>
      </div>

      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}

      {rows.length > 0 ? (
        <DataTable columns={columnDefs} data={rows} emptyMessage="No columns found for this batch." />
      ) : (
        !error && <p className="text-sm text-muted-foreground">Building mapping spec…</p>
      )}
    </div>
  );
}
