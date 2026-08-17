import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { Search } from "lucide-react";
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
import { categories, findings, projectStats, severityCounts } from "@/lib/mock-data";

export const Route = createFileRoute("/findings")({
  head: () => ({
    meta: [
      { title: "Findings — TraceAudit" },
      {
        name: "description",
        content: "Triage missing evidence, partial evidence and potential conflicts.",
      },
      { property: "og:title", content: "Findings — TraceAudit" },
      {
        property: "og:description",
        content: "A workspace to triage and review audit findings requiring human attention.",
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
  const [severity, setSeverity] = useState("all");
  const [type, setType] = useState("all");
  const [status, setStatus] = useState("all");
  const [category, setCategory] = useState("all");
  const [reviewer, setReviewer] = useState("all");
  const [query, setQuery] = useState("");

  const reviewers = Array.from(new Set(findings.map((f) => f.owner)));

  const rows = useMemo(
    () =>
      findings.filter((f) => {
        if (severity !== "all" && f.severity !== severity) return false;
        if (type !== "all" && f.type !== type) return false;
        if (status !== "all" && f.status !== status) return false;
        if (category !== "all" && f.category !== category) return false;
        if (reviewer !== "all" && f.owner !== reviewer) return false;
        if (query && !`${f.id} ${f.requirement} ${f.requirementTitle}`.toLowerCase().includes(query.toLowerCase()))
          return false;
        return true;
      }),
    [severity, type, status, category, reviewer, query],
  );

  const summary = [
    { label: "Total", value: projectStats.findings, tone: "text-foreground" },
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
              {["Open", "Needs review", "Reviewed"].map((s) => (
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
        {rows.length === 0 ? (
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
                      navigate({ to: "/requirements/$id", params: { id: f.requirement } })
                    }
                    className="cursor-pointer border-b border-border/70 transition-colors last:border-0 hover:bg-accent/50"
                  >
                    <td className="px-5 py-3">
                      <Mono className="text-foreground">{f.id}</Mono>
                    </td>
                    <td className="px-5 py-3">{f.type}</td>
                    <td className="max-w-[340px] px-5 py-3">
                      <Mono className="text-muted-foreground">{f.requirement}</Mono>
                      <div className="truncate text-xs text-muted-foreground">
                        {f.requirementTitle}
                      </div>
                    </td>
                    <td className="px-5 py-3">
                      <SeverityBadge severity={f.severity} />
                    </td>
                    <td className="px-5 py-3 tabular text-muted-foreground">{f.sources} sources</td>
                    <td className="px-5 py-3">
                      <ReviewBadge state={f.status} />
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">{f.owner}</td>
                    <td className="px-5 py-3 text-muted-foreground">{f.updated}</td>
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
