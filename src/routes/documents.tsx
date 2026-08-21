import { createFileRoute } from "@tanstack/react-router";
import { useState, useRef } from "react";
import { CheckCircle2, FileSpreadsheet, FileText, Loader2, Trash2, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { PageHeader, Panel } from "@/components/primitives";
import { Mono, Tag } from "@/components/status";
import { useActiveProject } from "@/hooks/use-active-project";
import { useDocuments, useUploadDocument, useDeleteDocument } from "@/hooks/use-documents";
import { useProjectStats } from "@/hooks/use-projects";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/documents")({
  head: () => ({
    meta: [
      { title: "Technical Documents — TraceAudit" },
      {
        name: "description",
        content: "Manage the technical documents used as evidence for your audit.",
      },
      { property: "og:title", content: "Technical Documents — TraceAudit" },
      {
        property: "og:description",
        content: "Upload, index and trace technical documents used as audit evidence.",
      },
    ],
  }),
  component: DocumentsPage,
});

const stages = ["Upload", "Extract", "Analyze", "Index evidence"];

function DocumentsPage() {
  const [dragging, setDragging] = useState(false);
  const { activeProjectId } = useActiveProject();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: documentsList, isLoading } = useDocuments(activeProjectId);
  const { data: stats } = useProjectStats(activeProjectId);
  const uploadDocMutation = useUploadDocument(activeProjectId);
  const deleteDocMutation = useDeleteDocument(activeProjectId);

  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      if (!file) continue;
      try {
        toast.info(`Uploading ${file.name}...`);
        await uploadDocMutation.mutateAsync({ file });
        toast.success(`${file.name} uploaded successfully!`);
      } catch (err: any) {
        toast.error(`Upload failed: ${err.message || err}`);
      }
    }
  };

  const projectStats = stats || {
    documents: documentsList?.length || 6,
    evidence_segments: 15,
    supported: 7,
  };

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title="Technical Documents"
        subtitle="Manage the documents used as evidence for this audit."
        actions={
          <Button onClick={() => fileInputRef.current?.click()}>
            <UploadCloud className="size-4" />
            Upload documents
          </Button>
        }
      />

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.docx,.xlsx,.csv"
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel className="lg:col-span-2" title="Upload technical documentation">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              handleFiles(e.dataTransfer.files);
            }}
            className={cn(
              "flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-10 text-center transition-colors",
              dragging ? "border-primary bg-info-soft" : "border-border bg-surface/60",
            )}
          >
            <UploadCloud className="size-6 text-muted-foreground" />
            <p className="mt-3 text-sm font-medium">Drag and drop files here</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Supported file types: PDF, DOCX, XLSX, CSV
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={() => fileInputRef.current?.click()}
            >
              Browse files
            </Button>
          </div>

          <ol className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {stages.map((s, i) => (
              <li
                key={s}
                className="rounded-lg border border-border bg-card px-3 py-2 text-xs"
              >
                <div className="flex items-center gap-1.5 text-muted-foreground">
                  <span className="font-mono">{i + 1}</span>
                  <CheckCircle2 className="size-3.5 text-success" />
                </div>
                <div className="mt-1 font-medium text-foreground">{s}</div>
              </li>
            ))}
          </ol>

          <div className="mt-5 flex items-center gap-2 rounded-lg border border-success/25 bg-success-soft px-4 py-2.5 text-sm text-success">
            <CheckCircle2 className="size-4" />
            {projectStats.documents} documents ready & indexed
          </div>
        </Panel>

        <Panel title="Evidence index">
          <dl className="space-y-4 text-sm">
            <div className="flex items-baseline justify-between">
              <dt className="text-muted-foreground">Documents</dt>
              <dd className="text-xl font-semibold tabular">{projectStats.documents}</dd>
            </div>
            <div className="flex items-baseline justify-between">
              <dt className="text-muted-foreground">Evidence segments</dt>
              <dd className="text-xl font-semibold tabular">
                {projectStats.evidence_segments.toLocaleString()}
              </dd>
            </div>
            <div className="flex items-baseline justify-between">
              <dt className="text-muted-foreground">Requirements covered</dt>
              <dd className="text-xl font-semibold tabular">{projectStats.supported}</dd>
            </div>
          </dl>
          <p className="mt-5 border-t border-border pt-4 text-xs leading-relaxed text-muted-foreground">
            Indexed segments are used to build requirement-to-evidence traceability. Source pages
            remain linked for human review.
          </p>
        </Panel>
      </div>

      <Panel className="mt-4" title="Document library" bodyClassName="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] text-sm">
            <thead className="sticky top-14 bg-card">
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted-foreground">
                <th className="px-5 py-2.5 font-medium">Document</th>
                <th className="px-5 py-2.5 font-medium">Type</th>
                <th className="px-5 py-2.5 font-medium">Version</th>
                <th className="px-5 py-2.5 font-medium">Pages</th>
                <th className="px-5 py-2.5 font-medium">Requirements linked</th>
                <th className="px-5 py-2.5 font-medium">Processing status</th>
                <th className="px-5 py-2.5 font-medium">Uploaded</th>
                <th className="px-5 py-2.5 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {(documentsList || []).map((d) => (
                <tr
                  key={d.id}
                  className="border-b border-border/70 transition-colors last:border-0 hover:bg-accent/50"
                >
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2.5">
                      {d.original_filename.endsWith(".xlsx") ? (
                        <FileSpreadsheet className="size-4 text-success" />
                      ) : (
                        <FileText className="size-4 text-primary" />
                      )}
                      <span className="font-medium">{d.original_filename}</span>
                    </div>
                  </td>
                  <td className="px-5 py-3 text-muted-foreground">{d.doc_type}</td>
                  <td className="px-5 py-3">
                    <Mono>{d.version}</Mono>
                  </td>
                  <td className="px-5 py-3 text-muted-foreground">
                    {d.page_count ? `${d.page_count} pages` : "—"}
                  </td>
                  <td className="px-5 py-3 tabular">{d.requirements_linked}</td>
                  <td className="px-5 py-3">
                    {d.processing_status === "Indexed" ? (
                      <span className="inline-flex items-center gap-1.5 text-xs text-success">
                        <CheckCircle2 className="size-3.5" /> Indexed
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 text-xs text-primary">
                        <Loader2 className="size-3.5 animate-spin" /> {d.processing_status}
                      </span>
                    )}
                  </td>
                  <td className="px-5 py-3 text-muted-foreground">
                    <Tag>{new Date(d.uploaded_at).toLocaleDateString()}</Tag>
                  </td>
                  <td className="px-5 py-3 text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        deleteDocMutation.mutate(d.id);
                        toast.success("Document deleted");
                      }}
                      className="text-muted-foreground hover:text-critical"
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
