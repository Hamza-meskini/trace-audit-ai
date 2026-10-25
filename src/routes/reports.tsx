import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { Download, FileSpreadsheet, FileText, Loader2, Table2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel, EmptyState } from "@/components/primitives";
import { Mono } from "@/components/status";
import { useActiveProject } from "@/hooks/use-active-project";
import { useProjectStats } from "@/hooks/use-projects";
import { useRequirements } from "@/hooks/use-requirements";
import { api } from "@/lib/api-client";

export const Route = createFileRoute("/reports")({
  head: () => ({ meta: [{ title: "Audit Reports — TraceAudit" }] }),
  component: ReportsPage,
});

const colors: Record<string, string> = {
  Supported: "var(--success)",
  Partial: "var(--warning)",
  Conflict: "var(--critical)",
  Missing: "oklch(0.62 0.19 27)",
  Unknown: "var(--muted-foreground)",
  "Not applicable": "var(--muted-foreground)",
};

function csvCell(value: unknown) {
  const raw = String(value ?? "");
  const safe = /^\s*[=+\-@]|^[\t\r\n]/.test(raw) ? `'${raw}` : raw;
  return `"${safe.replaceAll('"', '""')}"`;
}

function ReportsPage() {
  const { activeProject, activeProjectId } = useActiveProject();
  const stats = useProjectStats(activeProjectId);
  const requirements = useRequirements(activeProjectId);
  const rows = requirements.data || [];
  const [isExportingXlsx, setIsExportingXlsx] = useState(false);

  const downloadXlsx = async () => {
    if (!activeProjectId) return;
    setIsExportingXlsx(true);
    try {
      const defaultFilename = `${activeProject?.audit_id || "TraceAudit"}_${(activeProject?.name || "Audit").replace(/\s+/g, "_")}_Audit_Report.xlsx`;
      await api.downloadProjectExportXlsx(activeProjectId, defaultFilename);
      toast.success("Audit report exported as Excel (.xlsx) successfully");
    } catch (err) {
      console.error("XLSX export failed:", err);
      toast.error(err instanceof Error ? err.message : "Failed to export Excel report");
    } finally {
      setIsExportingXlsx(false);
    }
  };

  const downloadCsv = () => {
    const columns = [
      "Requirement ID",
      "Title",
      "Category",
      "Severity",
      "AI verdict",
      "Human verdict",
      "Review state",
      "Confidence",
      "Evidence sources",
      "Contract complete",
      "Extraction issues",
      "Unresolved evidence",
      "Source document",
    ];
    const body = rows.map((item) => [
      item.req_code,
      item.title,
      item.category,
      item.severity,
      item.coverage_status,
      item.human_verdict || "",
      item.review_state,
      item.confidence,
      item.sources_count,
      item.contract_complete == null ? "Not recorded" : item.contract_complete,
      item.validation_issue_count || 0,
      item.unresolved_condition_count || 0,
      item.source_document || "",
    ]);
    const blob = new Blob(
      [[columns, ...body].map((row) => row.map(csvCell).join(",")).join("\r\n")],
      { type: "text/csv;charset=utf-8" },
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${activeProject?.audit_id || "traceaudit"}-requirement-register.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (stats.isLoading || requirements.isLoading)
    return (
      <div role="status" className="flex justify-center gap-2 p-20">
        <Loader2 className="size-5 animate-spin" />
        Loading live audit report…
      </div>
    );
  if (stats.isError || requirements.isError)
    return (
      <div role="alert" className="rounded-xl border bg-card p-8 text-sm">
        The live report could not be loaded. No demo data is shown.
      </div>
    );
  if (!stats.data || !rows.length)
    return (
      <EmptyState
        title="No audit results to report"
        description="Upload documents and complete an audit before exporting a requirement register."
      />
    );

  const distribution = [
    "Supported",
    "Partial",
    "Conflict",
    "Missing",
    "Unknown",
    "Not applicable",
  ].map((status) => ({
    status,
    count: rows.filter((item) => item.coverage_status === status).length,
  }));
  const reviewed = rows.filter((item) =>
    ["Reviewed", "Approved", "Rejected"].includes(item.review_state),
  ).length;
  const unresolved = rows.filter(
    (item) => (item.validation_issue_count || 0) + (item.unresolved_condition_count || 0) > 0,
  ).length;

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Audit Report"
        subtitle={`Live results for ${activeProject?.name || "the active project"}.`}
      />
      <Panel bodyClassName="p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h2 className="font-semibold">Requirement audit register</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Executive audit scorecard, color-coded requirement register, evidence traceability
              matrix, and findings.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={downloadCsv}
              className="text-xs text-muted-foreground gap-1.5"
              title="Export raw tabular CSV"
            >
              <Table2 className="size-3.5" />
              Export CSV
            </Button>
            <Button
              size="sm"
              onClick={downloadXlsx}
              disabled={isExportingXlsx}
              className="gap-2 bg-emerald-700 hover:bg-emerald-800 text-white dark:bg-emerald-600 dark:hover:bg-emerald-700 font-medium shadow-sm transition-all"
              title="Export complete executive-grade Excel workbook (.xlsx) with styled sheets and full evidence"
            >
              {isExportingXlsx ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Generating Excel…
                </>
              ) : (
                <>
                  <FileSpreadsheet className="size-4" />
                  Export Audit Report (.xlsx)
                </>
              )}
            </Button>
          </div>
        </div>
      </Panel>

      <article className="mt-5 rounded-xl border border-border bg-card p-8 shadow-card md:p-10">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b pb-6">
          <div>
            <div className="flex items-center gap-2 text-xs uppercase tracking-widest text-muted-foreground">
              <FileText className="size-4" />
              TraceAudit live report
            </div>
            <h2 className="mt-2 text-2xl font-semibold">{activeProject?.product_name}</h2>
            <p className="text-sm text-muted-foreground">
              {activeProject?.company || "Company not recorded"}
            </p>
          </div>
          <dl className="text-right text-sm">
            <dt className="text-xs text-muted-foreground">Audit ID</dt>
            <dd>
              <Mono>{activeProject?.audit_id}</Mono>
            </dd>
            <dt className="mt-2 text-xs text-muted-foreground">Generated</dt>
            <dd>{new Date().toLocaleDateString()}</dd>
          </dl>
        </header>
        <section className="mt-7 grid grid-cols-2 gap-5 md:grid-cols-4">
          {[
            ["Requirements", stats.data.requirements],
            ["Human reviewed", reviewed],
            ["Need review", rows.length - reviewed],
            ["With validation issues", unresolved],
          ].map(([label, value]) => (
            <div key={String(label)}>
              <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
            </div>
          ))}
        </section>
        <section className="mt-8">
          <h3 className="text-sm font-semibold">AI verdict distribution</h3>
          <div className="mt-3 flex h-3 overflow-hidden rounded-md border">
            {distribution
              .filter((item) => item.count)
              .map((item) => (
                <div
                  key={item.status}
                  title={`${item.status}: ${item.count}`}
                  style={{
                    width: `${(item.count / rows.length) * 100}%`,
                    backgroundColor: colors[item.status],
                  }}
                />
              ))}
          </div>
          <ul className="mt-3 flex flex-wrap gap-4 text-xs text-muted-foreground">
            {distribution.map((item) => (
              <li key={item.status} className="flex items-center gap-1.5">
                <span
                  className="size-2 rounded-sm"
                  style={{ backgroundColor: colors[item.status] }}
                />
                {item.status} · {item.count}
              </li>
            ))}
          </ul>
        </section>
        <p className="mt-8 rounded-lg border bg-surface/70 p-4 text-xs leading-relaxed text-muted-foreground">
          AI verdicts and human-assessed verdicts are reported separately. This is an AI-assisted
          traceability record, not a certification or determination of regulatory conformity.
        </p>
      </article>
    </div>
  );
}
