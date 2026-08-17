import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Check, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader, Panel } from "@/components/primitives";
import { categories, frameworks, projectStats } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/new-audit")({
  head: () => ({
    meta: [
      { title: "New Audit — TraceAudit" },
      { name: "description", content: "Create a technical documentation audit in four steps." },
      { property: "og:title", content: "New Audit — TraceAudit" },
      { property: "og:description", content: "Project, requirements, evidence and review setup." },
    ],
  }),
  component: WizardPage,
});

const steps = ["Project", "Requirements", "Evidence", "Review"];

function WizardPage() {
  const [step, setStep] = useState(0);
  const [running, setRunning] = useState(false);
  const navigate = useNavigate();

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title="New Audit" subtitle="Set up a technical documentation audit." />

      <ol className="mb-4 flex gap-2">
        {steps.map((s, i) => (
          <li
            key={s}
            className={cn(
              "flex flex-1 items-center gap-2 rounded-lg border px-3 py-2 text-xs",
              i === step
                ? "border-primary bg-info-soft text-foreground"
                : i < step
                  ? "border-success/25 bg-success-soft text-success"
                  : "border-border bg-card text-muted-foreground",
            )}
          >
            {i < step ? <Check className="size-3.5" /> : <span className="font-mono">{i + 1}</span>}
            {s}
          </li>
        ))}
      </ol>

      <Panel title={`Step ${step + 1} — ${steps[step]}`}>
        {step === 0 && (
          <div className="space-y-4">
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">Project name</label>
              <Input className="mt-1.5" defaultValue="X200 EU Technical Documentation Audit" />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">Product name</label>
              <Input className="mt-1.5" defaultValue="Industrial Controller X200" />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">Product category</label>
              <Select defaultValue="Electrical">
                <SelectTrigger className="mt-1.5">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {categories.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        )}

        {step === 1 && (
          <div className="space-y-3">
            <div className="rounded-lg border border-dashed border-border bg-surface/60 p-6 text-center text-sm">
              <UploadCloud className="mx-auto size-5 text-muted-foreground" />
              <p className="mt-2 font-medium">Upload requirements</p>
              <p className="text-xs text-muted-foreground">CSV, XLSX or DOCX requirement lists</p>
            </div>
            <div className="text-center text-xs text-muted-foreground">or select a framework</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {frameworks.map((f) => (
                <button
                  key={f.id}
                  className="rounded-lg border border-border bg-card p-3 text-left text-sm hover:border-primary"
                >
                  <div className="font-medium">{f.name}</div>
                  <div className="text-xs text-muted-foreground">{f.status}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        {step === 2 && (
          <ul className="space-y-2 text-sm">
            {[
              "Technical specifications",
              "Test reports",
              "Risk assessments",
              "Supplier documents",
              "Other evidence",
            ].map((t) => (
              <li
                key={t}
                className="flex items-center justify-between rounded-lg border border-border px-4 py-3"
              >
                {t}
                <Button variant="outline" size="sm">
                  <UploadCloud className="size-4" />
                  Upload
                </Button>
              </li>
            ))}
          </ul>
        )}

        {step === 3 && (
          <div className="space-y-4">
            <dl className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-xs text-muted-foreground">Requirements</dt>
                <dd className="text-2xl font-semibold tabular">{projectStats.requirements}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Documents</dt>
                <dd className="text-2xl font-semibold tabular">{projectStats.documents}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-xs text-muted-foreground">Framework</dt>
                <dd className="font-medium">EU Product Requirements — Example Dataset</dd>
              </div>
            </dl>
            {running && (
              <div>
                <div className="flex justify-between text-xs text-muted-foreground">
                  <span>Analyzing evidence...</span>
                  <span>64%</span>
                </div>
                <Progress value={64} className="mt-1.5 h-1.5" />
              </div>
            )}
          </div>
        )}

        <div className="mt-6 flex justify-between border-t border-border pt-4">
          <Button variant="ghost" size="sm" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
            Back
          </Button>
          {step < 3 ? (
            <Button size="sm" onClick={() => setStep((s) => s + 1)}>
              Continue
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => {
                setRunning(true);
                toast.success("Audit started", { description: "Analyzing evidence..." });
                setTimeout(() => navigate({ to: "/" }), 1600);
              }}
            >
              Start AI Audit
            </Button>
          )}
        </div>
      </Panel>
    </div>
  );
}
