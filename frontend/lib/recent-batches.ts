import { useSyncExternalStore } from "react";
import type { RecentBatch } from "./types";

/** There's no GET /batches endpoint (a deliberate Day 5 scope cut -- see
 * the upload page) so "recently uploaded batches" is tracked client-side,
 * scoped to this browser's session, instead of a real server-backed list. */
const STORAGE_KEY = "conduitai:recent-batches";
const MAX_ENTRIES = 20;

const listeners = new Set<() => void>();

export function listRecentBatches(): RecentBatch[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as RecentBatch[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function addRecentBatch(batch: RecentBatch): void {
  if (typeof window === "undefined") return;
  const existing = listRecentBatches().filter((b) => b.batch_id !== batch.batch_id);
  const updated = [batch, ...existing].slice(0, MAX_ENTRIES);
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
  listeners.forEach((listener) => listener());
}

function subscribe(callback: () => void): () => void {
  listeners.add(callback);
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) callback();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(callback);
    window.removeEventListener("storage", onStorage);
  };
}

const EMPTY: RecentBatch[] = [];

// useSyncExternalStore requires getSnapshot to return a *stable*
// reference when the underlying store hasn't changed -- listRecentBatches()
// parses JSON fresh every call, so a new array every render reads as "it
// changed" and loops forever. Cache the parsed array, keyed on the raw
// string, and only reparse when the raw value actually differs.
let cachedRaw: string | null | undefined;
let cachedSnapshot: RecentBatch[] = EMPTY;

function getSnapshot(): RecentBatch[] {
  if (typeof window === "undefined") return EMPTY;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === cachedRaw) return cachedSnapshot;
  cachedRaw = raw;
  cachedSnapshot = listRecentBatches();
  return cachedSnapshot;
}

/** localStorage is an external mutable store, not React state -- read it
 * through useSyncExternalStore (not useEffect+useState) so the server
 * snapshot ([]) and first client render agree and there's no hydration
 * mismatch or setState-in-effect cascade. */
export function useRecentBatches(): RecentBatch[] {
  return useSyncExternalStore(subscribe, getSnapshot, () => EMPTY);
}
