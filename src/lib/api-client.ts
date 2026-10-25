/**
 * TraceAudit API Client
 * Typed HTTP client connecting frontend to the FastAPI backend.
 */

declare global {
  interface Window {
    __DATABRICKS_ROOT_PATH__?: string;
  }
}

export function getApiBaseUrl(): string {
  if (typeof import.meta !== "undefined" && import.meta.env?.["VITE_API_URL"]) {
    return import.meta.env["VITE_API_URL"];
  }
  if (typeof window !== "undefined" && window.__DATABRICKS_ROOT_PATH__) {
    const root = window.__DATABRICKS_ROOT_PATH__.replace(/\/+$/, "");
    return `${root}/api`;
  }
  if (typeof import.meta !== "undefined" && !import.meta.env?.PROD) {
    return "http://localhost:8000/api";
  }
  return "/api";
}

export const API_BASE_URL = getApiBaseUrl();

export type ReviewState = "Reviewed" | "Needs review" | "Open" | "Approved" | "Rejected";

export type Severity = "Critical" | "High" | "Medium" | "Low";

export type FindingType =
  | "Missing evidence"
  | "Partial evidence"
  | "Potential conflict"
  | "Unsupported requirement"
  | "Duplicate requirement"
  | "Ambiguous requirement"
  | "Inconclusive evidence";

export interface ApiCurrentUser {
  user: string;
  email: string | null;
  provider: string;
}

export interface ApiVisitor {
  id: string;
  email: string;
  full_name: string | null;
  company: string | null;
  role: string | null;
  notes: string | null;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface RegisterVisitorRequest {
  email: string;
  full_name?: string | undefined;
  company?: string | undefined;
  role?: string | undefined;
  notes?: string | undefined;
  source?: string | undefined;
}

export interface VisitorListResponse {
  total: number;
  items: ApiVisitor[];
}

export interface VisitorRegisterResponse {
  status: string;
  message: string;
  lead: ApiVisitor;
}

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
  unknown: number;
  not_applicable: number;
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
  document_id?: string | null;
  chunk_id?: string | null;
  metadata?: BlockMetadata;
}

export interface BlockMetadata {
  block_type?: string;
  section_path?: string[];
  table?: { caption?: string; headers?: string[]; row_count?: number };
  visual_analysis?: {
    status?: string;
    description?: string;
    caption?: string;
    reason?: string;
    provider?: string;
  };
}
export interface DocumentBlock {
  id: string;
  document_id: string;
  page_number: number | null;
  content: string;
  metadata: BlockMetadata;
}
export interface DocumentInspection {
  document: ApiDocument;
  is_pdf: boolean;
  available_pages: number[];
  profile: Record<string, unknown>;
  diagnostics: Record<string, unknown>;
  blocks: DocumentBlock[];
  counts: { blocks: number; tables: number; figures: number };
}
export interface AtomicCondition {
  condition_id: string;
  description?: string | null;
  parameter?: string | null;
  operator?: string | null;
  right_operand?: string | null;
  threshold?: string | number | boolean | null;
  unit?: string | null;
  min_value?: number | null;
  max_value?: number | null;
  condition_role?: string;
  requires_visual_evidence?: boolean;
}
export interface ConditionResult {
  condition_id: string;
  description?: string | null;
  status: string;
  reason?: string | null;
  validation_state: string;
  validation_notes: string[];
  observed_value?: string | null;
  observed_min_value?: number | null;
  observed_max_value?: number | null;
  observed_unit?: string | null;
  execution_state?: string;
  subject_identity?: string;
  coverage_scope?: string;
  evidence_ids: string[];
  quote?: string | null;
  evidence_spans?: {
    evidence_id: string;
    exact_quote: string;
    start_offset: number;
    end_offset: number;
    document_name?: string;
    page_number?: number;
  }[];
}
export interface ReviewEvent {
  id: string;
  action: string;
  reviewer: string;
  comment: string;
  created_at: string;
  ai_verdict: string;
  previous_review_state?: string;
  resolution_type?: ReviewResolutionType;
  human_verdict?: CoverageStatus | null;
}
export type CoverageStatus =
  "Supported" | "Partial" | "Missing" | "Conflict" | "Unknown" | "Not applicable";
