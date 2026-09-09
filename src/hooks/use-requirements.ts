import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ReviewRequest } from "@/lib/api-client";

export function useRequirements(
  projectId: string,
  filters?: { category?: string; status?: string; severity?: string; review?: string },
) {
  return useQuery({
    queryKey: ["requirements", projectId, filters],
    queryFn: () => api.getRequirements(projectId, filters),
    enabled: !!projectId,
  });
}

export function useRequirement(projectId: string, reqId: string) {
  return useQuery({
    queryKey: ["requirements", projectId, reqId],
    queryFn: () => api.getRequirement(projectId, reqId),
    enabled: !!projectId && !!reqId,
  });
}

export function useSaveReview(projectId: string, reqId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (data: ReviewRequest) => api.saveReview(projectId, reqId, data),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["requirements", projectId] });
      client.invalidateQueries({ queryKey: ["findings", projectId] });
    },
  });
}
