// The client's own photos, shared by the Photos panel and Ask Stratum's "which photo?".

import { useQuery } from "@tanstack/react-query";
import { getJSON } from "../lib/api";
import type { Asset, LayerInfo } from "../lib/types";

// A signed photo URL lasts an hour; ask again well before.
const PHOTOS_FRESH_MS = 30 * 60 * 1000;

export function usePhotos(clientId: string) {
  return useQuery({
    queryKey: ["assets", clientId],
    queryFn: () => getJSON<Asset[]>(`/api/clients/${clientId}/assets`),
    staleTime: PHOTOS_FRESH_MS,
    select: (rows) => rows.filter((row) => row.kind === "photo"),
  });
}

/** The photo part a click fills: the selected one, or the only one there is. */
export function photoTarget(layers: LayerInfo[], selected: string | null): LayerInfo | null {
  const slots = layers.filter((one) => one.kind === "photo" && one.visible && !one.locked);
  const chosen = slots.find((one) => one.id === selected);
  return chosen ?? (slots.length === 1 ? (slots[0] ?? null) : null);
}
