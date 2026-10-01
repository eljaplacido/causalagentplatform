/**
 * Entity ids → entity names, from the semantic layer (GET /api/semantic/entities).
 *
 * The semantic layer refers to entities by UUID -- an edge's source and
 * target, a fragment's nodes, a discovery agent's "Propose edge: <id> → <id>".
 * Pages show the entity's name when the semantic layer has an entity with that
 * id, and otherwise a short form of the id (its first 8 characters and "…"),
 * with the full id in a tooltip: an id that names no entity is shown as an id,
 * never dressed up as a name. A failed entity request leaves every id short
 * rather than blocking the page.
 */

import { useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  semanticApi,
  type CausalEntityResponse,
  type ReviewQueueEndpoint,
  type ReviewQueueItemResponse,
} from "@/lib/api";

/** A UUID, as the backend writes them. */
export const UUID_PATTERN = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;

/** The short form of an id: first 8 characters and an ellipsis (unchanged if already short). */
export function shortId(id: string): string {
  return id.length > 9 ? `${id.slice(0, 8)}…` : id;
}

/** An entity's name if `names` has it, else the short id. */
export function nameOrShortId(names: ReadonlyMap<string, string>, id: string): string {
  return names.get(id) ?? names.get(id.toLowerCase()) ?? shortId(id);
}

/** Every UUID in `text` replaced by its entity's name, or by its short form. */
export function withEntityNames(names: ReadonlyMap<string, string>, text: string): string {
  return text.replace(UUID_PATTERN, (id) => nameOrShortId(names, id));
}

/** Build the id → name map from the entities the backend returned. */
export function entityNameMap(entities: readonly Pick<CausalEntityResponse, "id" | "name">[]): Map<string, string> {
  return new Map(entities.map((e) => [e.id.toLowerCase(), e.name]));
}

/** The entity names, and the two helpers bound to them. */
export function useEntityNames() {
  const query = useQuery({
    queryKey: ["semantic-entities"],
    queryFn: () => semanticApi.entities(),
    staleTime: 60_000,
    retry: 1,
  });
  const names = useMemo(() => entityNameMap(query.data ?? []), [query.data]);
  const nameOf = useCallback((id: string) => nameOrShortId(names, id), [names]);
  const withNames = useCallback((text: string) => withEntityNames(names, text), [names]);
  return { names, nameOf, withNames, isError: query.isError };
}

/**
 * A review-queue row's label. An edge's `name` is only its edge type, so an
 * edge is labelled by what it connects -- each end by the entity name the
 * backend resolved, else its object type and short id -- and then its type:
 * "checkout-service → Outcome 5559caf1… (CAUSES)". Other rows keep their name.
 */
export function reviewQueueLabel(item: ReviewQueueItemResponse): string {
  if (!item.source || !item.target) return item.name;
  const end = (e: ReviewQueueEndpoint) => e.name ?? `${e.object_type} ${shortId(e.id)}`;
  return `${end(item.source)} → ${end(item.target)} (${item.name})`;
}
