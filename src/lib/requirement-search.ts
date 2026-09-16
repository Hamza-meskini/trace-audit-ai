export interface RequirementQueueSearch {
  tab?: string | undefined;
  query?: string | undefined;
  category?: string | undefined;
  severity?: string | undefined;
  document?: string | undefined;
  issue?: string | undefined;
  sort?: "asc" | "desc" | undefined;
}

export interface NormalizedRequirementQueueSearch {
  tab: string;
  query: string;
  category: string;
  severity: string;
  document: string;
  issue: string;
  sort: "asc" | "desc";
}

export function parseRequirementQueueSearch(
  search: Record<string, unknown>,
): RequirementQueueSearch {
  return {
    tab: typeof search["tab"] === "string" ? search["tab"] : undefined,
    query: typeof search["query"] === "string" ? search["query"] : undefined,
    category: typeof search["category"] === "string" ? search["category"] : undefined,
    severity: typeof search["severity"] === "string" ? search["severity"] : undefined,
    document: typeof search["document"] === "string" ? search["document"] : undefined,
    issue: typeof search["issue"] === "string" ? search["issue"] : undefined,
    sort: search["sort"] === "desc" ? "desc" : undefined,
  };
}

export function normalizeRequirementQueueSearch(
  search: RequirementQueueSearch,
): NormalizedRequirementQueueSearch {
  return {
    tab: search.tab || "All",
    query: search.query || "",
    category: search.category || "all",
    severity: search.severity || "all",
    document: search.document || "all",
    issue: search.issue || "all",
    sort: search.sort || "asc",
  };
}
