import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, useRef } from "react";
import { Brain, Check, FileText, Loader2, Sparkles, UploadCloud, Zap } from "lucide-react";
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
import { useCreateProject } from "@/hooks/use-projects";
import { useUploadDocument, useDocuments } from "@/hooks/use-documents";
import { useTriggerAudit } from "@/hooks/use-audit";
import { useAiSettings } from "@/hooks/use-ai-settings";
import { useActiveProject } from "@/hooks/use-active-project";
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
const categories = [
  "Electrical",
  "Safety",
  "Environmental",
  "Mechanical",
  "Cybersecurity",
  "Documentation",
] as const;

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function WizardPage() {
  const [step, setStep] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [running, setRunning] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [productName, setProductName] = useState("");
  const [productCategory, setProductCategory] = useState("Electrical");
  const [company, setCompany] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const [thinkingLevel, setThinkingLevel] = useState("HIGH");
  const [createdProjectId, setCreatedProjectId] = useState<string>("");
  const { selectProject } = useActiveProject();

  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const { data: aiSettings } = useAiSettings();
  const createProjectMutation = useCreateProject();
  const uploadDocMutation = useUploadDocument(createdProjectId);
  const triggerAuditMutation = useTriggerAudit(createdProjectId);
  const { data: documentsList } = useDocuments(createdProjectId);

  const availableModels = aiSettings?.available_models || [];
  useEffect(() => {
    if (!selectedModel && aiSettings?.current_model) {
      setSelectedModel(aiSettings.current_model);
      setThinkingLevel(aiSettings.thinking_level || "HIGH");
    }
  }, [aiSettings, selectedModel]);

  const handleNextStep = async () => {
    if (step === 0) {
      if (createdProjectId) {
        setStep(1);
        return;
      }
      if (!projectName.trim() || !productName.trim()) {
        toast.error("Enter a project and product name");
        return;
      }
      try {
        const proj = await createProjectMutation.mutateAsync({
          name: projectName,
          product_name: productName,
          product_category: productCategory,
          company: company,
        });
        setCreatedProjectId(proj.id);
        selectProject(proj.id);
        toast.success("Project created", { description: `${proj.audit_id}` });
        setStep(1);
      } catch (err: unknown) {
        toast.error(`Project was not created: ${errorMessage(err)}`);
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
      } catch (err: unknown) {
        toast.error(`Upload error: ${errorMessage(err)}`);
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
      toast.success("Audit started", {
        description: "Follow live progress from any page in this project.",
      });
      setTimeout(() => navigate({ to: "/" }), 800);
    } catch (err: unknown) {
      toast.error(`Audit pipeline notice: ${errorMessage(err)}`);
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
              <label className="text-xs uppercase tracking-wide text-muted-foreground">
                Project name
              </label>
              <Input
                className="mt-1.5"
                value={projectName}
                onChange={(e) => setProjectName(e.target.value)}
                placeholder="e.g. Battery validation evidence review"
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">
                Company name
              </label>
              <Input
                className="mt-1.5"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="Company or team (optional)"
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">
                Product name
              </label>
              <Input
                className="mt-1.5"
                value={productName}
                onChange={(e) => setProductName(e.target.value)}
                placeholder="Product or system under review"
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wide text-muted-foreground">
                Product category
              </label>
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
              <p className="text-xs text-muted-foreground">
                PDF, CSV, XLSX or DOCX requirement lists
              </p>
            </div>
            <p className="text-center text-xs text-muted-foreground">
              Upload the authorized requirement source. Framework templates are not enabled in this
              workspace.
            </p>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-4">
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                handleFileUpload(e.dataTransfer.files);
              }}
              onClick={() => fileInputRef.current?.click()}
              className={cn(
                "cursor-pointer rounded-xl border-2 border-dashed p-8 text-center transition-all",
                dragging
                  ? "border-primary bg-primary/10 scale-[1.01]"
                  : "border-border/80 bg-surface/50 hover:border-primary/70 hover:bg-surface",
              )}
            >
              <div className="mx-auto flex size-12 items-center justify-center rounded-full bg-primary/10 text-primary">
                <UploadCloud className="size-6" />
              </div>
              <p className="mt-3 text-sm font-semibold text-foreground">
                Drop all evidence documents here, or click to browse
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Select and upload all files at once (PDF, DOCX, XLSX, CSV)
              </p>
              <p className="mt-0.5 text-[11px] text-muted-foreground/80">
                Supports Test Reports, Supplier Datasheets, Compliance Matrices, Architecture Specs
                & Safety Logs
              </p>
              <Button variant="outline" size="sm" className="mt-4 pointer-events-none">
                <UploadCloud className="mr-1.5 size-4" />
                Select Multiple Files
              </Button>
            </div>

            {/* List of uploaded documents */}
            {documentsList && documentsList.length > 0 && (
              <div className="rounded-lg border border-border bg-card p-4">
                <div className="flex items-center justify-between border-b border-border/80 pb-2.5">
                  <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Uploaded Technical Files ({documentsList.length})
                  </span>
                  <span className="text-xs text-success flex items-center gap-1 font-medium">
                    <Check className="size-3.5" /> Uploaded
                  </span>
                </div>
                <ul className="mt-2 divide-y divide-border/60">
                  {documentsList.map((doc) => (
                    <li key={doc.id} className="flex items-center justify-between py-2 text-xs">
                      <div className="flex items-center gap-2 truncate pr-4">
                        <FileText className="size-4 shrink-0 text-muted-foreground" />
                        <span className="font-medium text-foreground truncate">
                          {doc.original_filename}
                        </span>
                      </div>
                      <span className="shrink-0 rounded-full border border-border bg-surface px-2.5 py-0.5 text-[10px] font-mono text-muted-foreground">
                        {doc.doc_type || "Technical documentation"}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
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
                  {documentsList?.length || 0} files
                </dd>
              </div>
            </dl>

            <p className="rounded-lg border bg-surface/70 p-4 text-sm">
              TraceAudit will use the workspace model{" "}
              <strong>
                {availableModels.find((item) => item.id === selectedModel)?.name ||
                  selectedModel ||
                  "not configured"}
              </strong>
              . Document content may be processed by configured remote AI services.
            </p>
            <details className="rounded-lg border border-border bg-surface/70 p-4">
              <summary className="cursor-pointer text-sm font-medium">
                Advanced model settings
              </summary>
              <div className="mt-4">
                <div className="flex items-center gap-2">
                  <Sparkles className="size-4 text-primary" />
                  <label className="text-xs font-semibold uppercase tracking-wide text-foreground">
                    Select AI Audit Model
                  </label>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Choose the primary reasoning model. Figure processing and focused secondary review
                  may use other configured providers.
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
                          : "border-border bg-card hover:border-primary/50",
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
                {availableModels.find((m) => m.id === selectedModel)?.thinking_supported && (
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
                              : "border-border bg-card text-muted-foreground hover:text-foreground",
                          )}
                        >
                          {lvl} {lvl === "HIGH" ? "(Recommended)" : ""}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </details>

            {running && (
              <div className="rounded-lg border border-primary/30 bg-primary/5 p-4">
                <div className="flex items-center gap-2 text-sm font-medium text-primary">
                  <Loader2 className="size-4 animate-spin" />
                  Running {selectedModel} with Thinking: {thinkingLevel}...
                </div>
                <p className="mt-2 text-xs">
                  Submitting audit job… Live stage updates will appear after the server accepts it.
                </p>
              </div>
            )}
          </div>
        )}

        <div className="mt-6 flex justify-between border-t border-border pt-4">
          <Button
            variant="ghost"
            size="sm"
            disabled={step === 0 || running}
            onClick={() => setStep((s) => s - 1)}
          >
            Back
          </Button>
          {step < 3 ? (
            <Button
              size="sm"
              disabled={createProjectMutation.isPending || uploadDocMutation.isPending}
              onClick={handleNextStep}
            >
              Continue
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={
                running ||
                !createdProjectId ||
                !documentsList?.length ||
                !selectedModel ||
                uploadDocMutation.isPending
              }
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
