import { createFileRoute, Link } from "@tanstack/react-router";
import { Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/primitives";
import { Tag } from "@/components/status";
import { useActiveProject } from "@/hooks/use-active-project";
import { useRequirements } from "@/hooks/use-requirements";

export const Route = createFileRoute("/frameworks")({
  head: () => ({
    meta: [
      { title: "Standards Reference — TraceAudit" },
      {
        name: "description",
        content:
          "Reference list of common technical standards. Not configured per project; TraceAudit does not check compliance against them.",
      },
      { property: "og:title", content: "Standards Reference — TraceAudit" },
      {
        property: "og:description",
        content: "Automotive, aerospace, safety and cybersecurity standards, for reference.",
      },
    ],
  }),
  component: FrameworksPage,
});

export interface IndustryFramework {
  id: string;
  name: string;
  code: string;
  authority: string;
  description: string;
  /** Requirement category most often associated with this standard. */
  category: string;
}

/**
 * Static reference content. Nothing here is loaded from or stored for a
 * project: TraceAudit audits requirements extracted from uploaded documents,
 * not these standards.
 */
const FRAMEWORKS: IndustryFramework[] = [
  {
    id: "fmvss-305",
    name: "FMVSS 305 / 305a",
    code: "49 CFR § 571.305",
    authority: "NHTSA / DOT",
    description:
      "Electric vehicle safety standards, high-voltage electrical isolation, barrier crash protection, and battery safety.",
    category: "Safety",
  },
  {
    id: "iso-26262",
    name: "ISO 26262 (ASIL A–D)",
    code: "ISO 26262:2018",
    authority: "ISO",
    description:
      "Road vehicles functional safety standard for electrical and electronic systems in series production.",
    category: "Safety",
  },
  {
    id: "un-ece-r100",
    name: "UN ECE Regulation 100",
    code: "UN ECE R100 Rev. 3",
    authority: "UNECE",
    description:
      "Approval requirements concerning the electric power train and rechargeable energy storage systems (REESS).",
    category: "Electrical",
  },
  {
    id: "iso-21434",
    name: "ISO/SAE 21434",
    code: "ISO/SAE 21434:2021",
    authority: "ISO / SAE",
    description:
      "Road vehicles cybersecurity engineering risk management and threat analysis throughout the lifecycle.",
    category: "Cybersecurity",
  },
  {
    id: "do-178c",
    name: "DO-178C / ED-12C",
    code: "RTCA DO-178C",
    authority: "FAA / EASA",
    description:
      "Software considerations in airborne systems and equipment certification for civil avionics programs.",
    category: "Documentation",
  },
  {
    id: "aspice",
    name: "Automotive SPICE (ASPICE)",
    code: "VDA QMC v3.1",
    authority: "VDA QMC",
    description:
      "Standard process assessment model for software and embedded system development in automotive supply chains.",
    category: "Documentation",
  },
];

function FrameworksPage() {
  const { activeProject, activeProjectId } = useActiveProject();
  const { data: requirements, isSuccess } = useRequirements(activeProjectId);
  const reqList = requirements || [];

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Standards reference"
        subtitle="Common standards in this domain, listed for orientation only."
      />

      <div
        role="note"
        className="mb-4 flex gap-3 rounded-xl border border-border bg-card p-4 text-sm"
      >
        <Info className="mt-0.5 size-4 shrink-0 text-primary" />
        <p className="leading-relaxed text-muted-foreground">
          These standards are not configured for {activeProject?.name || "this project"}, and
          TraceAudit does not check compliance against them. Audits cover the requirements extracted
          from your uploaded documents. The counts below are requirements in a related category, not
          a mapping to the standard.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {FRAMEWORKS.map((f) => {
          // Exact category match, the same filter the requirements page applies.
          const category = reqList.find(
            (r) => r.category?.toLowerCase() === f.category.toLowerCase(),
          )?.category;
          const count = category ? reqList.filter((r) => r.category === category).length : 0;

          return (
            <Panel key={f.id} title={f.name}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-primary">{f.code}</span>
                <Tag>Reference only</Tag>
              </div>
              <p className="mt-2 text-xs font-medium text-muted-foreground">{f.authority}</p>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{f.description}</p>
              <div className="mt-4 text-sm">
                {!activeProjectId ? (
                  <span className="text-muted-foreground">No active project.</span>
                ) : !isSuccess ? (
                  <span className="text-muted-foreground">Requirement counts unavailable.</span>
                ) : (
                  <>
                    <span className="text-2xl font-semibold tabular">{count}</span>
                    <span className="ml-1.5 text-xs text-muted-foreground">
                      requirements in the {f.category} category
                    </span>
                  </>
                )}
              </div>
              {category ? (
                <Button variant="outline" size="sm" className="mt-4 w-full" asChild>
                  <Link to="/requirements" search={{ category }}>
                    View {category} requirements
                  </Link>
                </Button>
              ) : null}
            </Panel>
          );
        })}
      </div>

      <p className="mt-4 rounded-lg border border-border bg-surface/70 p-4 text-xs leading-relaxed text-muted-foreground">
        Final compliance determination remains with the manufacturer, system safety engineers, and
        certifying authorities.
      </p>
    </div>
  );
}
