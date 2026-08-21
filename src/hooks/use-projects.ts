import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ApiProject } from "@/lib/api-client";

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
  return useQuery({
    queryKey: ["projects", id, "stats"],
    queryFn: () => api.getProjectStats(id),
    enabled: !!id,
    refetchInterval: 5000,
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; product_name: string; product_category?: string; company?: string; description?: string }) =>
      api.createProject(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}
