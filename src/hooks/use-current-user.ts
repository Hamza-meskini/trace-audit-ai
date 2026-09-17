import { useQuery } from "@tanstack/react-query";
import { api, type ApiCurrentUser } from "@/lib/api-client";

export function useCurrentUser() {
  return useQuery<ApiCurrentUser>({
    queryKey: ["currentUser"],
    queryFn: () => api.getCurrentUser(),
    staleTime: 60_000,
    retry: 1,
  });
}
