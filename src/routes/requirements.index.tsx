import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { ArrowUpDown, Search, SlidersHorizontal } from "lucide-react";
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
import { categories, documents, requirements } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/requirements/")({
  head: () => ({
    meta: [
      { title: "Requirements — TraceAudit" },
      {
        name: "description",
        content: "Review extracted requirements and their evidence coverage.",
      },
      { property: "og:title", content: "Requirements — TraceAudit" },
      {
        property: "og:description",
        content: "Filter requirements by coverage status, category and review state.",
      },
    ],
  }),
  component: RequirementsPage,
});

const tabs = ["All", "Supported", "Partial", "Missing", "Conflict", "Needs review"] as const;

function RequirementsPage() {
  const navigate = useNavigate();
  const [tab, setTab] = useState<(typeof tabs)[number]>("All");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [severity, setSeverity] = useState("all");
  const [doc, setDoc] = useState("all");
  const [sortAsc, setSortAsc] = useState(true);

  const rows = useMemo(() => {
    const filtered = requirements.filter((r) => {
      if (tab === "Needs review" && r.review !== "Needs review") return false;
      if (tab !== "All" && tab !== "Needs review" && r.status !== tab) return false;
      if (category !== "all" && r.category !== category) return false;
      if (severity !== "all" && r.severity !== severity) return false;
      if (doc !== "all" && r.sourceDocument !== doc) return false;
      if (query && !`${r.id} ${r.title}`.toLowerCase().includes(query.toLowerCase())) return false;
      return true;
    });
    return [...filtered].sort((a, b) =>
      sortAsc ? a.id.localeCompare(b.id) : b.id.localeCompare(a.id),
    );
  }, [tab, query, category, severity, doc, sortAsc]);

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Requirements"
        subtitle="Review extracted requirements and their evidence coverage."
        actions={
          <Button variant="outline" size="sm">
            <SlidersHorizontal className="size-4" />
            Import requirements
          </Button>
        }
      />

      <Panel bodyClassName="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap rounded-lg border border-border bg-surface p-0.5">
            {tabs.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
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
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search requirements..."
              className="h-9 pl-8"
            />
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          <Select value={category} onValueChange={setCategory}>
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

          <Select value={severity} onValueChange={setSeverity}>
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

          <Select value={doc} onValueChange={setDoc}>
            <SelectTrigger className="h-9 w-[260px]">
              <SelectValue placeholder="Source document" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All source documents</SelectItem>
              {documents.map((d) => (
                <SelectItem key={d.id} value={d.name}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Button variant="ghost" size="sm" onClick={() => setSortAsc((v) => !v)}>
            <ArrowUpDown className="size-4" />
            Sort by ID
          </Button>
          <span className="ml-auto self-center text-xs text-muted-foreground">
            {rows.length} of {requirements.length} shown · 347 analyzed in full dataset
          </span>
        </div>
      </Panel>

      <Panel className="mt-4" bodyClassName="p-0">
        {rows.length === 0 ? (
          <div className="p-6">
            <EmptyState
              title="No requirements match these filters."
              description="Adjust the coverage status, category or search term to see results."
              action={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setTab("All");
                    setQuery("");
                    setCategory("all");
                    setSeverity("all");
                    setDoc("all");
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
              <thead className="sticky top-14 bg-card">
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
                    onClick={() => navigate({ to: "/requirements/$id", params: { id: r.id } })}
                    className="cursor-pointer border-b border-border/70 transition-colors last:border-0 hover:bg-accent/50"
                  >
                    <td className="px-5 py-3">
                      <Mono className="text-foreground">{r.id}</Mono>
                    </td>
                    <td className="max-w-[420px] px-5 py-3 font-medium">{r.title}</td>
                    <td className="px-5 py-3 text-muted-foreground">{r.category}</td>
                    <td className="px-5 py-3 tabular text-muted-foreground">
                      {r.sources} sources
                    </td>
                    <td className="px-5 py-3">
                      <CoverageBadge status={r.status} />
                    </td>
                    <td className="px-5 py-3 tabular">{r.confidence}%</td>
                    <td className="px-5 py-3">
                      <ReviewBadge state={r.review} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="flex items-center justify-between border-t border-border px-5 py-3 text-xs text-muted-foreground">
          <span>Page 1 of 29</span>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled>
              Previous
            </Button>
            <Button variant="outline" size="sm">
              Next
            </Button>
          </div>
        </div>
      </Panel>
    </div>
  );
}
