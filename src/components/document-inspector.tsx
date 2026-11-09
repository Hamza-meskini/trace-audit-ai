import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileText,
  Image,
  Loader2,
  Quote,
  Table2,
} from "lucide-react";
import { api, getErrorMessage, type BlockMetadata, type DocumentBlock } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { citationPattern, containsCitation, splitByCitation } from "@/lib/highlight";

function Highlighted({ text, pattern }: { text: string; pattern: RegExp | null }) {
  return (
    <>
      {splitByCitation(text, pattern).map((segment, i) =>
        segment.match ? (
          <mark key={i} className="rounded-sm bg-warning/30 px-0.5 text-foreground">
            {segment.text}
          </mark>
        ) : (
          segment.text
        ),
      )}
    </>
  );
}

/** Render only text nodes, never HTML from a document or an LLM. */
export function StructuredText({
  text,
  highlight,
}: {
  text: string;
  /** Cited span to mark, matched case- and whitespace-insensitively. */
  highlight?: string | null | undefined;
}) {
  const pattern = citationPattern(highlight);
  const lines = text.split("\n");
  const groups: { table: boolean; lines: string[] }[] = [];
  for (const line of lines) {
    const table = line.trim().startsWith("|") && line.trim().endsWith("|");
    const last = groups.at(-1);
    if (last?.table === table) last.lines.push(line);
    else groups.push({ table, lines: [line] });
  }
  return (
    <div className="space-y-3 text-sm leading-relaxed">
      {groups.map((group, i) => {
        if (!group.table)
          return (
            <p key={i} className="whitespace-pre-wrap break-words">
              <Highlighted text={group.lines.join("\n")} pattern={pattern} />
            </p>
          );
        const rows = group.lines
          .filter((line) => !/^\|[\s|:-]+\|$/.test(line.trim()))
          .map((line) =>
            line
              .trim()
              .slice(1, -1)
              .split("|")
              .map((cell) => cell.trim()),
          );
        return (
          <div key={i} className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left text-xs">
              <thead className="bg-muted/60">
                <tr>
                  {rows[0]?.map((cell, j) => (
                    <th key={j} className="border-b px-3 py-2 font-medium whitespace-pre-wrap">
                      <Highlighted text={cell} pattern={pattern} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(1).map((row, j) => (
                  <tr key={j} className="border-b last:border-0">
                    {row.map((cell, k) => (
                      <td key={k} className="px-3 py-2 align-top whitespace-pre-wrap">
                        <Highlighted text={cell} pattern={pattern} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </div>
  );
}

function PageImage({
  projectId,
  documentId,
  page,
}: {
  projectId: string;
  documentId: string;
  page: number;
}) {
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  return (
    <div className="relative min-h-36 rounded-lg border bg-white">
      {state === "loading" && (
        <div
          role="status"
          className="flex items-center justify-center gap-2 p-10 text-sm text-slate-500"
        >
          <Loader2 className="size-4 animate-spin" />
          Loading original page…
        </div>
      )}
      {state === "error" ? (
        <p role="alert" className="p-6 text-sm text-slate-600">
          Page preview unavailable. Open the original file to inspect this source.
        </p>
      ) : (
        <img
          src={api.pageUrl(projectId, documentId, page)}
          alt={`Original document, page ${page}. Includes source text, tables and figures.`}
          className={cn("w-full rounded-lg", state === "loading" && "hidden")}
          onLoad={() => setState("ready")}
          onError={() => setState("error")}
        />
      )}
    </div>
  );
}

export function ExtractedBlock({
  block,
  projectId,
  isPdf = true,
  highlight,
  cited = false,
}: {
  block: DocumentBlock;
  projectId: string;
  isPdf?: boolean;
  highlight?: string | null | undefined;
  /** This block is the cited chunk or contains the cited span. */
  cited?: boolean;
}) {
  const kind = block.metadata.block_type || "text";
  const visual = block.metadata.visual_analysis;
  const Icon = kind === "table" ? Table2 : kind === "figure" ? Image : FileText;
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (cited) ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [cited]);
  return (
    <article
      ref={ref}
      className={cn(
        "rounded-xl border bg-card p-4",
        cited && "border-warning ring-2 ring-warning/40",
      )}
      aria-label={cited ? "Cited source passage" : undefined}
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-2">
          <Icon className="size-4" />
          {kind.replaceAll("_", " ")}
          {cited && (
            <span className="rounded bg-warning-soft px-1.5 py-0.5 font-medium text-warning">
              Cited
            </span>
          )}
        </span>
        <span>{block.page_number ? `Page ${block.page_number}` : "Page not recorded"}</span>
      </div>
      {kind === "figure" && isPdf && block.page_number && (
        <div className="mb-3">
          <PageImage
            key={block.id}
            projectId={projectId}
            documentId={block.document_id}
            page={block.page_number}
          />
          <p className="mt-1 text-xs text-muted-foreground">
            Original page containing this figure; not an AI-generated reconstruction.
          </p>
        </div>
      )}
      <StructuredText text={block.content} highlight={highlight} />
      {visual && (
        <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">
          Visual interpretation: {visual.status || "not available"}
          {visual.provider ? ` · ${visual.provider}` : ""}
          {visual.reason ? ` — ${visual.reason}` : ""}. The original page remains authoritative.
        </p>
      )}
    </article>
  );
}

export function DocumentInspector({
  projectId,
  documentId,
  initialPage = 1,
  highlight,
  chunkId,
}: {
  projectId: string;
  documentId: string;
  initialPage?: number;
  /** Cited span text to highlight in the extracted content of the cited page. */
  highlight?: string | null | undefined;
  /** Evidence chunk the citation points at, outlined when it is on the cited page. */
  chunkId?: string | null | undefined;
}) {
  const [page, setPage] = useState(initialPage);
  const hasCitation = !!highlight?.trim() || !!chunkId;
  // The original page is rendered as an image with no text layer, so a citation
  // opens on the extracted content, where the cited span can actually be marked.
  const [mode, setMode] = useState<"original" | "extracted">(
    hasCitation ? "extracted" : "original",
  );
  const [zoom, setZoom] = useState(false);
  const query = useQuery({
    queryKey: ["document-inspection", projectId, documentId, page],
    queryFn: ({ signal }) => api.inspectDocument(projectId, documentId, page, signal),
    enabled: !!documentId && !!projectId,
  });
  if (query.isPending)
    return (
      <div role="status" className="flex items-center gap-2 p-8 text-sm">
        <Loader2 className="size-4 animate-spin" />
        Loading document…
      </div>
    );
  if (query.isError)
    return (
      <div role="alert" className="p-6 text-sm">
        <AlertCircle className="mb-2 size-5" />
        Could not load this document. {getErrorMessage(query.error)}{" "}
        <Button variant="outline" size="sm" onClick={() => query.refetch()}>
          Retry
        </Button>
      </div>
    );
  const data = query.data;
  const onCitedPage = hasCitation && page === initialPage;
  const pattern = citationPattern(highlight);
  const isCited = (block: DocumentBlock) =>
    onCitedPage &&
    ((!!chunkId && block.id === chunkId) || containsCitation(block.content, pattern));
  const citedOnPage = data.blocks.some(isCited);
  const pageCount = Math.max(data.document.page_count || 1, ...data.available_pages, 1);
  return (
    <section
      className="min-w-0 overflow-hidden rounded-xl border bg-card"
      aria-label="Document inspector"
    >
      <div className="border-b p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="break-words text-sm font-semibold">{data.document.original_filename}</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {data.document.doc_type} · {data.document.version} · {pageCount} pages / sheets
            </p>
          </div>
          <a
            href={api.documentUrl(projectId, documentId)}
            target="_blank"
            rel="noreferrer"
            className="shrink-0 rounded-md border p-2 hover:bg-muted"
            aria-label="Open original file"
          >
            <ExternalLink className="size-4" />
          </a>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div
            className="inline-flex rounded-lg bg-muted p-1"
            role="group"
            aria-label="Document view"
          >
            {(["original", "extracted"] as const).map((value) => (
              <button
                key={value}
                aria-pressed={mode === value}
                onClick={() => setMode(value)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs",
                  mode === value && "bg-background font-medium",
                )}
              >
                {value === "original" ? "Original page" : "Extracted content"}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              aria-label="Previous page"
              disabled={page <= 1}
              onClick={() => setPage(page - 1)}
            >
              <ChevronLeft className="size-4" />
            </Button>
            <label className="text-xs">
              Page{" "}
              <select
                aria-label="Select document page"
                className="rounded border bg-background p-1"
                value={page}
                onChange={(e) => setPage(Number(e.target.value))}
              >
                {Array.from({ length: pageCount }, (_, i) => (
                  <option key={i} value={i + 1}>
                    {i + 1}
                  </option>
                ))}
              </select>{" "}
              / {pageCount}
            </label>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Next page"
              disabled={page >= pageCount}
              onClick={() => setPage(page + 1)}
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
        </div>
      </div>
      {hasCitation && (
        <div
          role="note"
          aria-label="Cited text"
          className="flex flex-wrap items-start gap-2 border-b bg-warning-soft/60 px-4 py-3 text-xs"
        >
          <Quote className="mt-0.5 size-3.5 shrink-0 text-warning" />
          <div className="min-w-0 flex-1">
            {highlight?.trim() && (
              <p className="line-clamp-3 break-words italic">“{highlight.trim()}”</p>
            )}
            <p className="mt-1 text-muted-foreground">
              {!onCitedPage
                ? `The citation is on page ${initialPage}.`
                : citedOnPage
                  ? "Marked in the extracted content of this page. Confirm it against the original page."
                  : "The cited text was not found in the extracted content of this page. Check the original page."}
            </p>
          </div>
          {onCitedPage && citedOnPage && mode === "original" && (
            <button className="shrink-0 underline" onClick={() => setMode("extracted")}>
              Show marked text
            </button>
          )}
          {!onCitedPage && (
            <button className="shrink-0 underline" onClick={() => setPage(initialPage)}>
              Go to cited page
            </button>
          )}
        </div>
      )}
      <div className="max-h-[75vh] overflow-auto bg-muted/30 p-3">
        {mode === "original" && data.is_pdf ? (
          <>
            <div className="mb-2 flex justify-between text-xs text-muted-foreground">
              <span>Original file render · text, tables & images preserved</span>
              <button className="underline" onClick={() => setZoom(!zoom)}>
                {zoom ? "Fit width" : "Enlarge"}
              </button>
            </div>
            <div className={zoom ? "min-w-[1000px]" : ""}>
              <PageImage
                key={`${documentId}-${page}`}
                projectId={projectId}
                documentId={documentId}
                page={page}
              />
            </div>
          </>
        ) : (
          <div className="space-y-3">
            {mode === "original" && !data.is_pdf && (
              <p className="rounded-lg border bg-background p-3 text-sm">
                Original page preview is available for PDFs. These are the extracted blocks;
                download the original Office file for its exact layout.
              </p>
            )}
            {data.blocks.length ? (
              data.blocks.map((block) => (
                <ExtractedBlock
                  key={block.id}
                  block={block}
                  projectId={projectId}
                  isPdf={data.is_pdf}
                  highlight={onCitedPage ? highlight : null}
                  cited={isCited(block)}
                />
              ))
            ) : (
              <p className="p-8 text-center text-sm text-muted-foreground">
                No extracted blocks on this page. Inspect the original page; it may contain
                unreadable or unindexed content.
              </p>
            )}
          </div>
        )}
      </div>
      {data.diagnostics?.["parser_fallback"] ? (
        <p
          role="alert"
          className="border-t bg-amber-50 p-3 text-xs text-amber-900 dark:bg-amber-950 dark:text-amber-100"
        >
          The preferred layout parser failed for this document and a local fallback produced its
          text, so tables and figures may be degraded. Requirements that cite it stay under review.
        </p>
      ) : null}
      <details className="border-t p-4 text-xs">
        <summary className="cursor-pointer font-medium">
          Processing & source details · {data.counts.tables} table blocks · {data.counts.figures}{" "}
          figures
        </summary>
        <p className="mt-2 text-muted-foreground">
          {data.counts.blocks} indexed blocks. Detection counts are not a guarantee that every
          element was extracted.
        </p>
        <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
          {JSON.stringify({ profile: data.profile, diagnostics: data.diagnostics }, null, 2)}
        </pre>
      </details>
    </section>
  );
}
