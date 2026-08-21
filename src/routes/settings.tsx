import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Brain, CheckCircle2, ExternalLink, KeyRound, Loader2, Sparkles, Zap } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { PageHeader, Panel } from "@/components/primitives";
import { Tag } from "@/components/status";
import { useAiSettings, useUpdateAiSettings } from "@/hooks/use-ai-settings";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/settings")({
  head: () => ({
    meta: [
      { title: "Settings — TraceAudit" },
      { name: "description", content: "Organization, roles, retention and AI model settings." },
      { property: "og:title", content: "Settings — TraceAudit" },
      { property: "og:description", content: "Enterprise configuration for your audit workspace." },
    ],
  }),
  component: SettingsPage,
});

const sections = [
  "Organization",
  "AI configuration",
  "Users & roles",
  "Projects",
  "Document retention",
  "Audit logs",
  "Security",
] as const;

const users = [
  { name: "Hamza Meskini", role: "Admin", email: "h.meskini@atlasmotion.com" },
  { name: "A. Benali", role: "Reviewer", email: "a.benali@atlasmotion.com" },
  { name: "L. Fischer", role: "Engineer", email: "l.fischer@atlasmotion.com" },
  { name: "S. Novak", role: "Viewer", email: "s.novak@atlasmotion.com" },
];

const thinkingLevels = [
  {
    level: "HIGH",
    title: "High Thinking (Recommended for Audits)",
    desc: "Maximum reasoning depth. Performs multi-step cross-referencing and exhaustive contradiction detection across technical files.",
    badge: "Maximum Depth",
  },
  {
    level: "MEDIUM",
    title: "Medium Thinking",
    desc: "Standard balance of fast response latency and detailed parameter verification.",
    badge: "Balanced",
  },
  {
    level: "LOW",
    title: "Low Thinking",
    desc: "Optimized for high-speed extraction on simple specification documents.",
    badge: "Fastest",
  },
] as const;

