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

  // Find active project from list or default to proj-001
  const activeProject: ApiProject =
    projects?.find((p) => p.id === selectedId) ||
    projects?.[0] || {
      id: "proj-001",
      name: "Industrial Controller X200",
      audit_id: "TA-2026-0042",
      product_name: "Industrial Controller X200",
      product_category: "Industrial electronic controller",
      company: "Atlas Motion Systems",
      status: "Analysis complete",
      description: "EU technical documentation audit for the X200 industrial electronic controller.",
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
