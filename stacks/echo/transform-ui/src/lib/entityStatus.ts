// The step-1 entity cards' live half (UIFPE-219): the entity's last
// transform run and its gold row count, from the grain-probe service's
// entityStatus mode. Degrades gracefully — while the service URL is
// unconfigured (or a card's fetch fails) the cards show only what the
// repository knows: lifecycle, columns, schedule.
import { GRAIN_PROBE_URL, probeConfigured } from "./grainProbe";

export interface EntityStatus {
  rowCount: number;
  lastRun: { state: string; startedOn: string; seconds?: number } | null;
}

export const statusConfigured = probeConfigured;

// Orientation data changes hourly at most — one fetch per entity per
// session-ish window, not one per step visit (each miss is a real Athena
// execution).
const cache = new Map<string, { at: number; value: Promise<EntityStatus> }>();
const TTL_MS = 5 * 60_000;

export async function fetchEntityStatus(goldId: string, database: string,
                                        table: string): Promise<EntityStatus> {
  const key = `${database}.${table}#${goldId}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.value;
  const value = fetchFresh(goldId, database, table);
  cache.set(key, { at: Date.now(), value });
  value.catch(() => cache.delete(key));   // a failure is not worth caching
  return value;
}

async function fetchFresh(goldId: string, database: string,
                          table: string): Promise<EntityStatus> {
  const r = await fetch(GRAIN_PROBE_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ mode: "entityStatus", goldId, database, table }),
  });
  const body = await r.json().catch(() => null);
  if (!r.ok)
    throw new Error((body?.problems ?? [`status service answered HTTP ${r.status}`])
      .join(" "));
  if (typeof body?.rowCount !== "number")
    throw new Error("status service answered in an unexpected shape");
  return body as EntityStatus;
}
