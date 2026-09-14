"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import type { ColumnDef } from "@tanstack/react-table";
import { api } from "@/lib/api";
import type { LoadSummary, QuarantineItem } from "@/lib/types";
import { DataTable } from "@/components/data-table";
import { ErrorCodeBadge } from "@/components/status-badge";
import { StatTile } from "@/components/stat-tile";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** A single quarantined row's inline Fix/Ignore controls. Kept generic
 * per this plan's own guidance -- a plain "field name" + "new value"
 * pair rather than per-error-code-specific widgets, since deriving the
 * raw source column a canonical-field error came from would need a
 * dedicated spec lookup this build doesn't have. The reviewer can see
 * the raw row right there in the queue to know which key to correct. */
function FixPanel({
  suggestedFix,
  busy,
  onApply,
  onCancel,
}: {
  suggestedFix: string | null;
  busy: boolean;
  onApply: (field: string, value: string) => void;
  onCancel: () => void;
}) {
  const suggestedValue = suggestedFix?.match(/'([^']+)'/)?.[1] ?? "";
  const [field, setField] = useState("");
  const [value, setValue] = useState(suggestedValue);

  return (
    <div className="flex flex-wrap items-center gap-2 pt-2">
      <Input
        placeholder="raw field name"
        value={field}
        onChange={(e) => setField(e.target.value)}
        className="h-7 w-36 font-mono text-xs"
      />
      <Input
        placeholder="corrected value"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-7 w-44 font-mono text-xs"
      />
      <Button size="xs" disabled={busy || !field} onClick={() => onApply(field, value)}>
        Apply
      </Button>
      <Button size="xs" variant="ghost" disabled={busy} onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}

function QuarantineRowActions({
  item,
  busy,
  onResolve,
}: {
  item: QuarantineItem;
  busy: boolean;
  onResolve: (item: QuarantineItem, action: "fixed" | "ignored", field?: string, value?: string) => void;
}) {
  const [fixing, setFixing] = useState(false);

  if (fixing) {
    return (
      <FixPanel
        suggestedFix={item.suggested_fix}
        busy={busy}
        onApply={(field, value) => {
          onResolve(item, "fixed", field, value);
          setFixing(false);
        }}
        onCancel={() => setFixing(false)}
      />
    );
  }

  return (
    <div className="flex items-center gap-2">
      <Button size="xs" variant="outline" disabled={busy} onClick={() => setFixing(true)}>
        Fix
      </Button>
      <Button size="xs" variant="ghost" disabled={busy} onClick={() => onResolve(item, "ignored")}>
        Ignore
      </Button>
    </div>
  );
}

function BulkFixBar({
  errorCode,
  count,
  busy,
  onResolve,
}: {
  errorCode: string;
  count: number;
  busy: boolean;
  onResolve: (errorCode: string, action: "fixed" | "ignored", field?: string, value?: string) => void;
}) {
  const [field, setField] = useState("");
  const [value, setValue] = useState("");

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-card p-3 text-sm">
      <span className="text-muted-foreground">
        <span className="font-mono text-foreground">{count}</span> row{count === 1 ? "" : "s"} tagged
      </span>
      <ErrorCodeBadge code={errorCode} />
      <span className="text-muted-foreground">— set</span>
      <Input
        placeholder="raw field name"
        value={field}
        onChange={(e) => setField(e.target.value)}
        className="h-7 w-36 font-mono text-xs"
      />
      <span className="text-muted-foreground">to</span>
      <Input
        placeholder="value"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="h-7 w-36 font-mono text-xs"
      />
      <Button size="xs" disabled={busy || !field} onClick={() => onResolve(errorCode, "fixed", field, value)}>
        Apply to all
      </Button>
      <Button size="xs" variant="ghost" disabled={busy} onClick={() => onResolve(errorCode, "ignored")}>
        Ignore all
      </Button>
    </div>
  );
}