export type ReviewResolutionType =
  | "Confirm AI assessment"
  | "Override verdict"
  | "Evidence issue"
  | "Contract correction"
  | "Comment";
export interface ReviewRequest {
  action: "Approved" | "Rejected" | "Reviewed" | "Needs review" | "Comment";
  reviewer: string;
  comment: string;
  resolution_type?: ReviewResolutionType;
  human_verdict?: CoverageStatus | null;
}
export interface AuditProgress {
  run_id: string;
  project_id: string;
  model: string | null;
  status: "queued" | "running" | "cancelling" | "cancelled" | "complete" | "failed" | "interrupted";
  stage: string;
  completed: number;
  total: number;
  message: string;
  started_at: string;
  updated_at: string;
  error: string | null;
  events: { stage: string; completed: number; total: number; message: string; at: string }[];
}

export interface ApiRequirement {
  id: string;
  project_id: string;
  req_code: string;
  title: string;
  description: string | null;
  category:
    | "Electrical"
    | "Safety"
    | "Environmental"
    | "Mechanical"
    | "Cybersecurity"
    | "Documentation"
    | string;
  source_document: string | null;
  sources_count: number;
  coverage_status: CoverageStatus;
  confidence: number;
  review_state: "Reviewed" | "Needs review" | "Open" | "Approved" | "Rejected";
  severity: "Critical" | "High" | "Medium" | "Low";
  ai_analysis: string | null;
  ai_recommendation: string | null;
  evidence: ApiEvidenceItem[];
  created_at: string;
  updated_at: string;
  contract?: {
    conditions?: AtomicCondition[];
    logic?: {
      operator: string;
      condition_ids?: string[];
      if_condition_id?: string;
      then_condition_ids?: string[];
    };
    contract_complete?: boolean;
    unmapped_obligations?: string[];
    clause_coverage?: Record<string, unknown>[];
    validation_issues?: string[];
    ambiguities?: string[];
    logic_tree?: Record<string, unknown>;
  };
  condition_results?: ConditionResult[];
  diagnostics?: {
    review_gate?: { required: boolean; auto_close_eligible: boolean; reasons: string[] };
    model?: string;
    secondary_adjudication_resolved_ids?: string[];
    evidence_catalog?: {
      evidence_id: string;
      chunk_id: string;
      document_id: string;
      document_name: string;
      page_number: number | null;
    }[];
    [key: string]: unknown;
  };
  source_document_id?: string | null;
  source_blocks?: DocumentBlock[];
  review_history?: ReviewEvent[];
  human_verdict?: CoverageStatus | null;
  human_assessment?: ReviewEvent | null;
  contract_complete?: boolean | null;
  validation_issue_count?: number;
  unresolved_condition_count?: number;
  review_blocker_count?: number;
  assessment_run_id?: string | null;
  assessed_at?: string | null;
  source_sync_status?: string | null;
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
    | "Ambiguous requirement"
    | "Inconclusive evidence";
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
  const url = `${getApiBaseUrl()}${path}`;
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
  inspectDocument: (projectId: string, docId: string, page = 1) =>
    request<DocumentInspection>(
      `/projects/${projectId}/documents/${docId}/inspection?page=${page}`,
    ),
  documentUrl: (projectId: string, docId: string) =>
    `${getApiBaseUrl()}/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(docId)}/file`,
  pageUrl: (projectId: string, docId: string, page: number) =>
    `${getApiBaseUrl()}/projects/${encodeURIComponent(projectId)}/documents/${encodeURIComponent(docId)}/pages/${page}.png`,
  saveReview: (projectId: string, reqId: string, data: ReviewRequest) =>
    request<ReviewEvent>(`/projects/${projectId}/requirements/${reqId}/reviews`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getAuditProgress: (projectId: string) =>
    request<AuditProgress | null>(`/projects/${projectId}/audit`),
  // Projects
  getProjects: () => request<ApiProject[]>("/projects"),
  getProject: (id: string) => request<ApiProject>(`/projects/${id}`),
  getProjectStats: (id: string) => request<ApiProjectStats>(`/projects/${id}/stats`),
  createProject: (data: {
    name: string;
    product_name: string;
    product_category?: string;
    company?: string;
    description?: string;
  }) =>
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
  getDocument: (projectId: string, docId: string) =>
    request<ApiDocument>(`/projects/${projectId}/documents/${docId}`),
  uploadDocument: (projectId: string, file: File, docType = "", version = "v1.0") => {
    const formData = new FormData();
    formData.append("file", file);
    if (docType) formData.append("doc_type", docType);
    if (version) formData.append("version", version);
    return request<{
      id: string;
      filename: string;
      original_filename: string;
      doc_type: string;
      processing_status: string;
    }>(`/projects/${projectId}/documents`, {
      method: "POST",
      body: formData,
    });
  },
  deleteDocument: (projectId: string, docId: string) =>
    request<void>(`/projects/${projectId}/documents/${docId}`, {
      method: "DELETE",
    }),

  // Requirements
  getRequirements: (
    projectId: string,
    filters?: { category?: string; status?: string; severity?: string; review?: string },
  ) => {
    const params = new URLSearchParams();
    if (filters?.category && filters.category !== "all")
      params.append("category", filters.category);
    if (filters?.status && filters.status !== "All") params.append("status", filters.status);
    if (filters?.severity && filters.severity !== "all")
      params.append("severity", filters.severity);
    if (filters?.review && filters.review !== "all") params.append("review", filters.review);

    const query = params.toString() ? `?${params.toString()}` : "";
    return request<ApiRequirement[]>(`/projects/${projectId}/requirements${query}`);
  },
  getRequirement: (projectId: string, reqId: string) =>
    request<ApiRequirement>(`/projects/${projectId}/requirements/${reqId}`),
  createRequirement: (
    projectId: string,
    data: { req_code: string; title: string; category?: string; severity?: string },
  ) =>
    request<ApiRequirement>(`/projects/${projectId}/requirements`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Findings
  getFindings: (
    projectId: string,
    filters?: {
      severity?: string;
      finding_type?: string;
      review_state?: string;
      category?: string;
    },
  ) => {
    const params = new URLSearchParams();
    if (filters?.severity && filters.severity !== "all")
      params.append("severity", filters.severity);
    if (filters?.finding_type && filters.finding_type !== "all")
      params.append("finding_type", filters.finding_type);
    if (filters?.review_state && filters.review_state !== "all")
      params.append("review_state", filters.review_state);
    if (filters?.category && filters.category !== "all")
      params.append("category", filters.category);

    const query = params.toString() ? `?${params.toString()}` : "";
    return request<ApiFinding[]>(`/projects/${projectId}/findings${query}`);
  },
  updateFinding: (
    projectId: string,
    findingId: string,
    data: { review_state?: string; assigned_to?: string; severity?: string },
  ) =>
    request<ApiFinding>(`/projects/${projectId}/findings/${findingId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  // Audit Pipeline
  triggerAudit: (projectId: string, options?: { model?: string; thinking_level?: string }) =>
    request<AuditProgress>(`/projects/${projectId}/audit`, {
      method: "POST",
      body: JSON.stringify({
        model: options?.model || undefined,
        thinking_level: options?.thinking_level || undefined,
      }),
    }),
  cancelAudit: (projectId: string) =>
    request<AuditProgress>(`/projects/${projectId}/audit/cancel`, { method: "POST" }),

  // AI Configuration Settings
  getAiSettings: () => request<ApiAiSettings>("/settings/ai"),
  updateAiSettings: (data: { model?: string; thinking_level?: string }) =>
    request<ApiAiSettings>("/settings/ai", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // Current Authenticated User / Databricks SSO Identity
  getCurrentUser: () => request<ApiCurrentUser>("/me"),

  // Visitor Lead Registration & Management
  registerVisitor: (data: RegisterVisitorRequest) =>
    request<VisitorRegisterResponse>("/visitors", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getVisitors: (search?: string) => {
    const query = search ? `?search=${encodeURIComponent(search)}` : "";
    return request<VisitorListResponse>(`/visitors${query}`);
  },
  deleteVisitor: (id: string) =>
    request<{ status: string; message: string }>(`/visitors/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }),
  getVisitorsExportUrl: () => `${getApiBaseUrl()}/visitors/export`,
};
