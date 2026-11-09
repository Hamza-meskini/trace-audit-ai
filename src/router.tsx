import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
import { isApiError } from "@/lib/api-client";

function getBasepath(): string | undefined {
  if (typeof window !== "undefined" && window.__DATABRICKS_ROOT_PATH__) {
    const trimmed = window.__DATABRICKS_ROOT_PATH__.replace(/\/+$/, "");
    return trimmed || undefined;
  }
  return undefined;
}

export const getRouter = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        // Retry only failures that can succeed on a second try; a 404 or a
        // validation error will not, and retrying just delays the message.
        retry: (failureCount, error) => failureCount < 2 && (!isApiError(error) || error.retryable),
      },
    },
  });
  const basepath = getBasepath();

  const router = createRouter({
    routeTree,
    ...(basepath ? { basepath } : {}),
    context: { queryClient },
    scrollRestoration: true,
    defaultPreloadStaleTime: 0,
  });

  return router;
};
