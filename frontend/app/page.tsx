"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { addRecentBatch, useRecentBatches } from "@/lib/recent-batches";
import type { RecentBatch, UploadResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const SOURCE_KINDS = [
  { value: "crm", label: "CRM" },
  { value: "billing", label: "Billing" },
  { value: "support", label: "Support" },
];

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [sourceName, setSourceName] = useState("");
  const [sourceKind, setSourceKind] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const recentBatches = useRecentBatches();

  const isValid = file !== null && sourceName.trim().length > 0 && sourceKind.length > 0;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file || !isValid) return;
    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.upload(file, sourceName.trim(), sourceKind);
      setResult(response);
      const entry: RecentBatch = {
        batch_id: response.batch_id,
        source_name: sourceName.trim(),
        source_kind: sourceKind,
        row_count: response.row_count,
        uploaded_at: new Date().toISOString(),
      };
      addRecentBatch(entry);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-xl font-semibold">Upload a batch</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Upload a source CSV to start a new onboarding batch for review.
        </p>
      </div>

      <Card className="max-w-lg">
        <CardHeader>
          <CardTitle>New upload</CardTitle>
          <CardDescription>Every request runs against the fixed demo tenant.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="file">Source file (CSV)</Label>
              <Input
                id="file"
                type="file"
                accept=".csv"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                required
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="source-name">Source name</Label>
              <Input
                id="source-name"
                placeholder="e.g. salesforce-export"
                value={sourceName}
                onChange={(e) => setSourceName(e.target.value)}
                required
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="source-kind">Source kind</Label>
              <Select value={sourceKind} onValueChange={setSourceKind}>
                <SelectTrigger id="source-kind" className="w-full">
                  <SelectValue placeholder="Select a source kind" />
                </SelectTrigger>
                <SelectContent>
                  {SOURCE_KINDS.map((kind) => (
                    <SelectItem key={kind.value} value={kind.value}>
                      {kind.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {error && <p className="text-sm text-[var(--danger)]">{error}</p>}

            <Button type="submit" disabled={!isValid || submitting}>
              {submitting ? "Uploading…" : "Upload"}
            </Button>
          </form>

          {result && (
            <div className="mt-5 flex flex-col gap-3 rounded-lg border border-border bg-muted/50 p-4 text-sm">
              <div className="flex flex-col gap-1">
                <span className="text-muted-foreground">
                  {result.idempotent ? "Already uploaded — reusing existing batch." : "Uploaded."}
                </span>
                <span className="font-mono text-xs text-muted-foreground">{result.batch_id}</span>
                <span>{result.row_count ?? "?"} rows</span>
              </div>
              <Button asChild size="sm" className="self-start">
                <Link href={`/batches/${result.batch_id}/profile`}>Profile this batch</Link>
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* No GET /batches endpoint exists -- a deliberate Day 5 scope cut
          (see lib/recent-batches.ts). This lists only batches this
          browser has uploaded this session, not every batch for the
          tenant. */}
      {recentBatches.length > 0 && (
        <div className="max-w-lg">
          <h2 className="text-sm font-medium text-muted-foreground">Recently uploaded (this session)</h2>
          <ul className="mt-3 flex flex-col gap-2">
            {recentBatches.map((batch) => (
              <li key={batch.batch_id} className="rounded-lg border border-border bg-card p-3 text-sm">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex flex-col">
                    <span className="font-medium">{batch.source_name}</span>
                    <span className="text-xs text-muted-foreground">
                      {batch.source_kind} · {batch.row_count ?? "?"} rows
                    </span>
                  </div>
                  <Button asChild variant="outline" size="sm">
                    <Link href={`/batches/${batch.batch_id}/profile`}>Profile</Link>
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