function SettingsPage() {
  const [active, setActive] = useState<(typeof sections)[number]>("Organization");
  const { data: aiSettings, isLoading: isAiLoading } = useAiSettings();
  const updateAiMutation = useUpdateAiSettings();

  const currentModel = aiSettings?.current_model || "gemini-3.7-flash";
  const currentThinking = aiSettings?.thinking_level || "HIGH";

  const handleModelChange = async (modelId: string) => {
    try {
      await updateAiMutation.mutateAsync({ model: modelId });
      toast.success(`Active AI Model switched to ${modelId}`);
    } catch (err: any) {
      toast.error(`Failed updating model: ${err.message || err}`);
    }
  };

  const handleThinkingChange = async (level: string) => {
    try {
      await updateAiMutation.mutateAsync({ thinking_level: level });
      toast.success(`Gemini Thinking Level set to ${level}`);
    } catch (err: any) {
      toast.error(`Failed updating thinking level: ${err.message || err}`);
    }
  };

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader title="Settings" subtitle="Workspace configuration for Atlas Motion Systems." />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <nav className="space-y-1 lg:col-span-1">
          {sections.map((s) => (
            <button
              key={s}
              onClick={() => setActive(s)}
              className={cn(
                "w-full rounded-md px-3 py-2 text-left text-sm transition-colors",
                active === s
                  ? "bg-card font-medium shadow-subtle"
                  : "text-muted-foreground hover:bg-accent",
              )}
            >
              {s}
            </button>
          ))}
        </nav>

        <div className="space-y-4 lg:col-span-3">
          {active === "AI configuration" ? (
            <Panel
              title="Google Gemini AI & Thinking Configuration"
              description="Configure Google Gemini models and internal reasoning depth for autonomous requirement auditing."
            >
              {isAiLoading ? (
                <div className="flex h-32 items-center justify-center">
                  <Loader2 className="size-6 animate-spin text-primary" />
                </div>
              ) : (
                <div className="space-y-6">
                  {/* API Key Status */}
                  <div className="flex items-center justify-between rounded-lg border border-border bg-surface/60 p-4">
                    <div className="flex items-center gap-3">
                      <KeyRound className="size-5 text-primary" />
                      <div>
                        <div className="text-sm font-medium">Google Gemini API Key</div>
                        <p className="text-xs text-muted-foreground">
                          Configured in <code className="font-mono text-xs">backend/.env</code>
                        </p>
                      </div>
                    </div>
                    {aiSettings?.has_gemini_key ? (
                      <span className="inline-flex items-center gap-1.5 rounded-md border border-success/30 bg-success-soft px-2.5 py-1 text-xs font-medium text-success">
                        <CheckCircle2 className="size-3.5" /> Key Active
                      </span>
                    ) : (
                      <span className="rounded-md border border-warning/30 bg-warning-soft px-2.5 py-1 text-xs font-medium text-warning">
                        Key not detected in .env
                      </span>
                    )}
                  </div>

                  {/* Thinking Configuration Section */}
                  <div>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Brain className="size-4 text-primary" />
                        <label className="text-xs font-semibold uppercase tracking-wide text-foreground">
                          Gemini Thinking Level (Reasoning Effort)
                        </label>
                      </div>
                      <a
                        href="https://ai.google.dev/gemini-api/docs/thinking"
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                      >
                        Docs
                        <ExternalLink className="size-3" />
                      </a>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Controls the intensity of Gemini's internal reasoning tokens prior to output generation.
                    </p>

                    <div className="mt-3 grid gap-3 sm:grid-cols-3">
                      {thinkingLevels.map((t) => (
                        <div
                          key={t.level}
                          onClick={() => handleThinkingChange(t.level)}
                          className={cn(
                            "cursor-pointer rounded-lg border p-3.5 transition-all",
                            currentThinking === t.level
                              ? "border-primary bg-info-soft/40 shadow-subtle ring-1 ring-primary"
                              : "border-border bg-card hover:border-primary/50"
                          )}
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-bold font-mono uppercase">{t.level}</span>
                            <span
                              className={cn(
                                "rounded px-1.5 py-0.5 text-[10px] font-medium",
                                currentThinking === t.level
                                  ? "bg-primary text-primary-foreground"
                                  : "bg-muted text-muted-foreground"
                              )}
                            >
                              {t.badge}
                            </span>
                          </div>
                          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{t.desc}</p>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Model Selection */}
                  <div>
                    <label className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Active Model
                    </label>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      {(aiSettings?.available_models || []).map((m) => (
                        <div
                          key={m.id}
                          onClick={() => handleModelChange(m.id)}
                          className={cn(
                            "cursor-pointer rounded-lg border p-4 transition-all",
                            currentModel === m.id
                              ? "border-primary bg-info-soft/40 shadow-subtle ring-1 ring-primary"
                              : "border-border bg-card hover:border-primary/50"
                          )}
                        >
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2">
                              <Sparkles className="size-4 text-primary" />
                              <span className="text-sm font-semibold">{m.name}</span>
                            </div>
                            {currentModel === m.id && <Tag>Active</Tag>}
                          </div>
                          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                            {m.description}
                          </p>
                          {m.thinking_supported && (
                            <div className="mt-3 flex items-center gap-1.5 text-[11px] text-primary">
                              <Zap className="size-3" />
                              <span>Thinking Supported ({m.default_thinking})</span>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-lg border border-border bg-surface/40 p-4 text-xs text-muted-foreground">
                    💡 <strong>Thinking Recommendation:</strong> <strong>Gemini 3.7 Flash</strong> with <strong>HIGH Thinking</strong> delivers maximum precision on complex numerical parameter checks, voltage tolerances, and missing certification records. <strong>Gemini 3.1 Pro Preview</strong> provides extended reasoning on subtle multi-document semantic contradictions.
                  </div>
                </div>
              )}
            </Panel>
          ) : active === "Users & roles" ? (
            <Panel title="Users & roles" bodyClassName="p-0">
              <table className="w-full text-sm">
                <tbody>
                  {users.map((u) => (
                    <tr key={u.email} className="border-b border-border/70 last:border-0">
                      <td className="px-5 py-3 font-medium">{u.name}</td>
                      <td className="px-5 py-3 text-muted-foreground">{u.email}</td>
                      <td className="px-5 py-3">
                        <Tag>{u.role}</Tag>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          ) : (
            <Panel title={active}>
              <div className="space-y-4">
                <div>
                  <label className="text-xs uppercase tracking-wide text-muted-foreground">
                    Organization name
                  </label>
                  <Input className="mt-1.5" defaultValue="Atlas Motion Systems" />
                </div>
                <div className="flex items-center justify-between rounded-lg border border-border p-4">
                  <div>
                    <div className="text-sm font-medium">Require human review on critical findings</div>
                    <p className="text-xs text-muted-foreground">
                      Critical findings cannot be closed without a reviewer decision.
                    </p>
                  </div>
                  <Switch defaultChecked />
                </div>
                <div className="flex items-center justify-between rounded-lg border border-border p-4">
                  <div>
                    <div className="text-sm font-medium">Retain source documents</div>
                    <p className="text-xs text-muted-foreground">Retention period: 24 months.</p>
                  </div>
                  <Switch defaultChecked />
                </div>
                <Button size="sm" onClick={() => toast.success("Settings saved")}>
                  Save changes
                </Button>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
