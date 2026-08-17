import { createFileRoute, Link, notFound } from "@tanstack/react-router";
import { useState } from "react";
import {
  ArrowLeft,
  Check,
  FileText,
  MessageSquare,
  Sparkles,
  ThumbsDown,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { MetaItem, Panel } from "@/components/primitives";
import { CoverageBadge, Mono, ReviewBadge, SeverityBadge, Tag } from "@/components/status";
import { requirements, type Evidence, type ReviewState } from "@/lib/mock-data";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/requirements/$id")({
  loader: ({ params }) => {
    const requirement = requirements.find((r) => r.id === params.id);
    if (!requirement) throw notFound();
    return { requirement };
  },
  head: ({ loaderData }) => {
    if (!loaderData) {
      return { meta: [{ title: "Requirement unavailable — TraceAudit" }, { name: "robots", content: "noindex" }] };
    }
    const title = `${loaderData.requirement.id} — TraceAudit`;
    return {
      meta: [
        { title },
        { name: "description", content: loaderData.requirement.title },
        { property: "og:title", content: title },
        { property: "og:description", content: loaderData.requirement.title },
      ],
    };
  },
  component: RequirementDetail,
});

const evidenceTone: Record<Evidence["status"], string> = {
  "Supports requirement": "border-success/25 bg-success-soft text-success",
  "Potential conflict": "border-critical/25 bg-critical-soft text-critical",
  "Supporting evidence": "border-info/25 bg-info-soft text-info",
};

function RequirementDetail() {
  const { requirement } = Route.useLoaderData();
  const [review, setReview] = useState<ReviewState>(requirement.review);
  const [comment, setComment] = useState("");
  const [comments, setComments] = useState([
    {
      author: "A. Benali",
      time: "1h ago",
      text: "Supplier confirmed that 32 V is acceptable under transient conditions. Engineering review required.",
    },
  ]);

  const act = (state: ReviewState, message: string) => {
    setReview(state);
    toast.success(message, { description: `${requirement.id} updated` });
  };

  return (
    <div className="mx-auto max-w-[1400px]">
      <Link
        to="/requirements"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" />
        Requirements
      </Link>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-5">
          <Panel title="Requirement">
            <Mono className="text-muted-foreground">{requirement.id}</Mono>
            <h1 className="mt-2 text-lg font-semibold leading-snug">{requirement.title}</h1>
            <div className="mt-4 grid grid-cols-2 gap-4">
              <MetaItem label="Category" value={requirement.category} />
              <MetaItem label="Severity" value={<SeverityBadge severity={requirement.severity} />} />
              <MetaItem
                label="Status"
                value={
                  requirement.status === "Conflict" ? (
                    <span className="text-critical">Potential conflict</span>
                  ) : (
                    <CoverageBadge status={requirement.status} />
                  )
                }
              />
              <MetaItem label="Confidence" value={`${requirement.confidence}%`} />
              <MetaItem label="Review state" value={<ReviewBadge state={review} />} />
              <MetaItem label="Evidence sources" value={`${requirement.sources} sources`} />
            </div>
            <div className="mt-4 border-t border-border pt-4">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Primary source document
              </div>
              <div className="mt-1.5 flex items-center gap-2 text-sm">
                <FileText className="size-4 text-primary" />
                {requirement.sourceDocument}
              </div>
            </div>
          </Panel>

          <Panel
            title="AI Analysis"
            actions={
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <Sparkles className="size-3.5 text-primary" />
                Analysis complete
              </span>
            }
          >
            <p className="text-sm leading-relaxed text-foreground/90">
              {requirement.analysis ??
                "All indexed evidence segments are consistent with this requirement. No discrepancies were detected."}
            </p>
            <div className="mt-4 rounded-lg border border-border bg-surface/70 p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Recommended action
              </div>
              <p className="mt-1.5 text-sm">
                {requirement.recommendation ??
                  "No action required. Retain the current evidence set for the technical file."}
              </p>
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              Decision-support output. Final assessment requires human review.
            </p>
          </Panel>
        </div>

        <div className="space-y-4 lg:col-span-7">
          <Panel
            title="Evidence analysis"
            description={`${requirement.evidence.length} evidence segments linked from the indexed document set.`}
          >
            {requirement.evidence.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border bg-surface/60 px-5 py-10 text-center">
                <h3 className="text-sm font-semibold">No evidence identified</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  No indexed document segment supports this requirement.
                </p>
                <Button variant="outline" size="sm" className="mt-4" asChild>
                  <Link to="/documents">Upload supporting document</Link>
                </Button>
              </div>
            ) : (
              <ul className="space-y-3">
                {requirement.evidence.map((e) => (
                  <li
                    key={e.id}
                    className="rounded-lg border border-border bg-card p-4 transition-shadow hover:shadow-card"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <FileText className="size-4 text-primary" />
                        <span className="text-sm font-semibold">{e.label}</span>
                      </div>
                      <span
                        className={cn(
                          "rounded-md border px-2 py-0.5 text-xs font-medium",
                          evidenceTone[e.status],
                        )}
                      >
                        {e.status}
                      </span>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span className="font-mono">{e.document}</span>
                      <Tag>Page {e.page}</Tag>
                    </div>
                    <blockquote className="mt-3 border-l-2 border-border pl-3 text-sm italic leading-relaxed">
                      {e.highlight ? (
                        <>
                          {e.quote.split(e.highlight)[0]}
                          <mark className="rounded bg-warning-soft px-1 not-italic text-warning">
                            {e.highlight}
                          </mark>
                          {e.quote.split(e.highlight)[1]}
                        </>
                      ) : (
                        e.quote
                      )}
                    </blockquote>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Human review">
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => act("Approved", "Finding approved")}>
                <Check className="size-4" />
                Approve finding
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => act("Rejected", "Finding rejected")}
              >
                <X className="size-4" />
                Reject finding
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => act("Reviewed", "Marked as reviewed")}
              >
                <ThumbsDown className="size-4 rotate-180" />
                Mark as reviewed
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => toast.info("Clarification requested from document owner")}
              >
                <MessageSquare className="size-4" />
                Request clarification
              </Button>
            </div>

            <div className="mt-5 space-y-3">
              {comments.map((c, i) => (
                <div key={i} className="rounded-lg border border-border bg-surface/60 p-3">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">{c.author}</span>
                    <span>{c.time}</span>
                  </div>
                  <p className="mt-1.5 text-sm">{c.text}</p>
                </div>
              ))}
            </div>

            <div className="mt-4">
              <Textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Add a review comment..."
                className="min-h-[90px]"
              />
              <div className="mt-2 flex justify-end">
                <Button
                  size="sm"
                  disabled={!comment.trim()}
                  onClick={() => {
                    setComments((c) => [
                      ...c,
                      { author: "Hamza Meskini", time: "just now", text: comment.trim() },
                    ]);
                    setComment("");
                    toast.success("Comment added");
                  }}
                >
                  Add comment
                </Button>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
