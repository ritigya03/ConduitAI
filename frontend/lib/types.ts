/** Request/response shapes for the ConduitAI backend, matching the
 * Pydantic models field-for-field (see backend/app/loader.py's
 * LoadSummary, backend/app/scoring.py's ColumnMapping/MappingCandidate,
 * backend/app/profiling.py's ColumnStats, and each router). */

export interface UploadResponse {
  batch_id: string;
  row_count: number | null;
  idempotent: boolean;
}

export interface ColumnStats {
  column_name: string;
  row_count: number;
  null_count: number;
  null_fraction: number;
  distinct_count: number;
  distinct_fraction: number;
  sample_values: string[];
  date_parse_fraction: number;
  int_parse_fraction: number;
  float_parse_fraction: number;
  email_match_fraction: number;
  phone_match_fraction: number;
  currency_code_match_fraction: number;
}

export interface MappingCandidate {
  canonical_field: string;
  confidence: number;
  name_score: number;
  embedding_score: number;
  type_score: number;
}

export type MappingBucket = "auto_accept" | "human_confirm" | "unmapped";

export interface ProfileColumn {
  column_name: string;
  stats: ColumnStats;
  candidates: MappingCandidate[];
  best_field: string | null;
  bucket: MappingBucket;
}

export interface ProfileResponse {
  batch_id: string;
  columns: ProfileColumn[];
}

export type ProvenanceMethod = "deterministic" | "llm" | "deterministic_fallback" | "human";

export interface SpecEntryProvenance {
  method: ProvenanceMethod;
  model: string | null;
  confidence: number | null;
  reasoning: string | null;
}

export interface SpecEntry {
  source_column: string;
  transform: { function: string; params: Record<string, unknown> };
  provenance: SpecEntryProvenance;
}

/** Keyed by canonical field name -- see backend/app/mapping_spec.py's
 * build_spec_entries docstring. */
export type SpecJson = Record<string, SpecEntry>;

/** PATCH /mapping-spec/{id} payload shape for one field: `transform`/
 * `provenance` are optional because the backend fills them in from the
 * field's registered type when omitted (see update_spec_json) -- a
 * reviewer's override only ever supplies a field name and a source
 * column. */
export type SpecOverrideEntry = Pick<SpecEntry, "source_column"> & Partial<Omit<SpecEntry, "source_column">>;

export type MappingSpecStatus = "draft" | "confirmed" | "superseded";

export interface MappingSpecResponse {
  mapping_spec_id: string;
  version: number;
  parent_version: number | null;
  status: MappingSpecStatus;
  spec_json: SpecJson;
}

export interface ConfirmResponse {
  mapping_spec_id: string;
  version: number;
  status: MappingSpecStatus;
}

export interface LoadSummary {
  batch_id: string;
  mapping_spec_version: number;
  total_rows: number;
  loaded: number;
  quarantined: number;
}

export type QuarantineStatus = "open" | "fixed" | "ignored" | "resubmitted";

export interface QuarantineItem {
  id: string;
  batch_id: string;
  raw_record_json: Record<string, string | null>;
  error_codes: string[];
  severity: string;
  explanation: string | null;
  suggested_fix: string | null;
  status: QuarantineStatus;
}

export interface QuarantineListResponse {
  items: QuarantineItem[];
}

export interface QuarantineResolveResponse {
  status: string;
  loaded: boolean;
}

export interface BulkResolveResponse {
  processed: number;
  loaded: number;
  still_open: number;
}

export interface MetricsResponse {
  batches: number;
  customers: number;
  invoices: number;
  support_tickets: number;
  accounts: number;
  quarantine_open: number;
  quarantine_fixed: number;
  mapping_specs_confirmed: number;
}

/** A batch the user has created this session -- there's no GET /batches
 * endpoint (deliberate Day 5 scope cut, see the upload page), so recently
 * uploaded batches are tracked client-side in localStorage instead. */
export interface RecentBatch {
  batch_id: string;
  source_name: string;
  source_kind: string;
  row_count: number | null;
  uploaded_at: string;
}
