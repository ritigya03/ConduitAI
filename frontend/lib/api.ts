import type {
  BulkResolveResponse,
  ConfirmResponse,
  LoadSummary,
  MappingSpecResponse,
  MetricsResponse,
  ProfileResponse,
  QuarantineListResponse,
  QuarantineResolveResponse,
  SpecOverrideEntry,
  UploadResponse,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
// Day 5 has no auth/multi-tenant switcher -- every request just uses the
// backend's fixed demo tenant (app/config.py's default_tenant_id).
export const TENANT_ID = "demo-tenant";

class ApiError extends Error {
  constructor(
    public path: string,
    public status: number,
    detail: string,
  ) {
    super(`${path} failed: ${status} ${detail}`);
  }
}

async function apiForm<T>(
  path: string,
  fields: Record<string, string | File>,
  method: "POST" | "PATCH" = "POST",
): Promise<T> {
  const form = new FormData();
  for (const [key, value] of Object.entries(fields)) form.append(key, value);
  const res = await fetch(`${API_BASE}${path}`, { method, body: form });
  if (!res.ok) throw new ApiError(path, res.status, await res.text());
  return res.json();
}

async function apiGet<T>(path: string, params: Record<string, string | undefined> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, value);
  }
  const qs = query.toString();
  const res = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new ApiError(path, res.status, await res.text());
  return res.json();
}

/** URL for the ydata-profiling HTML report -- generated lazily on first
 * visit (Day 7: bundling report generation into every /profile call was
 * crashing the service on a memory-constrained deploy), then redirected
 * to the static file the backend's /reports mount serves. */
export function reportUrl(batchId: string): string {
  return `${API_BASE}/profile/${batchId}/report?tenant_id=${TENANT_ID}`;
}

export const api = {
  upload(file: File, sourceName: string, sourceKind: string): Promise<UploadResponse> {
    return apiForm("/upload", { tenant_id: TENANT_ID, source_name: sourceName, source_kind: sourceKind, file });
  },

  profile(batchId: string): Promise<ProfileResponse> {
    return apiForm("/profile", { tenant_id: TENANT_ID, batch_id: batchId });
  },

  createMappingSpec(batchId: string): Promise<MappingSpecResponse> {
    return apiForm("/mapping-spec", { tenant_id: TENANT_ID, batch_id: batchId });
  },

  patchMappingSpec(specId: string, overrides: Record<string, SpecOverrideEntry | null>): Promise<MappingSpecResponse> {
    return apiForm(
      `/mapping-spec/${specId}`,
      { tenant_id: TENANT_ID, spec_json: JSON.stringify(overrides) },
      "PATCH",
    );
  },

  confirmMappingSpec(specId: string): Promise<ConfirmResponse> {
    return apiForm(`/mapping-spec/${specId}/confirm`, { tenant_id: TENANT_ID });
  },

  loadBatch(batchId: string): Promise<LoadSummary> {
    return apiForm("/load", { tenant_id: TENANT_ID, batch_id: batchId });
  },

  listQuarantine(batchId?: string, status: string = "open"): Promise<QuarantineListResponse> {
    return apiGet("/quarantine", { tenant_id: TENANT_ID, batch_id: batchId, status });
  },

  resolveQuarantine(
    id: string,
    action: "fixed" | "ignored",
    correctedValues?: Record<string, string>,
  ): Promise<QuarantineResolveResponse> {
    const fields: Record<string, string> = { tenant_id: TENANT_ID, action };
    if (correctedValues) fields.corrected_values = JSON.stringify(correctedValues);
    return apiForm(`/quarantine/${id}/resolve`, fields);
  },

  bulkResolveQuarantine(
    errorCode: string,
    action: "fixed" | "ignored",
    correctedValues?: Record<string, string>,
  ): Promise<BulkResolveResponse> {
    const fields: Record<string, string> = { tenant_id: TENANT_ID, error_code: errorCode, action };
    if (correctedValues) fields.corrected_values = JSON.stringify(correctedValues);
    return apiForm("/quarantine/bulk-resolve", fields);
  },

  getMetrics(): Promise<MetricsResponse> {
    return apiGet("/metrics", { tenant_id: TENANT_ID });
  },
};
