import { useState, useEffect } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useProjects } from "./use-projects";
import type { ApiProject } from "@/lib/api-client";

const ACTIVE_PROJECT_KEY = "traceaudit_active_project_id";

function readStoredProject(): string {
  if (typeof window === "undefined") return "";
  try {
    return localStorage.getItem(ACTIVE_PROJECT_KEY) || "";
  } catch {
    return "";
  }
}

/**
 * The active project. The `?project=` URL parameter wins so shared links open
 * the same project for everyone; localStorage remembers the last choice for
 * links without one; otherwise the first project is used.
 */
export function useActiveProject() {
  const { data: projects, isLoading } = useProjects();
  const navigate = useNavigate();
  const urlProjectId = useSearch({
    strict: false,
    select: (search: Record<string, unknown>) =>
      typeof search["project"] === "string" ? search["project"] : "",
  });
  const [storedId, setStoredId] = useState<string>(readStoredProject);

  const selectProject = (id: string) => {
    setStoredId(id);
    if (typeof window !== "undefined") {
      try {
        localStorage.setItem(ACTIVE_PROJECT_KEY, id);
      } catch {
        // Storage can be unavailable (private mode); the URL still carries it.
      }
      window.dispatchEvent(new Event("active_project_changed"));
    }
    navigate({ to: ".", search: (prev) => ({ ...prev, project: id }), replace: true });
  };

  useEffect(() => {
    const handleStorage = () => {
      const stored = readStoredProject();
      if (stored !== storedId) {
        setStoredId(stored);
      }
    };
    window.addEventListener("storage", handleStorage);
    window.addEventListener("active_project_changed", handleStorage);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener("active_project_changed", handleStorage);
    };
  }, [storedId]);

  const fromUrl = urlProjectId ? projects?.find((p) => p.id === urlProjectId) : undefined;
  const activeProject: ApiProject = fromUrl ||
    projects?.find((p) => p.id === storedId) ||
    projects?.[0] || {
      // While projects load, keep the URL's project id so queries start at once.
      id: isLoading ? urlProjectId : "",
      name: isLoading ? "Loading project…" : "No active project",
      audit_id: "—",
      product_name: "",
      product_category: "",
      company: "",
      status: "—",
      description: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

  return {
    activeProject,
    activeProjectId: activeProject.id,
    /** The URL names a project that does not exist (deleted, or no access). */
    urlProjectMissing: !!urlProjectId && !isLoading && !!projects && !fromUrl,
    urlProjectId,
    projects: projects || [],
    isLoading,
    selectProject,
  };
}
