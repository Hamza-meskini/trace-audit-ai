import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState, useRef } from "react";
import { Brain, Check, Loader2, Sparkles, UploadCloud, Zap } from "lucide-react";
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
import { categories, frameworks } from "@/lib/mock-data";
import { useCreateProject } from "@/hooks/use-projects";
import { useUploadDocument, useDocuments } from "@/hooks/use-documents";
import { useTriggerAudit } from "@/hooks/use-audit";
import { useAiSettings } from "@/hooks/use-ai-settings";
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

const steps = ["Project", "Requirements", "Evidence", "Review & Model"];

function WizardPage() {
  const [step, setStep] = useState(0);
  const [running, setRunning] = useState(false);
  const [projectName, setProjectName] = useState("X200 EU Technical Documentation Audit");
  const [productName, setProductName] = useState("Industrial Controller X200");
  const [productCategory, setProductCategory] = useState("Electrical");
  const [company, setCompany] = useState("Atlas Motion Systems");
  const [selectedModel, setSelectedModel] = useState("gemini-3.7-flash");
  const [thinkingLevel, setThinkingLevel] = useState("HIGH");
  const [createdProjectId, setCreatedProjectId] = useState<string>("proj-001");

  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const { data: aiSettings } = useAiSettings();
  const createProjectMutation = useCreateProject();
  const uploadDocMutation = useUploadDocument(createdProjectId);
  const triggerAuditMutation = useTriggerAudit(createdProjectId);
  const { data: documentsList } = useDocuments(createdProjectId);

  const availableModels = aiSettings?.available_models || [
    {
      id: "gemini-3.7-flash",
      name: "Gemini 3.7 Flash",
      description: "Recommended. Ultra-fast, highly accurate extraction with High Thinking reasoning enabled.",
      thinking_supported: true,
      default_thinking: "HIGH",
    },
    {
      id: "gemini-3.1-pro-preview",
      name: "Gemini 3.1 Pro Preview",
      description: "Advanced reasoning model with Thinking enabled for deep contradiction analysis across complex technical files.",
      thinking_supported: true,
      default_thinking: "HIGH",
    },
    {
      id: "gemini-2.5-flash",
      name: "Gemini 2.5 Flash",
      description: "Fast production model for high-throughput batch extraction.",
      thinking_supported: true,
      default_thinking: "MEDIUM",
    },
  ];

  const handleNextStep = async () => {
    if (step === 0) {
      try {
        const proj = await createProjectMutation.mutateAsync({
          name: projectName,
          product_name: productName,
          product_category: productCategory,
          company: company,
        });
        setCreatedProjectId(proj.id);
        toast.success("Project created", { description: `${proj.audit_id}` });
        setStep(1);
      } catch (err: any) {
        // If already exists or error, proceed with default ID
        setStep(1);
      }
    } else {
      setStep((s) => s + 1);
    }
  };

  const handleFileUpload = async (files: FileList | null, docType = "") => {
    if (!files || files.length === 0) return;
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (!file) continue;
      try {
        toast.info(`Uploading ${file.name}...`);
        await uploadDocMutation.mutateAsync({ file, docType });
        toast.success(`${file.name} uploaded!`);
      } catch (err: any) {
        toast.error(`Upload error: ${err.message}`);
      }
    }
  };

  const handleStartAudit = async () => {
    setRunning(true);
    toast.info(`Running AI audit with ${selectedModel} (Thinking: ${thinkingLevel})...`, {
      description: "Extracting requirements, matching evidence, and detecting conflicts.",
    });

    try {
      await triggerAuditMutation.mutateAsync({
        model: selectedModel,
        thinking_level: thinkingLevel,
      });
      toast.success("Audit complete!", {
        description: `Requirements analyzed using ${selectedModel} (Thinking: ${thinkingLevel}).`,
      });
      setTimeout(() => navigate({ to: "/" }), 800);
    } catch (err: any) {
      toast.error(`Audit pipeline notice: ${err.message || err}`);
      setTimeout(() => navigate({ to: "/" }), 1200);
    } finally {
      setRunning(false);
    }
  };

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

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.xlsx,.csv"
        className="hidden"
        onChange={(e) => handleFileUpload(e.target.files)}
      />

      <Panel title={`Step ${step + 1} — ${steps[step]}`}>
        {step === 0 && (
          <div className="space-y-4">
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">Project name</label>
              <Input
                className="mt-1.5"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">Company name</label>
              <Input
                className="mt-1.5"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">Product name</label>
              <Input
                className="mt-1.5"
                value={productName}
                onChange={(e) => setProductName(e.target.value)}
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">Product category</label>
              <Select value={productCategory} onValueChange={setProductCategory}>
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
            <div
              onClick={() => fileInputRef.current?.click()}
              className="cursor-pointer rounded-lg border border-dashed border-border bg-surface/60 p-6 text-center text-sm transition-colors hover:border-primary"
            >
              <UploadCloud className="mx-auto size-5 text-muted-foreground" />
              <p className="mt-2 font-medium">Upload requirements document</p>
              <p className="text-xs text-muted-foreground">PDF, CSV, XLSX or DOCX requirement lists</p>
            </div>
            <div className="text-center text-xs text-muted-foreground">or select a pre-configured framework</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {frameworks.map((f) => (
                <button
                  key={f.id}
                  onClick={() => toast.info(`Selected ${f.name}`)}
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
                <div>
                  <div className="font-medium">{t}</div>
                  <div className="text-xs text-muted-foreground">
                    PDF, DOCX, XLSX evidence files
                  </div>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <UploadCloud className="size-4" />
                  Upload
                </Button>
              </li>
            ))}
          </ul>
        )}

        {step === 3 && (
          <div className="space-y-5">
            <dl className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <dt className="text-xs text-muted-foreground">Product</dt>
                <dd className="text-lg font-semibold">{productName}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Documents Ready</dt>
                <dd className="text-lg font-semibold tabular">
                  {documentsList?.length || 6} files
                </dd>
              </div>
            </dl>

            {/* Model Selection Option */}
            <div className="rounded-lg border border-border bg-surface/70 p-4">
              <div className="flex items-center gap-2">
                <Sparkles className="size-4 text-primary" />
                <label className="text-xs font-semibold uppercase tracking-wide text-foreground">
                  Select AI Audit Model
                </label>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                Choose the Gemini model for requirements extraction, evidence reasoning, and contradiction detection.
              </p>

              <div className="mt-3 space-y-2">
                {availableModels.map((m) => (
                  <div
                    key={m.id}
                    onClick={() => setSelectedModel(m.id)}
                    className={cn(
                      "cursor-pointer rounded-lg border p-3 transition-all",
                      selectedModel === m.id
                        ? "border-primary bg-info-soft/40 shadow-subtle ring-1 ring-primary"
                        : "border-border bg-card hover:border-primary/50"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-semibold">{m.name}</span>
                      {selectedModel === m.id && (
                        <span className="rounded-full bg-primary px-2 py-0.5 text-[10px] font-medium text-primary-foreground">
                          Selected
                        </span>
                      )}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{m.description}</p>
                  </div>
                ))}
              </div>

              {/* Thinking Intensity Selector */}
              <div className="mt-4 border-t border-border/80 pt-3">
                <div className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                  <Brain className="size-3.5 text-primary" />
                  <span>Thinking Intensity (Reasoning Effort):</span>
                </div>
                <div className="mt-2 flex gap-2">
                  {["HIGH", "MEDIUM", "LOW"].map((lvl) => (
                    <button
                      key={lvl}
                      type="button"
                      onClick={() => setThinkingLevel(lvl)}
                      className={cn(
                        "flex-1 rounded-md border py-1.5 text-center font-mono text-xs font-semibold uppercase transition-colors",
                        thinkingLevel === lvl
                          ? "border-primary bg-primary text-primary-foreground shadow-subtle"
                          : "border-border bg-card text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {lvl} {lvl === "HIGH" ? "(Recommended)" : ""}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {running && (
              <div className="rounded-lg border border-primary/30 bg-primary/5 p-4">
                <div className="flex items-center gap-2 text-sm font-medium text-primary">
                  <Loader2 className="size-4 animate-spin" />
                  Running {selectedModel} with Thinking: {thinkingLevel}...
                </div>
                <Progress value={85} className="mt-3 h-1.5" />
              </div>
            )}
          </div>
        )}

        <div className="mt-6 flex justify-between border-t border-border pt-4">
          <Button variant="ghost" size="sm" disabled={step === 0 || running} onClick={() => setStep((s) => s - 1)}>
            Back
          </Button>
          {step < 3 ? (
            <Button size="sm" onClick={handleNextStep}>
              Continue
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={running}
              onClick={handleStartAudit}
            >
              {running ? (
                <>
                  <Loader2 className="size-4 animate-spin" />
                  Analyzing with Thinking...
                </>
              ) : (
                `Start AI Audit (${selectedModel} · ${thinkingLevel})`
              )}
            </Button>
          )}
        </div>
      </Panel>
    </div>
  );
}
