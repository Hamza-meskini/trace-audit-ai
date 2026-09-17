import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";

function getBasepath(): string | undefined {
  if (typeof window !== "undefined" && window.__DATABRICKS_ROOT_PATH__) {
    const trimmed = window.__DATABRICKS_ROOT_PATH__.replace(/\/+$/, "");
    return trimmed || undefined;
  }
  return undefined;
}

export const getRouter = () => {
  const queryClient = new QueryClient();
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
