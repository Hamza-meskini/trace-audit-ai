import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type AuditProgress } from "@/lib/api-client";
import { isAuditActive } from "@/hooks/use-audit";

export function useDocuments(projectId: string, options?: { enabled?: boolean }) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.getDocuments(projectId),
    enabled: !!projectId && (options?.enabled ?? true),
    // Processing status only changes while an audit runs. AuditProgressPanel
    // invalidates this query when a run starts, which re-arms the interval.
    refetchInterval: () =>
      isAuditActive(queryClient.getQueryData<AuditProgress | null>(["audit-progress", projectId]))
        ? 4000
        : false,
  });
}

export function useUploadDocument(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, docType, version }: { file: File; docType?: string; version?: string }) =>
      api.uploadDocument(projectId, file, docType, version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "stats"] });
    },
  });
}

export function useUploadDocuments(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      files,
      docType,
      version,
    }: {
      files: File[];
      docType?: string;
      version?: string;
    }) => api.uploadDocuments(projectId, files, docType, version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "stats"] });
    },
  });
}

export function useDeleteDocument(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (docId: string) => api.deleteDocument(projectId, docId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "stats"] });
    },
  });
}
