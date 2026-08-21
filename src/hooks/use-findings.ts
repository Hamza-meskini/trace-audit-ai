import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";

export function useFindings(
  projectId: string,
  filters?: { severity?: string; finding_type?: string; review_state?: string; category?: string }
) {
  return useQuery({
    queryKey: ["findings", projectId, filters],
    queryFn: () => api.getFindings(projectId, filters),
    enabled: !!projectId,
  });
}

export function useUpdateFinding(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      findingId,
      data,
    }: {
      findingId: string;
      data: { review_state?: string; assigned_to?: string; severity?: string };
    }) => api.updateFinding(projectId, findingId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["findings", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "stats"] });
    },
  });
}
