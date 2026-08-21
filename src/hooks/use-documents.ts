import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";

export function useDocuments(projectId: string) {
  return useQuery({
    queryKey: ["documents", projectId],
    queryFn: () => api.getDocuments(projectId),
    enabled: !!projectId,
    refetchInterval: 4000,
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
