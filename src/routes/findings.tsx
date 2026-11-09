import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";
import { Search, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState, PageHeader, Panel } from "@/components/primitives";
import { Mono, ReviewBadge, SeverityBadge, normalizeSeverity } from "@/components/status";
import { useActiveProject } from "@/hooks/use-active-project";
import { useFindings, useUpdateFinding } from "@/hooks/use-findings";
import { cn } from "@/lib/utils";
import { getErrorMessage } from "@/lib/api-client";

interface FindingsSearch {
  severity?: string | undefined;
  type?: string | undefined;
  status?: string | undefined;
  category?: string | undefined;
  reviewer?: string | undefined;
  query?: string | undefined;
}

const FINDINGS_FILTERS = ["severity", "type", "status", "category", "reviewer", "query"] as const;

export const Route = createFileRoute("/findings")({
  // Filters live in the URL so a triage view can be bookmarked or shared.
  validateSearch: (search: Record<string, unknown>): FindingsSearch => {
    const result: FindingsSearch = {};
    for (const key of FINDINGS_FILTERS) {
      const value = search[key];
      if (typeof value === "string" && value && value !== "all") result[key] = value;
    }
    return result;
  },
  head: () => ({
    meta: [
      { title: "Findings & Triage — TraceAudit" },
      {
        name: "description",
        content: "Triage and resolve audit findings, missing evidence and conflicts.",
      },
      { property: "og:title", content: "Findings & Triage — TraceAudit" },
      {
        property: "og:description",
        content: "Prioritize findings by severity and assign review actions.",
      },
    ],
  }),
  component: FindingsPage,
});

const types = [
  "Missing evidence",
  "Partial evidence",
  "Potential conflict",
  "Unsupported requirement",
  "Duplicate requirement",
  "Ambiguous requirement",
];

