import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuditProgress } from "@/lib/api-client";

/** Poll fast only while a run can change; idle tabs check back slowly. */
export const ACTIVE_POLL_MS = 3000;
export const IDLE_POLL_MS = 30000;

export function isAuditActive(job: Pick<AuditProgress, "status"> | null | undefined): boolean {
  return job?.status === "queued" || job?.status === "running" || job?.status === "cancelling";
}

export function useTriggerAudit(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (options?: { model?: string; thinking_level?: string } | string) => {
      const opts = typeof options === "string" ? { model: options } : options;
      return api.triggerAudit(projectId, opts);
    },
    onSuccess: (job) => {
      queryClient.setQueryData(["audit-progress", projectId], job);
      queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "stats"] });
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
      queryClient.invalidateQueries({ queryKey: ["findings", projectId] });
    },
  });
}

export function useAuditProgress(projectId: string) {
  return useQuery({
    queryKey: ["audit-progress", projectId],
    queryFn: () => api.getAuditProgress(projectId),
    enabled: !!projectId,
    // The idle interval still notices a run started from another tab or by a
    // colleague, without querying the SQL warehouse every few seconds forever.
    refetchInterval: (query) => (isAuditActive(query.state.data) ? ACTIVE_POLL_MS : IDLE_POLL_MS),
    retry: 1,
  });
}

export function useCancelAudit(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.cancelAudit(projectId),
    onSuccess: (job) => queryClient.setQueryData(["audit-progress", projectId], job),
  });
}
