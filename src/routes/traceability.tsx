import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ArrowRight, FileText, GitBranch, Loader2, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import { PageHeader, EmptyState } from "@/components/primitives";
import { CoverageBadge, ReviewBadge } from "@/components/status";
import { useActiveProject } from "@/hooks/use-active-project";
import { useRequirements } from "@/hooks/use-requirements";

export const Route = createFileRoute("/traceability")({
  head: () => ({ meta: [{ title: "Traceability Register — TraceAudit" }] }),
  component: TraceabilityPage,
});

function TraceabilityPage() {
  const navigate = useNavigate();
  const { activeProject, activeProjectId } = useActiveProject();
  const query = useRequirements(activeProjectId);
  const [search, setSearch] = useState("");
  const rows = useMemo(
    () =>
      (query.data || []).filter(
        (item) =>
          !search ||
          `${item.req_code} ${item.title} ${item.source_document || ""}`
            .toLowerCase()
            .includes(search.toLowerCase()),
      ),
    [query.data, search],
  );

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Traceability register"
        subtitle={`Live requirement-to-source-to-evidence links for ${activeProject?.name || "the active project"}.`}
      />
      <div className="mb-4 rounded-xl border bg-card p-4">
        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search requirement or document…"
          />
        </div>
      </div>
      {query.isLoading ? (
        <div role="status" className="flex justify-center gap-2 p-20">
          <Loader2 className="size-5 animate-spin" />
          Loading traceability…
        </div>
      ) : query.isError ? (
        <div role="alert" className="rounded-xl border bg-card p-8 text-sm">
          Traceability data could not be loaded. No example graph is being substituted.
        </div>
      ) : !rows.length ? (
        <EmptyState
          title="No traceability links found"
          description="Complete an audit or adjust the search to see live requirement links."
        />
      ) : (
        <div className="space-y-3">
          {rows.map((item) => (
            <button
              key={item.id}
              onClick={() =>
                navigate({
                  to: "/requirements/$id",
                  params: { id: item.id },
                  search: {
                    tab: "All",
                    query: "",
                    category: "all",
                    severity: "all",
                    document: "all",
                    issue: "all",
                    sort: "asc",
                  },
                })
              }
              className="grid w-full gap-3 rounded-xl border bg-card p-4 text-left transition-colors hover:bg-accent/30 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,.8fr)_auto_minmax(0,.6fr)] lg:items-center"
            >
              <div className="min-w-0">
                <p className="font-mono text-xs text-primary">{item.req_code}</p>
                <p className="mt-1 truncate text-sm font-medium">{item.title}</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <CoverageBadge status={item.coverage_status} />
                  <ReviewBadge state={item.review_state} />
                </div>
              </div>
              <ArrowRight className="hidden size-4 text-muted-foreground lg:block" />
              <div className="min-w-0 rounded-lg bg-muted/40 p-3">
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <FileText className="size-3.5" />
                  Requirement source
                </p>
                <p className="mt-1 truncate text-sm">
                  {item.source_document || "Unresolved source"}
                </p>
                {item.contract_complete === false && (
                  <p className="mt-1 text-xs text-warning">Contract needs confirmation</p>
                )}
              </div>
              <ArrowRight className="hidden size-4 text-muted-foreground lg:block" />
              <div className="rounded-lg bg-muted/40 p-3">
                <p className="flex items-center gap-2 text-xs text-muted-foreground">
                  <GitBranch className="size-3.5" />
                  Evidence links
                </p>
                <p className="mt-1 text-sm font-medium">{item.sources_count}</p>
                {item.unresolved_condition_count ? (
                  <p className="mt-1 text-xs text-warning">
                    {item.unresolved_condition_count} unresolved
                  </p>
                ) : null}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
