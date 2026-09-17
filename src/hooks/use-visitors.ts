import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type RegisterVisitorRequest } from "@/lib/api-client";

export function useVisitors(search?: string) {
  return useQuery({
    queryKey: ["visitors", search || ""],
    queryFn: () => api.getVisitors(search),
  });
}

export function useRegisterVisitor() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: RegisterVisitorRequest) => api.registerVisitor(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["visitors"] });
    },
  });
}

export function useDeleteVisitor() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteVisitor(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["visitors"] });
    },
  });
}
