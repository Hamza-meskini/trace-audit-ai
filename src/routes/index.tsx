import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import {
  ArrowRight,
  FileUp,
  ListPlus,
  Plus,
  ChevronRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState, KpiCard, PageHeader, Panel } from "@/components/primitives";
import { CoverageBadge, Mono, ReviewBadge, SeverityBadge } from "@/components/status";
import { AiStatus } from "@/components/app-shell";
import {
  coverageData,
  projectStats,
  recentFindings,
  severityCounts,
} from "@/lib/mock-data";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Audit Overview — TraceAudit" },
      {
        name: "description",
        content:
          "Monitor requirements coverage, evidence quality and unresolved findings for your technical documentation audit.",
      },
      { property: "og:title", content: "Audit Overview — TraceAudit" },
      {
        property: "og:description",
        content: "Requirement coverage, evidence quality and open findings in one workspace.",
      },
    ],
  }),
  component: Dashboard,
});

const coverageColors = [
  "var(--success)",
  "var(--warning)",
  "var(--critical)",
  "oklch(0.62 0.19 27)",
];

const severityOrder = [
  { key: "Critical", color: "bg-critical" },
  { key: "High", color: "bg-warning" },
  { key: "Medium", color: "bg-primary" },
  { key: "Low", color: "bg-muted-foreground/40" },
] as const;

