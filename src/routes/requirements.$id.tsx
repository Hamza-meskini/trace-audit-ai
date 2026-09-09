import { createFileRoute, Link, useParams } from "@tanstack/react-router";
import { useState } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  CheckCircle2,
  FileText,
  ListChecks,
  Loader2,
  MessageSquare,
  ShieldAlert,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { CoverageBadge, ReviewBadge } from "@/components/status";
import { DocumentInspector, ExtractedBlock, StructuredText } from "@/components/document-inspector";
import { useActiveProject } from "@/hooks/use-active-project";
import { useRequirement, useSaveReview } from "@/hooks/use-requirements";
import { useAuditProgress } from "@/hooks/use-audit";
import type { AtomicCondition, ReviewRequest } from "@/lib/api-client";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/requirements/$id")({
  head: () => ({ meta: [{ title: "Requirement Review — TraceAudit" }] }),
  component: RequirementDetail,
});

const statusLabels: Record<string, string> = {
  PROVEN: "Supported",
  FAILED: "Failed",
  UNTESTED: "Not tested",
  INCONCLUSIVE: "Inconclusive",
  PENDING: "Partially verified",
  NOT_APPLICABLE: "Not applicable",
  NOT_EVALUATED: "Not evaluated",
};
const statusColors: Record<string, string> = {
  PROVEN: "bg-success-soft text-success",
  FAILED: "bg-critical-soft text-critical",
  UNTESTED: "bg-muted text-muted-foreground",
  INCONCLUSIVE: "bg-warning-soft text-warning",
  PENDING: "bg-info-soft text-primary",
  NOT_APPLICABLE: "bg-muted text-muted-foreground",
};
function requiredTarget(condition: AtomicCondition) {
  const range =
    condition.min_value != null || condition.max_value != null
      ? `${condition.min_value ?? "…"} to ${condition.max_value ?? "…"}`
      : condition.threshold;
  return (
    [condition.operator, range, condition.unit]
      .filter((value) => value != null && value !== "")
      .join(" ") || "See condition text"
  );
}
function RequirementDetail() {
  const { id } = useParams({ from: "/requirements/$id" });
  const { activeProjectId } = useActiveProject();
  return <ReviewWorkspace key={`${activeProjectId}-${id}`} projectId={activeProjectId} id={id} />;
}

