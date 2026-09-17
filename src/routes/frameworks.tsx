import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/primitives";
import { Tag } from "@/components/status";
import { useActiveProject } from "@/hooks/use-active-project";
import { useRequirements } from "@/hooks/use-requirements";

export const Route = createFileRoute("/frameworks")({
  head: () => ({
    meta: [
      { title: "Requirement Frameworks — TraceAudit" },
      {
        name: "description",
        content:
          "Industry regulatory frameworks and verification standards for technical documentation analysis.",
      },
      { property: "og:title", content: "Requirement Frameworks — TraceAudit" },
      {
        property: "og:description",
        content: "Automotive, aerospace, safety, and cybersecurity compliance frameworks.",
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
  status: "Active" | "Available" | "Configuration required";
  description: string;
  categoryFilter?: string;
}

const FRAMEWORKS: IndustryFramework[] = [
  {
    id: "fmvss-305",
    name: "FMVSS 305 / 305a",
    code: "49 CFR § 571.305",
    authority: "NHTSA / DOT",
    status: "Active",
    description:
      "Electric vehicle safety standards, high-voltage electrical isolation, barrier crash protection, and battery safety.",
    categoryFilter: "safety",
  },
  {
    id: "iso-26262",
    name: "ISO 26262 (ASIL A–D)",
    code: "ISO 26262:2018",
    authority: "ISO",
    status: "Active",
    description:
      "Road vehicles functional safety standard for electrical and electronic systems in series production.",
    categoryFilter: "safety",
  },
  {
    id: "un-ece-r100",
    name: "UN ECE Regulation 100",
    code: "UN ECE R100 Rev. 3",
    authority: "UNECE",
    status: "Active",
    description:
      "Approval requirements concerning the electric power train and rechargeable energy storage systems (REESS).",
    categoryFilter: "electrical",
  },
  {
    id: "iso-21434",
    name: "ISO/SAE 21434",
    code: "ISO/SAE 21434:2021",
    authority: "ISO / SAE",
    status: "Available",
    description:
      "Road vehicles cybersecurity engineering risk management and threat analysis throughout the lifecycle.",
    categoryFilter: "cybersecurity",
  },
  {
    id: "do-178c",
    name: "DO-178C / ED-12C",
    code: "RTCA DO-178C",
    authority: "FAA / EASA",
    status: "Available",
    description:
      "Software considerations in airborne systems and equipment certification for civil avionics programs.",
    categoryFilter: "documentation",
  },
  {
    id: "aspice",
    name: "Automotive SPICE (ASPICE)",
    code: "VDA QMC v3.1",
    authority: "VDA QMC",
    status: "Configuration required",
    description:
      "Standard process assessment model for software and embedded system development in automotive supply chains.",
    categoryFilter: "documentation",
  },
];

function FrameworksPage() {
  const { activeProject, activeProjectId } = useActiveProject();
  const { data: requirements } = useRequirements(activeProjectId);
  const reqList = requirements || [];

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Requirement Frameworks & Standards"
        subtitle={`Standards against which ${activeProject?.name || "the active workspace"} is audited.`}
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {FRAMEWORKS.map((f) => {
          const matchingCount = f.categoryFilter
            ? reqList.filter(
                (r) =>
                  r.category?.toLowerCase().includes(f.categoryFilter ?? "") ||
                  r.title?.toLowerCase().includes(f.categoryFilter ?? ""),
              ).length
            : reqList.length;

          return (
            <Panel key={f.id} title={f.name}>
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs text-primary">{f.code}</span>
                <Tag
                  className={
                    f.status === "Active"
                      ? "border-success/25 bg-success-soft text-success"
                      : f.status === "Configuration required"
                        ? "border-warning/30 bg-warning-soft text-warning"
                        : ""
                  }
                >
                  {f.status}
                </Tag>
              </div>
              <p className="mt-2 text-xs font-medium text-muted-foreground">{f.authority}</p>
              <p className="mt-2 text-sm text-muted-foreground leading-relaxed">{f.description}</p>
              <div className="mt-4 text-2xl font-semibold tabular">
                {matchingCount}
                <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                  active requirements matched
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="mt-4 w-full"
                onClick={() =>
                  toast.info(
                    `${f.name} framework active for ${activeProject?.name || "current project"}`,
                  )
                }
              >
                Inspect standard requirements
              </Button>
            </Panel>
          );
        })}
      </div>

      <Panel className="mt-4" title="Active audit framework alignment">
        <div className="text-sm font-medium">
          {activeProject?.name || "Active Workspace"} ·{" "}
          {activeProject?.product_category || "Technical System"}
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {reqList.length} live requirements currently indexed for{" "}
          {activeProject?.company || "the manufacturer"}.
        </p>
        <p className="mt-4 rounded-lg border border-border bg-surface/70 p-4 text-xs leading-relaxed text-muted-foreground">
          Frameworks structure and categorize the analysis. Final compliance determination remains
          with the manufacturer, system safety engineers, and certifying authorities.
        </p>
      </Panel>
    </div>
  );
}
