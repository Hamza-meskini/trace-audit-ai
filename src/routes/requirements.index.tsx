import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";
import { AlertCircle, ArrowUpDown, Search, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, Panel, EmptyState } from "@/components/primitives";
import { CoverageBadge, Mono, ReviewBadge } from "@/components/status";
import { useActiveProject } from "@/hooks/use-active-project";
import { useRequirements } from "@/hooks/use-requirements";
import { useDocuments } from "@/hooks/use-documents";
import { useAuditProgress } from "@/hooks/use-audit";
import { cn } from "@/lib/utils";
import {
  normalizeRequirementQueueSearch,
  parseRequirementQueueSearch,
  type RequirementQueueSearch,
} from "@/lib/requirement-search";

export const Route = createFileRoute("/requirements/")({
  validateSearch: parseRequirementQueueSearch,
  head: () => ({
    meta: [
      { title: "Requirements Matrix — TraceAudit" },
      {
        name: "description",
        content: "Traceability matrix for technical requirements and evidence.",
      },
      { property: "og:title", content: "Requirements Matrix — TraceAudit" },
      {
        property: "og:description",
        content: "Filter requirements by coverage status, category and review state.",
      },
    ],
  }),
  component: RequirementsPage,
});

const tabs = [
  "All",
  "Supported",
  "Partial",
  "Missing",
  "Conflict",
  "Unknown",
  "Not applicable",
  "Needs review",
] as const;

