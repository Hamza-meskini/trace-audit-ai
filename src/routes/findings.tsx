import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
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
import { Mono, ReviewBadge, SeverityBadge } from "@/components/status";
import { useActiveProject } from "@/hooks/use-active-project";
import { useFindings, useUpdateFinding } from "@/hooks/use-findings";
import { categories } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/findings")({
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

  const [severity, setSeverity] = useState("all");
  const [type, setType] = useState("all");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [reviewer, setReviewer] = useState("all");
  const [query, setQuery] = useState("");

  const { data: findingsList, isLoading } = useFindings(activeProjectId, {
    severity,
    finding_type: type,
    review_state: status,
    category,
  });

  const allFindings = findingsList || [];
  const reviewers = Array.from(
    new Set(allFindings.map((f) => f.assigned_to).filter(Boolean) as string[])
  );

  const rows = useMemo(
    () =>
      allFindings.filter((f) => {
        if (reviewer !== "all" && f.assigned_to !== reviewer) return false;
        if (
          query &&
          !`${f.finding_code} ${f.requirement_title || ""}`.toLowerCase().includes(query.toLowerCase())
        )
          return false;
        return true;
      }),
    [allFindings, reviewer, query],
  );

  const severityCounts = {
    Critical: allFindings.filter((f) => f.severity === "Critical").length,
    High: allFindings.filter((f) => f.severity === "High").length,
    Medium: allFindings.filter((f) => f.severity === "Medium").length,
    Low: allFindings.filter((f) => f.severity === "Low").length,
  };

  const summary = [
    { label: "Total", value: allFindings.length, tone: "text-foreground" },
    { label: "Critical", value: severityCounts.Critical, tone: "text-critical" },
    { label: "High", value: severityCounts.High, tone: "text-warning" },
    { label: "Medium", value: severityCounts.Medium, tone: "text-primary" },
    { label: "Low", value: severityCounts.Low, tone: "text-muted-foreground" },
  ];

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
                  onClick={() => {
                    setSeverity("all");
                    setType("all");
                    setStatus("all");
                    setCategory("all");
                    setReviewer("all");
                    setQuery("");
                  }}
                >
                  Reset filters
                </Button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1040px] text-sm">
              <thead className="sticky top-14 bg-card">
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
                      navigate({
                        to: "/requirements/$id",
                        params: { id: f.requirement_id || "req-001" },
                      })
                    }
                    className="cursor-pointer border-b border-border/70 transition-colors last:border-0 hover:bg-accent/50"
                  >
                    <td className="px-5 py-3">
                      <Mono className="text-foreground">{f.finding_code}</Mono>
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
                    <td className="px-5 py-3 tabular text-muted-foreground">{f.sources_count} sources</td>
                    <td className="px-5 py-3">
                      <ReviewBadge state={f.review_state} />
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">{f.assigned_to || "Unassigned"}</td>
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
