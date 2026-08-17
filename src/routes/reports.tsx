import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Download, FileText, Table2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/primitives";
import { Mono } from "@/components/status";
import { coverageData, projectStats, reports } from "@/lib/mock-data";

export const Route = createFileRoute("/reports")({
  head: () => ({
    meta: [
      { title: "Audit Reports — TraceAudit" },
      { name: "description", content: "Generate and preview auditable technical evidence reports." },
      { property: "og:title", content: "Audit Reports — TraceAudit" },
      {
        property: "og:description",
        content: "Executive summaries, coverage, missing evidence and traceability in one report.",
      },
    ],
  }),
  component: ReportsPage,
});

const colors = ["var(--success)", "var(--warning)", "var(--critical)", "oklch(0.62 0.19 27)"];

function ReportsPage() {
  const [preview, setPreview] = useState(true);

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Audit Reports"
        subtitle="Generated reports for the Industrial Controller X200 audit."
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {reports.map((r) => (
          <Panel key={r.id} title={r.name} description={`${r.project} · ${r.generated}`}>
            <ul className="space-y-1.5 text-sm text-muted-foreground">
              {r.sections.map((s) => (
                <li key={s} className="flex items-center gap-2">
                  <span className="size-1 rounded-full bg-muted-foreground/60" />
                  {s}
                </li>
              ))}
            </ul>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => setPreview(true)}>
                Preview
              </Button>
              <Button size="sm" variant="outline" onClick={() => toast.success("PDF export started")}>
                <Download className="size-4" />
                Export PDF
              </Button>
              <Button size="sm" variant="ghost" onClick={() => toast.success("CSV export started")}>
                <Table2 className="size-4" />
                Export CSV
              </Button>
            </div>
          </Panel>
        ))}
      </div>

      {preview && (
        <div className="mt-6 rounded-xl border border-border bg-card p-8 shadow-card md:p-12">
          <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-6">
            <div>
              <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-muted-foreground">
                <FileText className="size-4" />
                TraceAudit report
              </div>
              <h2 className="mt-2 text-2xl font-semibold tracking-tight">
                Technical Evidence Audit
              </h2>
              <p className="text-sm text-muted-foreground">Industrial Controller X200</p>
            </div>
            <dl className="text-right text-sm">
              <dt className="text-xs text-muted-foreground">Audit ID</dt>
              <dd>
                <Mono>TA-2026-0042</Mono>
              </dd>
              <dt className="mt-2 text-xs text-muted-foreground">Analysis date</dt>
              <dd>17 August 2026</dd>
            </dl>
          </header>

          <section className="mt-8 grid grid-cols-2 gap-6 md:grid-cols-5">
            {[
              ["Requirements analyzed", projectStats.requirements],
              ["Fully supported", projectStats.supported],
              ["Partial", projectStats.partial],
              ["Missing", projectStats.missing],
              ["Potential conflicts", projectStats.conflict],
            ].map(([label, value]) => (
              <div key={label as string}>
                <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
                <div className="mt-1 text-2xl font-semibold tabular">{value}</div>
              </div>
            ))}
          </section>

          <section className="mt-8">
            <div className="flex items-baseline justify-between text-sm">
              <span className="font-medium">Overall evidence coverage</span>
              <span className="tabular text-lg font-semibold">{projectStats.coverage}%</span>
            </div>
            <div className="mt-2 flex h-3 overflow-hidden rounded-md border border-border">
              {coverageData.map((c, i) => (
                <div
                  key={c.key}
                  style={{
                    width: `${(c.value / projectStats.requirements) * 100}%`,
                    backgroundColor: colors[i],
                  }}
                  title={`${c.name}: ${c.value}`}
                />
              ))}
            </div>
            <ul className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
              {coverageData.map((c, i) => (
                <li key={c.key} className="flex items-center gap-1.5">
                  <span className="size-2 rounded-[3px]" style={{ backgroundColor: colors[i] }} />
                  {c.name} · {c.value}
                </li>
              ))}
            </ul>
          </section>

          <section className="mt-8">
            <h3 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Key observations
            </h3>
            <ul className="mt-3 space-y-2 text-sm leading-relaxed">
              <li>82% of requirements have complete supporting evidence.</li>
              <li>18 requirements currently have no identified evidence.</li>
              <li>14 potential inconsistencies were detected across technical documents.</li>
              <li>23 findings require human review.</li>
            </ul>
          </section>

          <p className="mt-8 rounded-lg border border-border bg-surface/70 p-4 text-xs leading-relaxed text-muted-foreground">
            This report provides AI-assisted technical documentation analysis and evidence
            traceability. It does not constitute legal advice, certification, or a determination of
            regulatory conformity. Final assessment remains the responsibility of the manufacturer
            and relevant qualified professionals.
          </p>
        </div>
      )}
    </div>
  );
}