function RequirementsPage() {
  const navigate = useNavigate();
  const { activeProjectId } = useActiveProject();
  const search = normalizeRequirementQueueSearch(Route.useSearch());
  const tab = tabs.includes(search.tab as (typeof tabs)[number])
    ? (search.tab as (typeof tabs)[number])
    : "All";
  const updateSearch = (patch: RequirementQueueSearch) =>
    navigate({ to: "/requirements", search: { ...search, ...patch }, replace: true });

  const { data: requirementsList, isLoading, isError, refetch } = useRequirements(activeProjectId);
  const { data: auditJob } = useAuditProgress(activeProjectId);
  const auditActive =
    auditJob?.status === "queued" ||
    auditJob?.status === "running" ||
    auditJob?.status === "cancelling";

  const { data: documentsList } = useDocuments(activeProjectId);
  const categories = useMemo(
    () => [...new Set((requirementsList || []).map((item) => item.category))].sort(),
    [requirementsList],
  );

  const rows = useMemo(() => {
    const list = requirementsList || [];
    const filtered = list.filter((r) => {
      if (tab === "Needs review" && r.review_state !== "Needs review") return false;
      if (tab !== "All" && tab !== "Needs review" && r.coverage_status !== tab) return false;
      if (search.category !== "all" && r.category !== search.category) return false;
      if (search.severity !== "all" && r.severity !== search.severity) return false;
      if (search.document !== "all" && r.source_document !== search.document) return false;
      if (search.issue === "contract" && r.contract_complete !== false && !r.validation_issue_count)
        return false;
      if (search.issue === "evidence" && !r.unresolved_condition_count) return false;
      if (search.issue === "none" && r.sources_count > 0) return false;
      if (
        search.query &&
        !`${r.req_code} ${r.title}`.toLowerCase().includes(search.query.toLowerCase())
      )
        return false;
      return true;
    });
    return [...filtered].sort((a, b) =>
      search.sort === "asc"
        ? a.req_code.localeCompare(b.req_code)
        : b.req_code.localeCompare(a.req_code),
    );
  }, [requirementsList, tab, search]);
  const reviewed = (requirementsList || []).filter((item) =>
    ["Reviewed", "Approved", "Rejected"].includes(item.review_state),
  ).length;

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Requirements"
        subtitle="Review extracted requirements and their evidence coverage."
        actions={
          <span className="text-xs text-muted-foreground">
            {reviewed} reviewed · {(requirementsList?.length || 0) - reviewed} remaining
          </span>
        }
      />

      <Panel bodyClassName="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap rounded-lg border border-border bg-surface p-0.5">
            {tabs.map((t) => (
              <button
                key={t}
                onClick={() => updateSearch({ tab: t })}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                  tab === t
                    ? "bg-card text-foreground shadow-subtle"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t}
              </button>
            ))}
          </div>

          <div className="relative ml-auto w-full sm:w-64">
            <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search.query}
              onChange={(e) => updateSearch({ query: e.target.value })}
              placeholder="Search requirements..."
              className="h-9 pl-8"
            />
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <Select value={search.category} onValueChange={(category) => updateSearch({ category })}>
            <SelectTrigger className="h-9 w-[170px]">
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

          <Select value={search.severity} onValueChange={(severity) => updateSearch({ severity })}>
            <SelectTrigger className="h-9 w-[150px]">
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

          <Select value={search.document} onValueChange={(document) => updateSearch({ document })}>
            <SelectTrigger className="h-9 w-[260px]">
              <SelectValue placeholder="Source document" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All source documents</SelectItem>
              {(documentsList || []).map((d) => (
                <SelectItem key={d.id} value={d.original_filename}>
                  {d.original_filename}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select value={search.issue} onValueChange={(issue) => updateSearch({ issue })}>
            <SelectTrigger className="h-9 w-[190px]">
              <SelectValue placeholder="Review issue" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All review issues</SelectItem>
              <SelectItem value="contract">Extraction issues</SelectItem>
              <SelectItem value="evidence">Unresolved evidence</SelectItem>
              <SelectItem value="none">No linked evidence</SelectItem>
            </SelectContent>
          </Select>

          <Button
            variant="ghost"
            size="sm"
            onClick={() => updateSearch({ sort: search.sort === "asc" ? "desc" : "asc" })}
          >
            <ArrowUpDown className="size-4" />
            Sort by ID
          </Button>
          <span className="ml-auto self-center text-xs text-muted-foreground">
            {rows.length} shown
          </span>
        </div>
      </Panel>

      <Panel className="mt-4" bodyClassName="p-0">
        {isLoading ? (
          <div className="flex items-center justify-center p-12">
            <Loader2 className="size-6 animate-spin text-primary" />
          </div>
        ) : isError ? (
          <div role="alert" className="flex items-center justify-between gap-4 p-6 text-sm">
            <span className="flex items-center gap-2">
              <AlertCircle className="size-4" />
              Requirements could not be loaded.
            </span>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <div className="p-6">
            <EmptyState
              title="No requirements match these filters."
              description="Adjust the coverage status, category or search term to see results."
              action={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    updateSearch({
                      tab: "All",
                      query: "",
                      category: "all",
                      severity: "all",
                      document: "all",
                      issue: "all",
                      sort: "asc",
                    });
                  }}
                >
                  Reset filters
                </Button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-sm">
              <thead className="border-b border-border bg-card">
                <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <th className="px-5 py-2.5 font-medium">ID</th>
                  <th className="px-5 py-2.5 font-medium">Requirement</th>
                  <th className="px-5 py-2.5 font-medium">Category</th>
                  <th className="px-5 py-2.5 font-medium">Evidence</th>
                  <th className="px-5 py-2.5 font-medium">Status</th>
                  <th className="px-5 py-2.5 font-medium">Confidence</th>
                  <th className="px-5 py-2.5 font-medium">Review</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.id}
                    onClick={() =>
                      navigate({
                        to: "/requirements/$id",
                        params: { id: r.id },
                        search,
                      })
                    }
                    className="cursor-pointer border-b border-border/70 transition-colors last:border-0 hover:bg-accent/50"
                  >
                    <td className="px-5 py-3">
                      <Mono className="text-foreground">{r.req_code}</Mono>
                    </td>
                    <td className="max-w-[420px] px-5 py-3 font-medium">{r.title}</td>
                    <td className="px-5 py-3 text-muted-foreground">{r.category}</td>
                    <td className="px-5 py-3 tabular text-muted-foreground">
                      {r.sources_count} sources
                    </td>
                    <td className="px-5 py-3">
                      <div className="space-y-1">
                        <CoverageBadge status={r.coverage_status} />
                        {r.human_verdict && (
                          <p className="text-[11px] text-muted-foreground">
                            Human: {r.human_verdict}
                          </p>
                        )}
                        {auditActive && (
                          <p className="text-[11px] text-muted-foreground">
                            {r.assessment_run_id === auditJob?.run_id
                              ? "Updated in current run"
                              : "Previous run result"}
                          </p>
                        )}
                        {r.source_sync_status === "removed_from_source" && (
                          <p className="text-[11px] text-warning">Removed from latest source</p>
                        )}
                        {r.validation_issue_count || r.unresolved_condition_count ? (
                          <p className="text-[11px] text-warning">
                            {r.validation_issue_count || 0} extraction ·{" "}
                            {r.unresolved_condition_count || 0} evidence issue(s)
                          </p>
                        ) : null}
                      </div>
                    </td>
                    <td className="px-5 py-3 tabular">{Math.round(r.confidence)}%</td>
                    <td className="px-5 py-3">
                      <ReviewBadge state={r.review_state} />
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
