import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";

export function useTriggerAudit(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (options?: { model?: string; thinking_level?: string } | string) => {
      const opts = typeof options === "string" ? { model: options } : options;
      return api.triggerAudit(projectId, opts);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "stats"] });
      queryClient.invalidateQueries({ queryKey: ["documents", projectId] });
      queryClient.invalidateQueries({ queryKey: ["requirements", projectId] });
      queryClient.invalidateQueries({ queryKey: ["findings", projectId] });
    },
  });
}