export default function LoadAndQuarantinePage() {
  const { batchId } = useParams<{ batchId: string }>();

  const [summary, setSummary] = useState<LoadSummary | null>(null);
  const [items, setItems] = useState<QuarantineItem[] | null>(null);
  const [resolvedCount, setResolvedCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const refreshQueue = useCallback(async () => {
    const response = await api.listQuarantine(batchId, "open");
    setItems(response.items);
  }, [batchId]);

  useEffect(() => {
    let cancelled = false;
    // POST /load is called exactly once, on page mount -- calling it
    // again later would recompute quarantine fresh from the original
    // raw rows and discard every fix/ignore resolution made below.
    // After this, the page only talks to /quarantine endpoints.
    api
      .loadBatch(batchId)
      .then((response) => {
        if (cancelled) return;
        setSummary(response);
        return refreshQueue();
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load batch");
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  const groups = useMemo(() => {
    if (!items) return [];
    const counts = new Map<string, number>();
    for (const item of items) {
      for (const code of item.error_codes) counts.set(code, (counts.get(code) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [items]);

  async function handleRowResolve(item: QuarantineItem, action: "fixed" | "ignored", field?: string, value?: string) {
    setBusyId(item.id);
    setError(null);
    try {
      const correctedValues = action === "fixed" && field ? { [field]: value ?? "" } : undefined;
      const result = await api.resolveQuarantine(item.id, action, correctedValues);
      if (result.loaded) setResolvedCount((c) => c + 1);
      await refreshQueue();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to resolve row");
    } finally {
      setBusyId(null);
    }
  }

  async function handleBulkResolve(errorCode: string, action: "fixed" | "ignored", field?: string, value?: string) {
    setBusyId(errorCode);
    setError(null);
    try {
      const correctedValues = action === "fixed" && field ? { [field]: value ?? "" } : undefined;
      const result = await api.bulkResolveQuarantine(errorCode, action, correctedValues);
      setResolvedCount((c) => c + result.loaded);
      await refreshQueue();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bulk resolve failed");
    } finally {
      setBusyId(null);
    }
  }

  const columns: ColumnDef<QuarantineItem>[] = [
    {
      id: "error_codes",
      header: "Errors",
      cell: ({ row }) => (
        <div className="flex flex-wrap gap-1">
          {row.original.error_codes.map((code) => (
            <ErrorCodeBadge key={code} code={code} />
          ))}
        </div>
      ),
    },
    {
      id: "explanation",
      header: "Explanation",
      cell: ({ row }) => <span className="text-xs text-muted-foreground">{row.original.explanation ?? "—"}</span>,
    },
    {
      id: "suggested_fix",
      header: "Suggested fix",
      cell: ({ row }) => <span className="text-xs text-muted-foreground">{row.original.suggested_fix ?? "—"}</span>,
    },
    {
      id: "actions",
      header: "Actions",
      cell: ({ row }) => (
        <QuarantineRowActions item={row.original} busy={busyId === row.original.id} onResolve={handleRowResolve} />
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-xl font-semibold">Load &amp; exception queue</h1>
        <p className="mt-1 font-mono text-xs text-muted-foreground">{batchId}</p>
      </div>

      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}

      {summary ? (
        <div className="grid grid-cols-3 gap-4 max-w-2xl">
          <StatTile value={summary.total_rows} label="Total rows" />
          <StatTile value={summary.loaded + resolvedCount} label="Loaded" tone="success" />
          <StatTile value={items?.length ?? summary.quarantined} label="Still in queue" tone="danger" />
        </div>
      ) : (
        !error && <p className="text-sm text-muted-foreground">Loading batch…</p>
      )}

      {groups.length > 0 && (
        <div className="flex flex-col gap-2">
          <h2 className="text-sm font-medium text-muted-foreground">Bulk-fix by error code</h2>
          {groups.map(([code, count]) => (
            <BulkFixBar
              key={code}
              errorCode={code}
              count={count}
              busy={busyId === code}
              onResolve={handleBulkResolve}
            />
          ))}
        </div>
      )}

      {items && (
        <div>
          <h2 className="mb-2 text-sm font-medium text-muted-foreground">Exception queue</h2>
          <DataTable columns={columns} data={items} emptyMessage="No quarantined rows — everything loaded." />
        </div>
      )}
    </div>
  );
}