function ReviewWorkspace({ projectId, id }: { projectId: string; id: string }) {
  const query = useRequirement(projectId, id);
  const saveReview = useSaveReview(projectId, id);
  const { data: auditJob } = useAuditProgress(projectId);
  const auditActive = auditJob?.status === "queued" || auditJob?.status === "running";
  const [tab, setTab] = useState<"conditions" | "source" | "review">("conditions");
  const [selection, setSelection] = useState<{
    documentId: string;
    page: number;
    context: string;
  } | null>(null);
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");
  const [action, setAction] = useState<ReviewRequest["action"]>("Comment");
  if (query.isPending)
    return (
      <div role="status" className="flex justify-center gap-2 p-20">
        <Loader2 className="size-5 animate-spin" />
        Loading requirement and sources…
      </div>
    );
  if (query.isError || !query.data)
    return (
      <div role="alert" className="rounded-xl border bg-card p-8">
        <h1 className="text-lg font-semibold">Requirement unavailable</h1>
        <p className="my-3 text-sm text-muted-foreground">
          Check your connection and active project.
        </p>
        <Button onClick={() => query.refetch()}>Retry</Button>
      </div>
    );
  const req = query.data;
  const results = req.condition_results || [];
  const conditions = req.contract?.conditions || [];
  const conditionRows: AtomicCondition[] = conditions.length
    ? conditions
    : results.map((result) => ({
        condition_id: result.condition_id,
        description: result.description || null,
      }));
  const gate = req.diagnostics?.review_gate;
  const catalog = req.diagnostics?.evidence_catalog || [];
  const sourceBlocks = req.source_blocks || [];
  const sourceId = req.source_document_id;
  const shownDocument =
    selection ||
    (sourceId
      ? {
          documentId: sourceId,
          page: sourceBlocks[0]?.page_number || 1,
          context: "Requirement source document",
        }
      : null);
  const history = req.review_history || [];
  const provenCount = results.filter((item) => item.status === "PROVEN").length;
  const logic = req.contract?.logic?.operator;
  const inspect = (documentId: string, page: number | null | undefined, context: string) =>
    setSelection({ documentId, page: page || 1, context });
  const submitReview = async () => {
    if (
      action !== "Comment" &&
      !window.confirm(
        `Save this human review as ${action}? The AI coverage verdict will not be changed.`,
      )
    )
      return;
    try {
      await saveReview.mutateAsync({ reviewer: reviewer.trim(), comment: comment.trim(), action });
      setComment("");
      toast.success("Review saved to this requirement");
    } catch {
      toast.error("Review was not saved. Your text is preserved; please retry.");
    }
  };
  return (
    <div className="mx-auto max-w-[1700px]">
      <Link
        to="/requirements"
        className="mb-4 inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        All requirements
      </Link>
      <header className="mb-5 rounded-xl border bg-card p-5 md:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 max-w-3xl">
            <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
              <span className="font-mono">{req.req_code}</span>
              <span>·</span>
              {req.category}
              <span>·</span>
              {req.severity} priority
            </div>
            <h1 className="text-xl font-semibold tracking-tight">{req.title}</h1>
          </div>
          <Button variant="outline" onClick={() => setTab("review")}>
            <MessageSquare className="size-4" />
            Review decision
          </Button>
        </div>
        <div className="mt-5 flex flex-wrap gap-x-8 gap-y-3 border-t pt-4 text-xs">
          <div>
            <p className="mb-1.5 text-muted-foreground">AI coverage verdict</p>
            <CoverageBadge status={req.coverage_status} />
          </div>
          <div>
            <p className="mb-1.5 text-muted-foreground">Workflow state</p>
            <ReviewBadge state={req.review_state} />
          </div>
          <div>
            <p className="mb-1.5 text-muted-foreground">Condition coverage</p>
            <span className="font-medium">
              {results.length
                ? `${provenCount} / ${results.length} supported`
                : "Details not recorded"}
            </span>
          </div>
          <div>
            <p className="mb-1.5 text-muted-foreground">Human review</p>
            <span className="font-medium">
              {history.some((item) => item.action !== "Comment")
                ? "Decision recorded"
                : "No human decision recorded"}
            </span>
          </div>
          <div>
            <p className="mb-1.5 text-muted-foreground">Assessment confidence</p>
            <span className="font-medium">
              {Math.round(req.confidence)}%{" "}
              <span className="font-normal text-muted-foreground">· not measured accuracy</span>
            </span>
          </div>
        </div>
      </header>
      {gate?.required && (
        <section
          className="mb-5 flex gap-3 rounded-xl border border-warning/30 bg-warning-soft p-4"
          aria-label="Review required"
        >
          <ShieldAlert className="mt-0.5 size-5 shrink-0 text-warning" />
          <div>
            <h2 className="text-sm font-semibold">Human review required before closure</h2>
            <ul className="mt-2 space-y-1 text-sm">
              {gate.reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </div>
        </section>
      )}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-4">
          <nav
            className="flex gap-1 rounded-xl border bg-card p-1"
            aria-label="Requirement review sections"
          >
            {(
              [
                ["conditions", "Conditions & evidence", ListChecks],
                ["source", "Requirement source", FileText],
                ["review", "Review history", MessageSquare],
              ] as const
            ).map(([key, label, Icon]) => (
              <button
                key={key}
                aria-current={tab === key ? "page" : undefined}
                onClick={() => setTab(key)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-2 rounded-lg px-2 py-3 text-xs transition-colors",
                  tab === key
                    ? "bg-primary/10 font-semibold text-primary"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                <Icon className="size-4 shrink-0" />
                {label}
              </button>
            ))}
          </nav>
          {tab === "conditions" && (
            <>
              <section className="rounded-xl border bg-card p-5">
                <h2 className="mb-3 text-sm font-semibold">Assessment summary</h2>
                <p className="text-sm leading-relaxed">
                  {req.ai_analysis || "No analysis has been recorded for this requirement."}
                </p>
                {req.ai_recommendation && (
                  <div className="mt-4 border-t pt-3 text-sm">
                    <p className="mb-1 font-medium">Recommended next step</p>
                    <p className="text-muted-foreground">{req.ai_recommendation}</p>
                  </div>
                )}
                {logic && (
                  <div className="mt-4 rounded-lg bg-muted/60 p-3 text-xs">
                    <strong className="font-mono">{logic}</strong>
                    <p className="mt-1 text-muted-foreground">
                      {logic === "ANY_OF"
                        ? "One demonstrated alternative can satisfy this requirement; unused paths do not all need to pass."
                        : logic === "IF_THEN"
                          ? "Evaluate the trigger first, then its obligations when applicable."
                          : "Every applicable mandatory condition must be supported."}
                    </p>
                  </div>
                )}
                {req.contract?.contract_complete === false && (
                  <p className="mt-3 text-sm text-warning">
                    Extraction completeness was not established.{" "}
                    {(req.contract.unmapped_obligations || []).join("; ")}
                  </p>
                )}
              </section>
              {!conditionRows.length && (
                <div className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
                  This older audit did not save atomic-condition details. Run a new audit to
                  populate them. The existing verdict has not been altered.
                </div>
              )}
              {conditionRows.map((condition, index) => {
                const result = results.find((item) => item.condition_id === condition.condition_id);
                const status = result?.status || "NOT_EVALUATED";
                const reviewedBySecondary =
                  req.diagnostics?.secondary_adjudication_resolved_ids?.includes(
                    condition.condition_id,
                  );
                return (
                  <article
                    key={condition.condition_id}
                    className="overflow-hidden rounded-xl border bg-card"
                  >
                    <div className="flex items-start gap-3 p-5">
                      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-muted font-mono text-xs">
                        C{index + 1}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-mono text-[11px] text-muted-foreground">
                            {condition.condition_id}
                          </span>
                          <span
                            className={cn(
                              "rounded-md px-2 py-1 text-xs font-medium",
                              statusColors[status] || "bg-muted",
                            )}
                          >
                            {statusLabels[status] || status}
                          </span>
                        </div>
                        <h3 className="mt-2 text-sm font-medium leading-relaxed">
                          {condition.description ||
                            condition.parameter ||
                            "Condition description unavailable"}
                        </h3>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-4 border-y bg-muted/20 px-5 py-3 text-xs">
                      <div>
                        <p className="text-muted-foreground">Required</p>
                        <p className="mt-1 font-medium">{requiredTarget(condition)}</p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Observed</p>
                        <p className="mt-1 font-medium">
                          {result?.observed_value ??
                            (result?.observed_min_value != null ||
                            result?.observed_max_value != null
                              ? `${result.observed_min_value ?? "…"} to ${result.observed_max_value ?? "…"}`
                              : "Not recorded")}{" "}
                          {result?.observed_unit}
                        </p>
                      </div>
                    </div>
                    <div className="space-y-3 p-5">
                      {result?.reason && <p className="text-sm leading-relaxed">{result.reason}</p>}
                      {reviewedBySecondary && (
                        <p className="flex items-center gap-2 text-xs text-primary">
                          <CheckCircle2 className="size-4" />
                          Resolved by a secondary model review
                        </p>
                      )}
                      {result?.quote && (
                        <div className="rounded-lg border-l-2 border-primary bg-muted/25 p-3">
                          <StructuredText text={result.quote} />
                        </div>
                      )}
                      {result && (
                        <p className="text-xs text-muted-foreground">
                          {result.evidence_spans?.length
                            ? "Exact excerpt span recorded · verify semantics against the source"
                            : result.quote
                              ? "Quote available · exact source span not recorded"
                              : "No citation recorded"}
                        </p>
                      )}
                      <div className="flex flex-wrap gap-2">
                        {result?.evidence_ids?.map((evidenceId) => {
                          const ref = catalog.find((item) => item.evidence_id === evidenceId);
                          return ref ? (
                            <Button
                              key={evidenceId}
                              variant="outline"
                              size="sm"
                              className="max-w-full"
                              onClick={() =>
                                inspect(
                                  ref.document_id,
                                  ref.page_number,
                                  `${condition.condition_id} · ${evidenceId}`,
                                )
                              }
                            >
                              <FileText className="size-3.5 shrink-0" />
                              <span className="truncate">
                                {evidenceId} · {ref.document_name}{" "}
                                {ref.page_number ? `· p.${ref.page_number}` : ""}
                              </span>
                              <ArrowUpRight className="size-3.5 shrink-0" />
                            </Button>
                          ) : (
                            <span key={evidenceId} className="text-xs text-muted-foreground">
                              {evidenceId}: source mapping unavailable
                            </span>
                          );
                        })}
                      </div>
                      {result && (
                        <details className="border-t pt-3 text-xs">
                          <summary className="cursor-pointer text-muted-foreground">
                            Validation & provenance ·{" "}
                            {result.validation_state?.toLowerCase() || "not recorded"}
                          </summary>
                          <dl className="my-3 grid grid-cols-2 gap-2">
                            <dt>Execution</dt>
                            <dd>{result.execution_state || "Not recorded"}</dd>
                            <dt>Subject identity</dt>
                            <dd>{result.subject_identity || "Not recorded"}</dd>
                            <dt>Population scope</dt>
                            <dd>{result.coverage_scope || "Not recorded"}</dd>
                          </dl>
                          <ul className="space-y-2">
                            {result.validation_notes?.map((note) => (
                              <li key={note}>{note}</li>
                            ))}
                          </ul>
                          {result.evidence_spans?.map((span, i) => (
                            <p className="mt-2 text-muted-foreground" key={i}>
                              {span.evidence_id}: characters {span.start_offset}–{span.end_offset}{" "}
                              in the reasoning excerpt (not PDF coordinates).
                            </p>
                          ))}
                        </details>
                      )}
                    </div>
                  </article>
                );
              })}
              {!!req.evidence.length && (
                <details className="rounded-xl border bg-card p-5">
                  <summary className="cursor-pointer text-sm font-medium">
                    All linked evidence ({req.evidence.length})
                  </summary>
                  <div className="mt-4 space-y-4">
                    {req.evidence.map((item) => (
                      <div key={item.id} className="border-t pt-3">
                        <p className="mb-2 text-xs font-medium">
                          {item.document_name} · {item.status}
                        </p>
                        <StructuredText text={item.quote} />
                        {item.document_id && (
                          <Button
                            className="mt-3"
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              inspect(item.document_id!, item.page_number, "Linked evidence")
                            }
                          >
                            Inspect original source
                            <ArrowUpRight className="size-3.5" />
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
          {tab === "source" && (
            <>
              <section className="rounded-xl border bg-card p-5">
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold">Requirement text</h2>
                  {sourceId && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        inspect(
                          sourceId,
                          sourceBlocks[0]?.page_number,
                          "Requirement source document",
                        )
                      }
                    >
                      Open original
                      <ArrowUpRight className="size-4" />
                    </Button>
                  )}
                </div>
                <div className="mt-4">
                  <StructuredText text={req.description || req.title} />
                </div>
                <p className="mt-3 text-xs text-muted-foreground">
                  {req.source_document || "Original source document was not recorded."}
                </p>
              </section>
              <p className="px-1 text-xs leading-relaxed text-muted-foreground">
                These blocks are page context matched by requirement ID, not a claim that every item
                belongs to this clause. Check the original pages for tables, diagrams and
                cross-references.
              </p>
              {sourceBlocks.map((block) => (
                <ExtractedBlock
                  key={block.id}
                  block={block}
                  projectId={projectId}
                  isPdf={!!req.source_document?.toLowerCase().endsWith(".pdf")}
                />
              ))}
              {!sourceBlocks.length && (
                <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">
                  No exact requirement-ID location was saved or found.{" "}
                  {sourceId
                    ? "Use the original document inspector to locate the clause; no page number has been guessed."
                    : "The uploaded source document could not be uniquely resolved."}
                </div>
              )}
            </>
          )}
          {tab === "review" && (
            <>
              <section className="rounded-xl border bg-card p-5">
                <h2 className="text-sm font-semibold">Record a human review</h2>
                <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                  A review changes workflow state, not the AI verdict. Names are self-reported in
                  this local workspace; authenticated sign-off is not implemented.
                </p>
                <div className="mt-5 space-y-4">
                  <label className="block text-xs font-medium">
                    Reviewer name
                    <Input
                      value={reviewer}
                      maxLength={120}
                      onChange={(e) => setReviewer(e.target.value)}
                      placeholder="Your name"
                      className="mt-1.5"
                    />
                  </label>
                  <label className="block text-xs font-medium">
                    Decision
                    <select
                      value={action}
                      onChange={(e) => setAction(e.target.value as ReviewRequest["action"])}
                      className="mt-1.5 block w-full rounded-md border bg-background p-2 text-sm"
                    >
                      <option value="Comment">Add a comment</option>
                      <option value="Approved">Approve the assessment</option>
                      <option value="Rejected">Disagree with the assessment</option>
                      <option value="Reviewed">Mark human review complete</option>
                      <option value="Needs review">Keep under review</option>
                    </select>
                  </label>
                  <label className="block text-xs font-medium">
                    Rationale / comment
                    <Textarea
                      className="mt-1.5 min-h-28"
                      maxLength={5000}
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder="Explain your decision and reference the source evidence…"
                    />
                  </label>
                  <Button
                    disabled={
                      auditActive || !reviewer.trim() || !comment.trim() || saveReview.isPending
                    }
                    onClick={submitReview}
                  >
                    {saveReview.isPending && <Loader2 className="size-4 animate-spin" />}Save review
                  </Button>
                  {auditActive && (
                    <p role="status" className="text-xs text-muted-foreground">
                      Audit in progress. You can draft a review; save it after the new results
                      arrive.
                    </p>
                  )}
                </div>
              </section>
              <section className="rounded-xl border bg-card p-5">
                <h2 className="mb-4 text-sm font-semibold">Saved review history</h2>
                {history.length ? (
                  <ol className="space-y-5">
                    {[...history].reverse().map((event) => (
                      <li key={event.id} className="border-l-2 border-primary/25 pl-4">
                        <div className="flex flex-wrap justify-between gap-2 text-xs">
                          <strong>
                            {event.reviewer} · {event.action}
                          </strong>
                          <time className="text-muted-foreground">
                            {new Date(event.created_at).toLocaleString()}
                          </time>
                        </div>
                        <p className="mt-2 whitespace-pre-wrap break-words text-sm">
                          {event.comment}
                        </p>
                        <p className="mt-2 text-xs text-muted-foreground">
                          AI verdict at review: {event.ai_verdict}
                        </p>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    No human review events have been saved.
                  </p>
                )}
              </section>
            </>
          )}
          <details className="rounded-xl border bg-card p-4 text-xs">
            <summary className="cursor-pointer text-muted-foreground">
              Technical audit details
            </summary>
            <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words">
              {JSON.stringify({ contract: req.contract, diagnostics: req.diagnostics }, null, 2)}
            </pre>
          </details>
        </div>
        <aside className="min-w-0 xl:sticky xl:top-20">
          <div className="mb-2 flex items-center justify-between text-xs text-muted-foreground">
            <span>{shownDocument?.context || "Source inspector"}</span>
            {selection && sourceId && (
              <button className="text-primary underline" onClick={() => setSelection(null)}>
                Return to requirement source
              </button>
            )}
          </div>
          {shownDocument ? (
            <DocumentInspector
              key={`${shownDocument.documentId}-${shownDocument.page}`}
              projectId={projectId}
              documentId={shownDocument.documentId}
              initialPage={shownDocument.page}
            />
          ) : (
            <div className="rounded-xl border border-dashed p-10 text-center">
              <FileText className="mx-auto mb-3 size-8 text-muted-foreground" />
              <h2 className="text-sm font-medium">Inspect a source</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                Select an evidence citation to inspect its original document alongside the
                condition.
              </p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