function Dashboard() {
  const navigate = useNavigate();
  const severityTotal = Object.values(severityCounts).reduce((a, b) => a + b, 0);

  return (
    <div className="mx-auto max-w-[1400px]">
      <section className="mb-6 overflow-hidden rounded-xl border border-border bg-card p-6 shadow-subtle md:p-8">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div className="max-w-2xl">
            <AiStatus />
            <h2 className="mt-3 text-2xl font-semibold tracking-tight md:text-[28px]">
              Turn technical documentation into traceable evidence.
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              Analyze requirements, technical documents, and test evidence to identify coverage
              gaps, inconsistencies, and items requiring human review.
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              <Button onClick={() => navigate({ to: "/new-audit" })}>
                Start an audit
                <ArrowRight className="size-4" />
              </Button>
              <Button variant="outline" onClick={() => navigate({ to: "/projects" })}>
                View demo audit
              </Button>
            </div>
          </div>
          <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
            <div>
              <dt className="text-xs text-muted-foreground">Project</dt>
              <dd className="font-medium">Industrial Controller X200</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Company</dt>
              <dd className="font-medium">Atlas Motion Systems</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Audit ID</dt>
              <dd className="font-mono text-xs">TA-2026-0042</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Status</dt>
              <dd className="font-medium text-success">Analysis completed</dd>
            </div>
          </dl>
        </div>
      </section>

      <PageHeader
        title="Audit Overview"
        subtitle="Monitor requirements coverage, evidence quality, and unresolved findings."
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => navigate({ to: "/reports" })}>
              Reports
            </Button>
            <Button size="sm" onClick={() => navigate({ to: "/new-audit" })}>
              <Plus className="size-4" />
              New Audit
            </Button>
          </>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          label="Requirements"
          value={projectStats.requirements}
          hint="Total requirements analyzed"
          accent="info"
        />
        <KpiCard
          label="Evidence Coverage"
          value={`${projectStats.coverage}%`}
          hint={`${projectStats.supported} requirements fully supported`}
          accent="success"
        />
        <KpiCard
          label="Open Findings"
          value={projectStats.findings}
          hint={`${projectStats.missing} missing · ${projectStats.partial} partial · ${projectStats.conflict} conflicts`}
          accent="warning"
        />
        <KpiCard
          label="Documents"
          value={projectStats.documents}
          hint={`${projectStats.evidenceSegments.toLocaleString()} evidence segments indexed`}
          accent="info"
        />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Panel
          title="Requirement Coverage"
          description="Distribution of evidence status across all analyzed requirements."
          className="lg:col-span-2"
        >
          <div className="relative h-[220px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={coverageData}
                  dataKey="value"
                  innerRadius={72}
                  outerRadius={98}
                  paddingAngle={2}
                  stroke="none"
                >
                  {coverageData.map((entry, i) => (
                    <Cell key={entry.key} fill={coverageColors[i]} />
                  ))}
                </Pie>
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-3xl font-semibold tabular">{projectStats.coverage}%</span>
              <span className="text-xs text-muted-foreground">Fully supported</span>
            </div>
          </div>
          <ul className="mt-4 grid grid-cols-2 gap-2 text-sm">
            {coverageData.map((entry, i) => (
              <li key={entry.key} className="flex items-center gap-2">
                <span
                  className="size-2 rounded-[3px]"
                  style={{ backgroundColor: coverageColors[i] }}
                />
                <span className="text-muted-foreground">{entry.name}</span>
                <span className="ml-auto font-medium tabular">{entry.value}</span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel
          title="Findings requiring attention"
          description="Severity distribution of unresolved findings."
          className="lg:col-span-3"
          actions={
            <Button variant="outline" size="sm" onClick={() => navigate({ to: "/findings" })}>
              View all findings
            </Button>
          }
        >
          <div className="flex h-3 w-full overflow-hidden rounded-md border border-border">
            {severityOrder.map((s) => (
              <div
                key={s.key}
                className={s.color}
                style={{ width: `${(severityCounts[s.key] / severityTotal) * 100}%` }}
                title={`${s.key}: ${severityCounts[s.key]}`}
              />
            ))}
          </div>
          <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {severityOrder.map((s) => (
              <div key={s.key} className="rounded-lg border border-border bg-surface/60 p-3">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className={`size-2 rounded-[3px] ${s.color}`} />
                  {s.key}
                </div>
                <div className="mt-1.5 text-2xl font-semibold tabular">
                  {severityCounts[s.key]}
                </div>
              </div>
            ))}
          </div>
          <div className="mt-5 rounded-lg border border-border bg-surface/60 p-4">
            <div className="text-sm font-medium">Start a new audit</div>
            <p className="mt-1 text-sm text-muted-foreground">
              Analyze requirements and supporting technical documentation.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" onClick={() => navigate({ to: "/new-audit" })}>
                <Plus className="size-4" />
                New Audit
              </Button>
              <Button variant="outline" size="sm" onClick={() => navigate({ to: "/documents" })}>
                <FileUp className="size-4" />
                Upload Documents
              </Button>
              <Button variant="outline" size="sm" onClick={() => navigate({ to: "/requirements" })}>
                <ListPlus className="size-4" />
                Import Requirements
              </Button>
            </div>
          </div>
        </Panel>
      </div>

      <Panel
        className="mt-4"
        title="Recent findings"
        description="Latest items detected across the indexed document set."
        bodyClassName="p-0"
        actions={
          <Button variant="ghost" size="sm" onClick={() => navigate({ to: "/findings" })}>
            All findings
            <ChevronRight className="size-4" />
          </Button>
        }
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="sticky top-0 bg-card">
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="px-5 py-2.5 font-medium">ID</th>
                <th className="px-5 py-2.5 font-medium">Requirement</th>
                <th className="px-5 py-2.5 font-medium">Type</th>
                <th className="px-5 py-2.5 font-medium">Severity</th>
                <th className="px-5 py-2.5 font-medium">Status</th>
                <th className="px-5 py-2.5 font-medium">Evidence</th>
                <th className="px-5 py-2.5 font-medium">Last updated</th>
              </tr>
            </thead>
            <tbody>
              {recentFindings.map((f) => (
                <tr
                  key={f.id}
                  onClick={() => navigate({ to: "/requirements/$id", params: { id: f.link } })}
                  className="cursor-pointer border-b border-border/70 transition-colors last:border-0 hover:bg-accent/50"
                >
                  <td className="px-5 py-3">
                    <Mono className="text-foreground">{f.id}</Mono>
                  </td>
                  <td className="px-5 py-3 font-medium">{f.requirement}</td>
                  <td className="px-5 py-3 text-muted-foreground">{f.type}</td>
                  <td className="px-5 py-3">
                    <SeverityBadge severity={f.severity} />
                  </td>
                  <td className="px-5 py-3">
                    <ReviewBadge state={f.status} />
                  </td>
                  <td className="px-5 py-3 tabular text-muted-foreground">{f.evidence}</td>
                  <td className="px-5 py-3 text-muted-foreground">{f.updated}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel title="Coverage by status" bodyClassName="p-5">
          <ul className="space-y-3">
            {coverageData.map((c, i) => (
              <li key={c.key}>
                <div className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2">
                    <CoverageBadge status={c.name as never} />
                  </span>
                  <span className="tabular text-muted-foreground">
                    {Math.round((c.value / projectStats.requirements) * 100)}%
                  </span>
                </div>
                <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${(c.value / projectStats.requirements) * 100}%`,
                      backgroundColor: coverageColors[i],
                    }}
                  />
                </div>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="Your review queue">
          <EmptyState
            title="No findings require your attention."
            description="Your current audit has no unresolved findings assigned to you."
            action={
              <Button variant="outline" size="sm" asChild>
                <Link to="/findings">Browse team findings</Link>
              </Button>
            }
          />
        </Panel>
      </div>
    </div>
  );
}
