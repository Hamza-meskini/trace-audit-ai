import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { useAuditProgress, useCancelAudit } from "@/hooks/use-audit";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const stages = [
  "ingestion",
  "profiling",
  "extraction",
  "retrieval",
  "vision",
  "reasoning",
  "saving",
];
const labels: Record<string, string> = {
  queued: "Queued",
  cancelling: "Cancelling audit",
  cancelled: "Audit cancelled",
  ingestion: "Read documents",
  profiling: "Identify sources",
  extraction: "Extract requirements",
  retrieval: "Retrieve evidence",
  vision: "Interpret figures",
  reasoning: "Verify conditions",
  saving: "Save audit",
  complete: "Audit complete",
  failed: "Audit failed",
};

export function AuditProgressPanel({ projectId }: { projectId: string }) {
  const query = useAuditProgress(projectId);
  const client = useQueryClient();
  const lastTerminal = useRef("");
  const lastVisibleCheckpoint = useRef("");
  const [now, setNow] = useState(Date.now());
  const job = query.data;
  const cancel = useCancelAudit(projectId);
  const active =
    job?.status === "queued" || job?.status === "running" || job?.status === "cancelling";
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  useEffect(() => {
    if (!job || active || lastTerminal.current === `${job.run_id}-${job.status}`) return;
    lastTerminal.current = `${job.run_id}-${job.status}`;
    for (const key of [
      ["requirements", projectId],
      ["documents", projectId],
      ["findings", projectId],
      ["projects"],
    ])
      client.invalidateQueries({ queryKey: key });
  }, [job, active, client, projectId]);
  useEffect(() => {
    if (!job || !active || job.stage !== "reasoning") return;
    const checkpoint = `${job.run_id}-${job.completed}`;
    if (checkpoint === lastVisibleCheckpoint.current) return;
    lastVisibleCheckpoint.current = checkpoint;
    client.invalidateQueries({ queryKey: ["requirements", projectId] });
    client.invalidateQueries({ queryKey: ["findings", projectId] });
  }, [job, active, client, projectId]);
  if (query.isError)
    return (
      <div
        role="alert"
        className="mb-5 flex items-center gap-3 rounded-lg border bg-card p-3 text-xs"
      >
        <AlertCircle className="size-4" />
        Audit status is temporarily unavailable; this does not mean the run has stopped.
        <Button size="sm" variant="ghost" onClick={() => query.refetch()}>
          Retry
        </Button>
      </div>
    );
  if (!job) return null;
  const end = active ? now : Date.parse(job.updated_at);
  const seconds = Math.max(0, Math.floor((end - Date.parse(job.started_at)) / 1000));
  const stageIndex = stages.indexOf(job.stage);
  const failed =
    job.status === "failed" || job.status === "interrupted" || job.status === "cancelled";
  return (
    <section
      className={cn("mb-5 rounded-xl border bg-card p-4", failed && "border-critical/30")}
      aria-label="Audit progress"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          {active ? (
            <Loader2 className="size-4 animate-spin text-primary" />
          ) : failed ? (
            <AlertCircle className="size-4 text-critical" />
          ) : (
            <CheckCircle2 className="size-4 text-success" />
          )}
          <span aria-live="polite">
            {job.status === "interrupted"
              ? "Audit interrupted"
              : labels[job.status] || labels[job.stage] || job.stage}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {active && job.status !== "cancelling" && (
            <Button
              size="sm"
              variant="ghost"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              {cancel.isPending ? "Cancelling…" : "Cancel"}
            </Button>
          )}
          <span className="font-mono text-xs text-muted-foreground">
            {Math.floor(seconds / 60)}m {seconds % 60}s · {job.run_id.slice(0, 8)}
          </span>
        </div>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {job.error || job.message}
        {active && " · You can navigate away; this run continues on the server."}
      </p>
      {active && (
        <>
          <ol className="mt-4 flex flex-wrap gap-2">
            {stages.map((stage, i) => (
              <li
                key={stage}
                className={cn(
                  "rounded-md px-2 py-1 text-[11px]",
                  i === stageIndex
                    ? "bg-primary/10 font-semibold text-primary"
                    : "bg-muted text-muted-foreground",
                )}
              >
                {labels[stage]}
              </li>
            ))}
          </ol>
          {job.total > 0 && (
            <div className="mt-3">
              <div className="mb-1 flex justify-between text-xs">
                <span>Current stage</span>
                <span>
                  {job.completed} / {job.total}
                </span>
              </div>
              <progress
                aria-label={`${labels[job.stage]} progress`}
                className="h-1.5 w-full accent-primary"
                value={job.completed}
                max={job.total}
              />
            </div>
          )}
          <p className="mt-2 text-[11px] text-muted-foreground">
            Last stage update: {new Date(job.updated_at).toLocaleTimeString()}. A model call may run
            without intermediate updates; no estimated percentage is shown.
          </p>
        </>
      )}
      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-muted-foreground">Run activity</summary>
        <ol className="mt-2 max-h-40 space-y-1 overflow-auto">
          {[...job.events].reverse().map((event, i) => (
            <li key={i}>
              <time className="mr-2 font-mono text-muted-foreground">
                {new Date(event.at).toLocaleTimeString()}
              </time>
              {event.message || labels[event.stage] || event.stage}
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}
