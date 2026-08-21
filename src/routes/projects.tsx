import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { MetaItem, PageHeader, Panel } from "@/components/primitives";
import { CoverageBadge, Mono, ReviewBadge, SeverityBadge, Tag } from "@/components/status";
import { useProjects, useProjectStats } from "@/hooks/use-projects";
import { useDocuments } from "@/hooks/use-documents";
import { useRequirements } from "@/hooks/use-requirements";
import { useFindings } from "@/hooks/use-findings";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/projects")({
  head: () => ({
    meta: [
      { title: "Industrial Controller X200 — TraceAudit" },
      {
        name: "description",
        content:
          "Project workspace for the X200 technical documentation audit: coverage, documents, findings and traceability.",
      },
      { property: "og:title", content: "Industrial Controller X200 — TraceAudit" },
      {
        property: "og:description",
        content: "Audit progress, evidence links and human review status for the X200 program.",
      },
    ],
  }),
  component: ProjectPage,
});

const tabs = ["Overview", "Requirements", "Documents", "Findings", "Traceability", "Reports"] as const;

import { useActiveProject } from "@/hooks/use-active-project";

function ProjectPage() {
  const [tab, setTab] = useState<(typeof tabs)[number]>("Overview");
  const navigate = useNavigate();

  const { activeProject: project, activeProjectId, projects: projectsList, selectProject } = useActiveProject();

  const { data: stats } = useProjectStats(activeProjectId);
  const { data: docs } = useDocuments(activeProjectId);
  const { data: reqs } = useRequirements(activeProjectId);
  const { data: findingsList } = useFindings(activeProjectId);

  const projectStats = stats || {
    requirements: 12,
    coverage: 58,
    supported: 7,
    partial: 1,
    missing: 2,
    conflict: 2,
    documents: 6,
    evidence_segments: 15,
    findings: 5,
  };

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title={project.name}
        subtitle={`${project.company} · ${project.audit_id} Technical Documentation Audit`}
        actions={
          <>
            <Button variant="outline" size="sm" asChild>
              <Link to="/reports">Generate report</Link>
            </Button>
            <Button size="sm" onClick={() => navigate({ to: "/new-audit" })}>
              <Plus className="size-4" />
              New Audit
            </Button>
          </>
        }
      />

      <Panel bodyClassName="p-5">
        <div className="grid grid-cols-2 gap-5 md:grid-cols-5">
          <MetaItem label="Project ID" value={<Mono>{project.audit_id}</Mono>} />
          <MetaItem label="Product category" value={project.product_category} />
          <MetaItem label="Created" value={new Date(project.created_at).toLocaleDateString()} />
          <MetaItem label="Last analysis" value={new Date(project.updated_at).toLocaleDateString()} />
          <MetaItem
            label="Status"
            value={<span className="text-success">{project.status}</span>}
          />
        </div>
      </Panel>

      <div className="mt-4 flex flex-wrap gap-1 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
              tab === t
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="mt-4">
        {tab === "Overview" && (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Panel title="Audit progress" className="lg:col-span-1">
              <div className="flex items-baseline gap-2">
                <span className="text-4xl font-semibold tabular">{projectStats.coverage}%</span>
                <span className="text-sm text-muted-foreground">coverage</span>
              </div>
              <Progress value={projectStats.coverage} className="mt-3 h-1.5" />
              <ul className="mt-5 space-y-3 text-sm">
                {[
                  ["Documents", projectStats.documents],
                  ["Requirements", projectStats.requirements],
                  ["Evidence segments", projectStats.evidence_segments],
                  ["Open findings", projectStats.findings],
                  ["Fully supported", projectStats.supported],
                ].map(([label, value]) => (
                  <li key={label as string} className="flex items-center justify-between">
                    <span className="text-muted-foreground">{label}</span>
                    <span className="font-semibold tabular">{value}</span>
                  </li>
                ))}
              </ul>
            </Panel>

            <Panel title="All projects" className="lg:col-span-2" bodyClassName="p-0">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                    <th className="px-5 py-2.5 font-medium">Project</th>
                    <th className="px-5 py-2.5 font-medium">Audit ID</th>
                    <th className="px-5 py-2.5 font-medium">Category</th>
                    <th className="px-5 py-2.5 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(projectsList || []).map((p) => (
                    <tr
                      key={p.id}
                      onClick={() => selectProject(p.id)}
                      className={cn(
                        "cursor-pointer border-b border-border/70 last:border-0 hover:bg-accent/50",
                        p.id === project.id ? "bg-accent/30 font-medium" : ""
                      )}
                    >
                      <td className="px-5 py-3 font-medium">{p.name}</td>
                      <td className="px-5 py-3">
                        <Mono className="text-muted-foreground">{p.audit_id}</Mono>
                      </td>
                      <td className="px-5 py-3 text-muted-foreground">{p.product_category}</td>
                      <td className="px-5 py-3">
                        <Tag>{p.status}</Tag>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          </div>
        )}

        {tab === "Requirements" && (
          <Panel
            bodyClassName="p-0"
            actions={
              <Button variant="outline" size="sm" asChild>
                <Link to="/requirements">Open workspace</Link>
              </Button>
            }
            title="Requirements sample"
          >
            <table className="w-full text-sm">
              <tbody>
                {(reqs || []).slice(0, 6).map((r) => (
                  <tr
                    key={r.id}
                    onClick={() => navigate({ to: "/requirements/$id", params: { id: r.id } })}
                    className="cursor-pointer border-b border-border/70 last:border-0 hover:bg-accent/50"
                  >
                    <td className="px-5 py-3">
                      <Mono>{r.req_code}</Mono>
                    </td>
                    <td className="px-5 py-3 font-medium">{r.title}</td>
                    <td className="px-5 py-3">
                      <CoverageBadge status={r.coverage_status} />
                    </td>
                    <td className="px-5 py-3 tabular text-muted-foreground">{r.confidence}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}

        {tab === "Documents" && (
          <Panel
            title="Documents"
            bodyClassName="p-0"
            actions={
              <Button variant="outline" size="sm" asChild>
                <Link to="/documents">Manage documents</Link>
              </Button>
            }
          >
            <table className="w-full text-sm">
              <tbody>
                {(docs || []).map((d) => (
                  <tr key={d.id} className="border-b border-border/70 last:border-0 hover:bg-accent/50">
                    <td className="px-5 py-3 font-medium">{d.original_filename}</td>
                    <td className="px-5 py-3 text-muted-foreground">{d.doc_type}</td>
                    <td className="px-5 py-3">
                      <Mono>{d.version}</Mono>
                    </td>
                    <td className="px-5 py-3 tabular text-muted-foreground">
                      {d.requirements_linked} requirements
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}

        {tab === "Findings" && (
          <Panel
            title="Findings"
            bodyClassName="p-0"
            actions={
              <Button variant="outline" size="sm" asChild>
                <Link to="/findings">Open findings</Link>
              </Button>
            }
          >
            <table className="w-full text-sm">
              <tbody>
                {(findingsList || []).slice(0, 6).map((f) => (
                  <tr key={f.id} className="border-b border-border/70 last:border-0 hover:bg-accent/50">
                    <td className="px-5 py-3">
                      <Mono>{f.finding_code}</Mono>
                    </td>
                    <td className="px-5 py-3">{f.finding_type}</td>
                    <td className="px-5 py-3">
                      <SeverityBadge severity={f.severity} />
                    </td>
                    <td className="px-5 py-3">
                      <ReviewBadge state={f.review_state} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        )}

        {tab === "Traceability" && (
          <Panel title="Traceability">
            <p className="text-sm text-muted-foreground">
              Explore requirement-to-evidence relationships across the indexed document set.
            </p>
            <Button className="mt-4" size="sm" asChild>
              <Link to="/traceability">Open traceability map</Link>
            </Button>
          </Panel>
        )}

        {tab === "Reports" && (
          <Panel title="Reports">
            <p className="text-sm text-muted-foreground">
              Generated audit reports for this project.
            </p>
            <Button className="mt-4" size="sm" asChild>
              <Link to="/reports">Open reports</Link>
            </Button>
          </Panel>
        )}
      </div>
    </div>
  );
}
