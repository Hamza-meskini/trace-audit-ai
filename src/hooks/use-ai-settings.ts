import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type ApiAiSettings } from "@/lib/api-client";

export function useAiSettings() {
  return useQuery({
    queryKey: ["settings", "ai"],
    queryFn: () => api.getAiSettings(),
  });
}

export function useUpdateAiSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: { model?: string; thinking_level?: string }) => api.updateAiSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings", "ai"] });
    },
  });
}