function FindingsPage() {
  const navigate = useNavigate();
  const { activeProjectId } = useActiveProject();

  const search = Route.useSearch();
  const severity = search.severity || "all";
  const type = search.type || "all";
  const status = search.status || "all";
  const category = search.category || "all";
  const reviewer = search.reviewer || "all";
  const query = search.query || "";
  const updateSearch = (patch: FindingsSearch) =>
    navigate({
      to: "/findings",
      search: (prev) => {
        const next: Record<string, unknown> = { ...prev, ...patch };
        for (const key of FINDINGS_FILTERS) if (!next[key] || next[key] === "all") delete next[key];
        return next;
      },
      replace: true,
    });
  const setSeverity = (value: string) => updateSearch({ severity: value });
  const setType = (value: string) => updateSearch({ type: value });
  const setStatus = (value: string) => updateSearch({ status: value });
  const setCategory = (value: string) => updateSearch({ category: value });
  const setReviewer = (value: string) => updateSearch({ reviewer: value });
  const setQuery = (value: string) => updateSearch({ query: value });

  const {
    data: findingsList,
    isLoading,
    isError,
    error,
    refetch,
  } = useFindings(activeProjectId, {
    severity,
    finding_type: type,
    review_state: status,
    category,
  });

  const allFindings = useMemo(() => findingsList ?? [], [findingsList]);
  const reviewers = Array.from(
    new Set(allFindings.map((f) => f.assigned_to).filter(Boolean) as string[]),
  );
  const categories = Array.from(
    new Set(allFindings.map((f) => f.category).filter(Boolean) as string[]),
  );

  const rows = useMemo(
    () =>
      allFindings.filter((f) => {
        if (reviewer !== "all" && f.assigned_to !== reviewer) return false;
        if (
          query &&
          !`${f.finding_code} ${f.requirement_title || ""}`
            .toLowerCase()
            .includes(query.toLowerCase())
        )
          return false;
        return true;
      }),
    [allFindings, reviewer, query],
  );

  const severityCounts = {
    Critical: allFindings.filter((f) => normalizeSeverity(f.severity) === "Critical").length,
    High: allFindings.filter((f) => normalizeSeverity(f.severity) === "High").length,
    Medium: allFindings.filter((f) => normalizeSeverity(f.severity) === "Medium").length,
    Low: allFindings.filter((f) => normalizeSeverity(f.severity) === "Low").length,
  };

  const summary = [
    { label: "Total", value: allFindings.length, tone: "text-foreground" },
    { label: "Critical", value: severityCounts.Critical, tone: "text-critical" },
    { label: "High", value: severityCounts.High, tone: "text-warning" },
    { label: "Medium", value: severityCounts.Medium, tone: "text-primary" },
    { label: "Low", value: severityCounts.Low, tone: "text-muted-foreground" },
  ];

  if (isError)
    return (
      <div role="alert" className="rounded-xl border bg-card p-8 text-sm">
        Findings could not be loaded. {getErrorMessage(error)}{" "}
        <Button variant="outline" className="ml-3" onClick={() => void refetch()}>
          Retry
        </Button>
      </div>
    );

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Findings"
        subtitle="Items detected during analysis that require human review or additional evidence."
      />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        {summary.map((s) => (
          <div key={s.label} className="rounded-xl border border-border bg-card p-4 shadow-subtle">
            <div className="text-xs uppercase tracking-wide text-muted-foreground">{s.label}</div>
            <div className={`mt-1.5 text-2xl font-semibold tabular ${s.tone}`}>{s.value}</div>
          </div>
        ))}
      </div>

      <Panel className="mt-4" bodyClassName="p-4">
        <div className="flex flex-wrap gap-2">
          <Select value={severity} onValueChange={setSeverity}>
            <SelectTrigger className="h-9 w-[140px]">
              <SelectValue placeholder="Severity" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All severities</SelectItem>
              {["Critical", "High", "Medium", "Low"].map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={type} onValueChange={setType}>
            <SelectTrigger className="h-9 w-[210px]">
              <SelectValue placeholder="Type" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All types</SelectItem>
              {types.map((t) => (
                <SelectItem key={t} value={t}>
                  {t}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="h-9 w-[150px]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {["Open", "Needs review", "Reviewed", "Approved", "Rejected"].map((s) => (
                <SelectItem key={s} value={s}>
                  {s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger className="h-9 w-[160px]">
              <SelectValue placeholder="Category" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All categories</SelectItem>
              {categories.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {reviewers.length > 0 && (
            <Select value={reviewer} onValueChange={setReviewer}>
              <SelectTrigger className="h-9 w-[160px]">
                <SelectValue placeholder="Reviewer" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All reviewers</SelectItem>
                {reviewers.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <div className="relative ml-auto w-full sm:w-56">
            <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search findings..."
              className="h-9 pl-8"
            />
          </div>
        </div>
      </Panel>

      <Panel className="mt-4" bodyClassName="p-0">
        {isLoading ? (
          <div className="flex items-center justify-center p-12">
            <Loader2 className="size-6 animate-spin text-primary" />
          </div>
        ) : rows.length === 0 ? (
          <div className="p-6">
            <EmptyState
              title="No findings require your attention."
              description="Your current audit has no unresolved findings matching these filters."
              action={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    updateSearch({
                      severity: undefined,
                      type: undefined,
                      status: undefined,
                      category: undefined,
                      reviewer: undefined,
                      query: undefined,
                    })
                  }
                >
                  Reset filters
                </Button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1040px] text-sm">
              <thead className="border-b border-border bg-card">
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-5 py-2.5 font-medium">ID</th>
                  <th className="px-5 py-2.5 font-medium">Type</th>
                  <th className="px-5 py-2.5 font-medium">Requirement</th>
                  <th className="px-5 py-2.5 font-medium">Severity</th>
                  <th className="px-5 py-2.5 font-medium">Evidence</th>
                  <th className="px-5 py-2.5 font-medium">Status</th>
                  <th className="px-5 py-2.5 font-medium">Owner</th>
                  <th className="px-5 py-2.5 font-medium">Updated</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((f) => (
                  <tr
                    key={f.id}
                    onClick={() =>
                      f.requirement_id &&
                      navigate({
                        to: "/requirements/$id",
                        params: { id: f.requirement_id },
                      })
                    }
                    className={cn(
                      "border-b border-border/70 transition-colors last:border-0 focus-within:bg-accent/50",
                      f.requirement_id && "cursor-pointer hover:bg-accent/50",
                    )}
                  >
                    <td className="whitespace-nowrap px-5 py-3">
                      {f.requirement_id ? (
                        // A real link: reachable with Tab, opens with Enter,
                        // and can be opened in a new tab or copied.
                        <Link
                          to="/requirements/$id"
                          params={{ id: f.requirement_id }}
                          onClick={(event) => event.stopPropagation()}
                          className="rounded-sm underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          aria-label={`Open requirement for finding ${f.finding_code}`}
                        >
                          <Mono className="text-foreground">{f.finding_code}</Mono>
                        </Link>
                      ) : (
                        <Mono className="text-foreground">{f.finding_code}</Mono>
                      )}
                    </td>
                    <td className="px-5 py-3">{f.finding_type}</td>
                    <td className="max-w-[340px] px-5 py-3">
                      <div className="font-medium text-foreground">
                        {f.requirement_title || "Requirement"}
                      </div>
                    </td>
                    <td className="px-5 py-3">
                      <SeverityBadge severity={f.severity} />
                    </td>
                    <td className="whitespace-nowrap px-5 py-3 tabular text-muted-foreground">
                      {f.sources_count} {f.sources_count === 1 ? "source" : "sources"}
                    </td>
                    <td className="px-5 py-3">
                      <ReviewBadge state={f.review_state} />
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">
                      {f.assigned_to || "Unassigned"}
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">
                      {new Date(f.updated_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
