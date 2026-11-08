import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ApiProject, type AuditProgress } from "@/lib/api-client";
import { isAuditActive } from "@/hooks/use-audit";

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: () => api.getProjects(),
  });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ["projects", id],
    queryFn: () => api.getProject(id),
    enabled: !!id,
  });
}

export function useProjectStats(id: string) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["projects", id, "stats"],
    queryFn: () => api.getProjectStats(id),
    enabled: !!id,
    // Stats cost several warehouse queries; they only move during an audit
    // (mutations and audit completion invalidate them explicitly).
    refetchInterval: () =>
      isAuditActive(queryClient.getQueryData<AuditProgress | null>(["audit-progress", id]))
        ? 5000
        : false,
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      name: string;
      product_name: string;
      product_category?: string;
      company?: string;
      description?: string;
    }) => api.createProject(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}
