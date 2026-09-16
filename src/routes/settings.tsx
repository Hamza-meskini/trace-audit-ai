import { createFileRoute } from "@tanstack/react-router";
import { CheckCircle2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { PageHeader, Panel } from "@/components/primitives";
import { useAiSettings, useUpdateAiSettings } from "@/hooks/use-ai-settings";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/settings")({
  head: () => ({ meta: [{ title: "AI Settings — TraceAudit" }] }),
  component: SettingsPage,
});

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function SettingsPage() {
  const settings = useAiSettings();
  const update = useUpdateAiSettings();
  const change = async (payload: { model?: string; thinking_level?: string }) => {
    try {
      await update.mutateAsync(payload);
      toast.success("AI configuration updated");
    } catch (error: unknown) {
      toast.error(`Configuration was not updated: ${message(error)}`);
    }
  };

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title="AI configuration" subtitle="Live model settings used by new audit runs." />
      <Panel>
        {settings.isLoading ? (
          <div role="status" className="flex justify-center gap-2 p-12">
            <Loader2 className="size-5 animate-spin" />
            Loading configuration…
          </div>
        ) : settings.isError || !settings.data ? (
          <div role="alert" className="rounded-lg border p-5 text-sm">
            AI settings could not be loaded. No example configuration is being shown.
          </div>
        ) : (
          <div className="space-y-7">
            <section>
              <h2 className="text-sm font-semibold">Primary audit model</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Changing this affects future audit runs; saved assessments keep their recorded
                model.
              </p>
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {settings.data.available_models.map((model) => (
                  <button
                    key={model.id}
                    onClick={() => change({ model: model.id })}
                    disabled={update.isPending}
                    className={cn(
                      "rounded-lg border p-4 text-left",
                      settings.data.current_model === model.id
                        ? "border-primary bg-primary/5 ring-1 ring-primary"
                        : "hover:border-primary/50",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{model.name}</span>
                      {settings.data.current_model === model.id && (
                        <CheckCircle2 className="size-4 text-primary" />
                      )}
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">{model.description}</p>
                    <p className="mt-2 font-mono text-[11px] text-muted-foreground">
                      {model.provider} · {model.id}
                    </p>
                  </button>
                ))}
              </div>
            </section>
            <section className="border-t pt-6">
              <h2 className="text-sm font-semibold">Reasoning effort</h2>
              <div className="mt-3 flex flex-wrap gap-2">
                {settings.data.supported_thinking_levels.map((level) => (
                  <button
                    key={level}
                    onClick={() => change({ thinking_level: level })}
                    disabled={update.isPending}
                    className={cn(
                      "rounded-md border px-4 py-2 text-xs font-medium",
                      settings.data.thinking_level === level
                        ? "border-primary bg-primary text-primary-foreground"
                        : "hover:border-primary/50",
                    )}
                  >
                    {level}
                  </button>
                ))}
              </div>
            </section>
            <section className="rounded-lg border bg-muted/30 p-4 text-xs text-muted-foreground">
              <p>
                Provider: <strong className="text-foreground">{settings.data.provider}</strong>
              </p>
              <p className="mt-1">
                Credentials are configured server-side and are never displayed here. Organization,
                users, retention, and security controls are not implemented in this local workspace.
              </p>
            </section>
          </div>
        )}
      </Panel>
    </div>
  );
}
