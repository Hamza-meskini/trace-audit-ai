/**
 * TraceAudit API Client
 * Typed HTTP client connecting frontend to the FastAPI backend.
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000/api";

export interface ApiProject {
  id: string;
  name: string;
  audit_id: string;
  product_name: string;
  product_category: string;
  company: string;
  status: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApiProjectStats {
  requirements: number;
  coverage: number;
  supported: number;
  partial: number;
  missing: number;
  conflict: number;
  documents: number;
  evidence_segments: number;
  findings: number;
}

export interface ApiDocument {
  id: string;
  project_id: string;
  filename: string;
  original_filename: string;
  doc_type: string;
  version: string;
  page_count: number | null;
  file_size: number | null;
  processing_status: "Indexed" | "Processing" | "Queued" | "Error";
  requirements_linked: number;
  uploaded_at: string;
  updated_at: string;
}

export interface ApiEvidenceItem {
  id: string;
  document_name: string;
  page_number: number | null;
  quote: string;
  status: "Supports requirement" | "Potential conflict" | "Supporting evidence";
  label: string;
  highlight?: string | null;
}

export interface ApiRequirement {
  id: string;
  project_id: string;
  req_code: string;
  title: string;
  description: string | null;
  category: "Electrical" | "Safety" | "Environmental" | "Mechanical" | "Cybersecurity" | "Documentation" | string;
  source_document: string | null;
  sources_count: number;
  coverage_status: "Supported" | "Partial" | "Missing" | "Conflict";
  confidence: number;
  review_state: "Reviewed" | "Needs review" | "Open" | "Approved" | "Rejected";
  severity: "Critical" | "High" | "Medium" | "Low";
  ai_analysis: string | null;
  ai_recommendation: string | null;
  evidence: ApiEvidenceItem[];
  created_at: string;
  updated_at: string;
}

export interface ApiFinding {
  id: string;
  project_id: string;
  requirement_id: string | null;
  finding_code: string;
  finding_type:
    | "Missing evidence"
    | "Partial evidence"
    | "Potential conflict"
    | "Unsupported requirement"
    | "Duplicate requirement"
    | "Ambiguous requirement";
  severity: "Critical" | "High" | "Medium" | "Low";
  review_state: "Reviewed" | "Needs review" | "Open" | "Approved" | "Rejected";
  assigned_to: string | null;
  description: string | null;
  sources_count: number;
  category: string;
  requirement_title?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuditRunResponse {
  status: string;
  project_id: string;
  requirements_analyzed: number;
  documents_indexed: number;
  findings_generated: number;
}

// ── HTTP Helper ─────────────────────────────────────────────────────────────

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE_URL}${path}`;
  const headers = new Headers(options.headers || {});
  
  if (!(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(`API Error [${response.status}] ${response.statusText}: ${errorBody}`);
  }

  if (response.status === 204) {
    return {} as T;
  }

  return response.json();
}

export interface ModelOption {
  id: string;
  name: string;
  provider: string;
  description: string;
  thinking_supported?: boolean;
  default_thinking?: string;
  is_default?: boolean;
}

export interface ApiAiSettings {
  current_model: string;
  provider: string;
  thinking_level: "HIGH" | "MEDIUM" | "LOW" | "MINIMAL" | string;
  supported_thinking_levels: string[];
  has_gemini_key: boolean;
  has_openai_key: boolean;
  available_models: ModelOption[];
}

export const api = {
  // Projects
  getProjects: () => request<ApiProject[]>("/projects"),
  getProject: (id: string) => request<ApiProject>(`/projects/${id}`),
  getProjectStats: (id: string) => request<ApiProjectStats>(`/projects/${id}/stats`),
  createProject: (data: { name: string; product_name: string; product_category?: string; company?: string; description?: string }) =>
    request<ApiProject>("/projects", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateProject: (id: string, data: Partial<ApiProject>) =>
    request<ApiProject>(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteProject: (id: string) =>
    request<void>(`/projects/${id}`, {
      method: "DELETE",
    }),

  // Documents
  getDocuments: (projectId: string) => request<ApiDocument[]>(`/projects/${projectId}/documents`),
  getDocument: (projectId: string, docId: string) => request<ApiDocument>(`/projects/${projectId}/documents/${docId}`),
  uploadDocument: (projectId: string, file: File, docType = "", version = "v1.0") => {
    const formData = new FormData();
    formData.append("file", file);
    if (docType) formData.append("doc_type", docType);
    if (version) formData.append("version", version);
    return request<{ id: string; filename: string; original_filename: string; doc_type: string; processing_status: string }>(
      `/projects/${projectId}/documents`,
      {
        method: "POST",
        body: formData,
      }
    );
  },
  deleteDocument: (projectId: string, docId: string) =>
    request<void>(`/projects/${projectId}/documents/${docId}`, {
      method: "DELETE",
    }),

  // Requirements
  getRequirements: (
    projectId: string,
    filters?: { category?: string; status?: string; severity?: string; review?: string }
  ) => {
    const params = new URLSearchParams();
    if (filters?.category && filters.category !== "all") params.append("category", filters.category);
    if (filters?.status && filters.status !== "All") params.append("status", filters.status);
    if (filters?.severity && filters.severity !== "all") params.append("severity", filters.severity);
    if (filters?.review && filters.review !== "all") params.append("review", filters.review);

    const query = params.toString() ? `?${params.toString()}` : "";
    return request<ApiRequirement[]>(`/projects/${projectId}/requirements${query}`);
  },
  getRequirement: (projectId: string, reqId: string) =>
    request<ApiRequirement>(`/projects/${projectId}/requirements/${reqId}`),
  createRequirement: (projectId: string, data: { req_code: string; title: string; category?: string; severity?: string }) =>
    request<ApiRequirement>(`/projects/${projectId}/requirements`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Findings
  getFindings: (
    projectId: string,
    filters?: { severity?: string; finding_type?: string; review_state?: string; category?: string }
  ) => {
    const params = new URLSearchParams();
    if (filters?.severity && filters.severity !== "all") params.append("severity", filters.severity);
    if (filters?.finding_type && filters.finding_type !== "all") params.append("finding_type", filters.finding_type);
    if (filters?.review_state && filters.review_state !== "all") params.append("review_state", filters.review_state);
    if (filters?.category && filters.category !== "all") params.append("category", filters.category);

    const query = params.toString() ? `?${params.toString()}` : "";
    return request<ApiFinding[]>(`/projects/${projectId}/findings${query}`);
  },
  updateFinding: (projectId: string, findingId: string, data: { review_state?: string; assigned_to?: string; severity?: string }) =>
    request<ApiFinding>(`/projects/${projectId}/findings/${findingId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  // Audit Pipeline
  triggerAudit: (projectId: string, options?: { model?: string; thinking_level?: string }) =>
    request<AuditRunResponse>(`/projects/${projectId}/audit`, {
      method: "POST",
      body: JSON.stringify({
        model: options?.model || undefined,
        thinking_level: options?.thinking_level || undefined,
      }),
    }),

  // AI Configuration Settings
  getAiSettings: () => request<ApiAiSettings>("/settings/ai"),
  updateAiSettings: (data: { model?: string; thinking_level?: string }) =>
    request<ApiAiSettings>("/settings/ai", {
      method: "POST",
      body: JSON.stringify(data),
    }),
};
