import { useState, useEffect } from "react";
import { useProjects } from "./use-projects";
import type { ApiProject } from "@/lib/api-client";

const ACTIVE_PROJECT_KEY = "traceaudit_active_project_id";

export function useActiveProject() {
  const { data: projects, isLoading } = useProjects();
  const [selectedId, setSelectedId] = useState<string>(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem(ACTIVE_PROJECT_KEY) || "proj-001";
    }
    return "proj-001";
  });

  const selectProject = (id: string) => {
    setSelectedId(id);
    if (typeof window !== "undefined") {
      localStorage.setItem(ACTIVE_PROJECT_KEY, id);
      window.dispatchEvent(new Event("active_project_changed"));
    }
  };

  useEffect(() => {
    const handleStorage = () => {
      const stored = localStorage.getItem(ACTIVE_PROJECT_KEY);
      if (stored && stored !== selectedId) {
        setSelectedId(stored);
      }
    };
    window.addEventListener("storage", handleStorage);
    window.addEventListener("active_project_changed", handleStorage);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener("active_project_changed", handleStorage);
    };
  }, [selectedId]);

  // Find active project from list or default to clean placeholder
  const activeProject: ApiProject = projects?.find((p) => p.id === selectedId) ||
    projects?.[0] || {
      id: selectedId || "",
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
    projects: projects || [],
    isLoading,
    selectProject,
  };
}
