import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { CheckCircle2, Download, Loader2, Mail, Search, Trash2, Users } from "lucide-react";
import { toast } from "sonner";
import { PageHeader, Panel } from "@/components/primitives";
import { useAiSettings, useUpdateAiSettings } from "@/hooks/use-ai-settings";
import { useVisitors, useDeleteVisitor } from "@/hooks/use-visitors";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/settings")({
  head: () => ({ meta: [{ title: "Settings — TraceAudit" }] }),
  component: SettingsPage,
});

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

function VisitorLeadsSection() {
  const [search, setSearch] = useState("");
  const visitorsQuery = useVisitors(search);
  const deleteMutation = useDeleteVisitor();

  const handleDelete = async (id: string, email: string) => {
    if (!window.confirm(`Delete visitor lead for ${email}?`)) return;
    try {
      await deleteMutation.mutateAsync(id);
      toast.success(`Removed ${email}`);
    } catch (err: unknown) {
      toast.error(`Could not delete visitor: ${message(err)}`);
    }
  };

  const handleExport = () => {
    const url = api.getVisitorsExportUrl();
    window.open(url, "_blank");
  };

  const total = visitorsQuery.data?.total ?? 0;
  const items = visitorsQuery.data?.items ?? [];

  return (
    <div className="mt-8 space-y-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold tracking-tight text-foreground flex items-center gap-2">
            <Users className="size-4 text-primary" />
            <span>Captured Visitor Leads & Emails</span>
            <Badge variant="secondary" className="text-[11px] font-mono">
              {total}
            </Badge>
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            People who submitted their email for early access, demo requests, or product updates.
          </p>
        </div>
        <div className="flex items-center gap-2 pt-2 sm:pt-0">
          <Button
            variant="outline"
            size="sm"
            onClick={handleExport}
            disabled={total === 0}
            className="gap-1.5 text-xs h-8"
          >
            <Download className="size-3.5" />
            <span>Export CSV</span>
          </Button>
        </div>
      </div>

      <Panel className="p-0 overflow-hidden">
        {/* Search Bar */}
        <div className="flex items-center gap-2 border-b border-border bg-muted/20 px-4 py-2.5">
          <Search className="size-3.5 text-muted-foreground shrink-0" />
          <Input
            placeholder="Filter leads by email, name, or company…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-7 text-xs border-0 bg-transparent shadow-none focus-visible:ring-0 px-1 placeholder:text-muted-foreground"
          />
          {search && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSearch("")}
              className="h-6 px-2 text-[11px] text-muted-foreground"
            >
              Clear
            </Button>
          )}
        </div>

        {/* Table Content */}
        {visitorsQuery.isLoading ? (
          <div
            role="status"
            className="flex items-center justify-center gap-2 p-10 text-xs text-muted-foreground"
          >
            <Loader2 className="size-4 animate-spin" />
            Loading visitor leads…
          </div>
        ) : visitorsQuery.isError ? (
          <div role="alert" className="p-6 text-center text-xs text-destructive">
            Could not load visitor leads.
          </div>
        ) : items.length === 0 ? (
          <div className="p-10 text-center space-y-2">
            <div className="mx-auto grid size-10 place-items-center rounded-full bg-muted text-muted-foreground">
              <Mail className="size-5" />
            </div>
            <p className="text-xs font-medium text-foreground">
              {search ? "No leads matching search" : "No visitor emails captured yet"}
            </p>
            <p className="text-[11px] text-muted-foreground max-w-sm mx-auto">
              Visitors who submit the &ldquo;Request Access&rdquo; modal on the website will be
              automatically stored here.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-border bg-muted/40 font-medium text-muted-foreground">
                <tr>
                  <th className="px-4 py-2.5">Email</th>
                  <th className="px-4 py-2.5">Name & Company</th>
                  <th className="px-4 py-2.5">Role / Focus</th>
                  <th className="px-4 py-2.5">Source</th>
                  <th className="px-4 py-2.5">Registered</th>
                  <th className="px-4 py-2.5 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {items.map((visitor) => (
                  <tr key={visitor.id} className="hover:bg-muted/30 transition-colors">
                    <td className="px-4 py-2.5 font-medium text-foreground">
                      <div className="flex items-center gap-1.5">
                        <Mail className="size-3 text-muted-foreground shrink-0" />
                        <span className="font-mono text-[11px]">{visitor.email}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      {visitor.full_name || visitor.company ? (
                        <div>
                          {visitor.full_name && (
                            <span className="font-medium text-foreground mr-1.5">
                              {visitor.full_name}
                            </span>
                          )}
                          {visitor.company && (
                            <span className="text-[11px] text-muted-foreground">
                              ({visitor.company})
                            </span>
                          )}
                        </div>
                      ) : (
                        <span className="text-[11px] text-muted-foreground/60">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      {visitor.role ? (
                        <span className="inline-flex rounded bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                          {visitor.role}
                        </span>
                      ) : (
                        <span className="text-[11px] text-muted-foreground/60">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2.5 text-[11px] font-mono text-muted-foreground">
                      {visitor.source}
                    </td>
                    <td className="px-4 py-2.5 text-[11px] text-muted-foreground whitespace-nowrap">
                      {new Date(visitor.created_at).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => handleDelete(visitor.id, visitor.email)}
                        disabled={deleteMutation.isPending}
                        className="size-7 text-muted-foreground hover:text-destructive"
                        title="Delete lead"
                      >
                        <Trash2 className="size-3.5" />
                        <span className="sr-only">Delete</span>
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
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
    <div className="mx-auto max-w-4xl pb-12">
      <PageHeader
        title="Settings & Workspace"
        subtitle="Manage model configuration, technical audit settings, and captured visitor leads."
      />
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

      <VisitorLeadsSection />
    </div>
  );
}
