import { createFileRoute } from "@tanstack/react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { PageHeader, Panel } from "@/components/primitives";
import { Tag } from "@/components/status";
import { frameworks } from "@/lib/mock-data";

export const Route = createFileRoute("/frameworks")({
  head: () => ({
    meta: [
      { title: "Requirement Frameworks — TraceAudit" },
      {
        name: "description",
        content: "Select the requirement sets used for technical documentation analysis.",
      },
      { property: "og:title", content: "Requirement Frameworks — TraceAudit" },
      {
        property: "og:description",
        content: "Company, safety, cybersecurity and example requirement datasets.",
      },
    ],
  }),
  component: FrameworksPage,
});

function FrameworksPage() {
  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Requirement Frameworks"
        subtitle="Choose the requirement sets your documentation is analyzed against."
      />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        {frameworks.map((f) => (
          <Panel key={f.id} title={f.name}>
            <Tag
              className={
                f.status === "Active"
                  ? "border-success/25 bg-success-soft text-success"
                  : f.status === "Configuration required"
                    ? "border-warning/30 bg-warning-soft text-warning"
                    : undefined
              }
            >
              {f.status}
            </Tag>
            <p className="mt-3 text-sm text-muted-foreground">{f.description}</p>
            <div className="mt-4 text-2xl font-semibold tabular">
              {f.requirements ? f.requirements : "—"}
              <span className="ml-1.5 text-xs font-normal text-muted-foreground">requirements</span>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="mt-4 w-full"
              onClick={() => toast.info(`${f.name} configuration opened`)}
            >
              Configure framework
            </Button>
          </Panel>
        ))}
      </div>

      <Panel className="mt-4" title="Selected framework">
        <div className="text-sm font-medium">EU Product Requirements — Example Dataset</div>
        <p className="mt-1 text-sm text-muted-foreground">
          Example requirement set used for technical documentation analysis.
        </p>
        <p className="mt-4 rounded-lg border border-border bg-surface/70 p-4 text-xs leading-relaxed text-muted-foreground">
          Frameworks structure the analysis only. Framework selection does not determine regulatory
          conformity; final assessment remains with the manufacturer and qualified professionals.
        </p>
      </Panel>
    </div>
  );
}
