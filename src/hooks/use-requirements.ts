import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ReviewRequest } from "@/lib/api-client";

export function useRequirements(
  projectId: string,
  filters?: { category?: string; status?: string; severity?: string; review?: string },
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ["requirements", projectId, filters],
    queryFn: ({ signal }) => api.getRequirements(projectId, filters, signal),
    enabled: !!projectId && (options?.enabled ?? true),
    // The list is the heaviest response in the app. Reviews and audit runs
    // invalidate it explicitly, so list -> detail -> list need not refetch it.
    staleTime: 60_000,
  });
}

export function useRequirement(projectId: string, reqId: string) {
  return useQuery({
    queryKey: ["requirements", projectId, reqId],
    queryFn: ({ signal }) => api.getRequirement(projectId, reqId, signal),
    enabled: !!projectId && !!reqId,
  });
}

export function useSaveReview(projectId: string, reqId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (data: ReviewRequest) => api.saveReview(projectId, reqId, data),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["requirements", projectId] });
      client.invalidateQueries({ queryKey: ["requirements", projectId, reqId] });
      client.invalidateQueries({ queryKey: ["findings", projectId] });
    },
  });
}
