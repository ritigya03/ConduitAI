"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MetricsResponse } from "@/lib/types";
import { StatTile } from "@/components/stat-tile";

export default function MetricsPage() {
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .getMetrics()
      .then((response) => {
        if (!cancelled) setMetrics(response);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load metrics");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold">Metrics</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Tenant-wide counts across every batch — a point-in-time snapshot, not a trend.
        </p>
      </div>

      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}

      {metrics ? (
        <div className="grid max-w-3xl grid-cols-2 gap-4 sm:grid-cols-4">
          <StatTile value={metrics.batches} label="Batches" />
          <StatTile value={metrics.mapping_specs_confirmed} label="Confirmed specs" />
          <StatTile value={metrics.customers} label="Customers" />
          <StatTile value={metrics.invoices} label="Invoices" />
          <StatTile value={metrics.support_tickets} label="Support tickets" />
          <StatTile value={metrics.accounts} label="Accounts" />
          <StatTile value={metrics.quarantine_open} label="Quarantine open" tone="danger" />
          <StatTile value={metrics.quarantine_fixed} label="Quarantine fixed" tone="success" />
        </div>
      ) : (
        !error && <p className="text-sm text-muted-foreground">Loading metrics…</p>
      )}
    </div>
  );
}
