const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8002";

export const BACKEND_START_HINT =
  "Port 8002 busy ho to stop-backend.bat chalao, phir start-backend.bat. Windows pe --reload mat use karo.";

export class ApiError extends Error {
  constructor(message: string, public status?: number) {
    super(message);
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = path.includes("/pause") ? 15_000 : 90_000;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json", ...options?.headers },
      ...options,
      signal: controller.signal,
    });
  } catch (e) {
    if (e instanceof Error && e.name === "AbortError") {
      throw new ApiError(
        `Backend slow or busy at ${API_BASE} (timeout ${timeoutMs / 1000}s). Scraper chal raha ho to thoda wait karo ya stop-backend.bat → start-backend.bat. ${BACKEND_START_HINT}`
      );
    }
    throw new ApiError(
      `Cannot connect to backend at ${API_BASE}. ${BACKEND_START_HINT}`
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    const err = await res.text();
    let msg = err || `API error ${res.status}`;
    try {
      const parsed = JSON.parse(err);
      if (parsed.detail) msg = typeof parsed.detail === "string" ? parsed.detail : JSON.stringify(parsed.detail);
    } catch { /* keep raw */ }
    if (res.status === 404) {
      msg = `Not Found (${path}). Backend restart karo: start-backend.bat`;
    }
    throw new ApiError(msg, res.status);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export interface DashboardStats {
  total_extracted: number;
  active_jobs: number;
  total_jobs: number;
  paused_jobs: number;
}

export interface SearchJob {
  id: number;
  job_name: string;
  target_location: string;
  target_industry: string;
  target_role: string;
  daily_target: number;
  status: string;
  total_found: number;
  restart_count: number;
  last_error: string | null;
  activity_message: string | null;
  last_activity_at: string | null;
  created_at: string;
  companies_total?: number;
  companies_pending?: number;
  companies_done?: number;
  queries_total?: number;
  queries_pending?: number;
  queries_completed?: number;
  listing?: boolean;
  harvesting?: boolean;
}

export interface CompanyQueueItem {
  id: number;
  domain: string;
  company_name: string | null;
  website: string | null;
  location: string | null;
  industry: string | null;
  source: string;
  size_label?: string | null;
  status: string;
  last_error: string | null;
  created_at: string | null;
  checked_at: string | null;
  scraped: boolean;
}

export interface CompanyQueueList {
  companies: CompanyQueueItem[];
  total: number;
  page: number;
  page_size: number;
  pending: number;
  harvesting: number;
  done: number;
  failed: number;
  job_id: number;
  job_name: string;
  location: string;
  industry: string;
}

export interface Lead {
  id: number;
  job_id: number;
  first_name: string | null;
  last_name: string | null;
  job_title: string | null;
  role_category: string | null;
  company_name: string | null;
  domain: string | null;
  email: string;
  email_status: string;
  phone_raw: string | null;
  phone_e164: string | null;
  is_phone_valid: boolean;
  source_url: string | null;
  target_location: string | null;
  target_industry: string | null;
  created_at: string;
}

export const api = {
  health: () => request<{ status: string; port: number }>("/health"),

  getStats: () => request<DashboardStats>("/api/stats"),

  getJobs: () => request<{ jobs: SearchJob[]; total: number }>("/api/jobs"),

  createJob: (data: {
    target_location: string;
    target_industry: string;
    target_role?: string;
    target_roles?: string[];
    daily_target?: number;
  }) =>
    request<SearchJob>("/api/jobs", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  pauseJob: (id: number) =>
    request<SearchJob>(`/api/jobs/${id}/pause`, { method: "POST" }),

  startJob: (id: number) =>
    request<SearchJob>(`/api/jobs/${id}/start`, { method: "POST" }),

  restartJob: (id: number) =>
    request<SearchJob>(`/api/jobs/${id}/restart`, { method: "POST" }),

  loadCompanies: (id: number) =>
    request<SearchJob>(`/api/jobs/${id}/load-companies`, { method: "POST" }),

  getJobCompanies: (
    id: number,
    params: Record<string, string | number> = {}
  ) => {
    const filtered = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== "" && v !== undefined)
    );
    const qs = new URLSearchParams(
      Object.entries(filtered).map(([k, v]) => [k, String(v)])
    ).toString();
    return request<CompanyQueueList>(
      `/api/jobs/${id}/companies${qs ? `?${qs}` : ""}`
    );
  },

  getLeads: (params: Record<string, string | number> = {}) => {
    const filtered = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== "" && v !== undefined)
    );
    const qs = new URLSearchParams(
      Object.entries(filtered).map(([k, v]) => [k, String(v)])
    ).toString();
    return request<{ leads: Lead[]; total: number; page: number; page_size: number }>(
      `/api/leads${qs ? `?${qs}` : ""}`
    );
  },

  getLeadFilterOptions: () =>
    request<{ locations: string[]; industries: string[] }>("/api/leads/filter-options"),

  exportLeads: async (params: Record<string, string | number> = {}) => {
    const filtered = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== "" && v !== undefined)
    );
    const qs = new URLSearchParams(
      Object.entries(filtered).map(([k, v]) => [k, String(v)])
    ).toString();
    const res = await fetch(`${API_BASE}/api/leads/export${qs ? `?${qs}` : ""}`);
    if (!res.ok) throw new ApiError("Export failed", res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "leads_export.csv";
    a.click();
    URL.revokeObjectURL(url);
  },

  exportCompanies: async (params: Record<string, string | number> = {}) => {
    const filtered = Object.fromEntries(
      Object.entries(params).filter(([, v]) => v !== "" && v !== undefined)
    );
    const qs = new URLSearchParams(
      Object.entries(filtered).map(([k, v]) => [k, String(v)])
    ).toString();
    const res = await fetch(`${API_BASE}/api/companies/export${qs ? `?${qs}` : ""}`);
    if (!res.ok) throw new ApiError("Companies export failed", res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "companies_export.csv";
    a.click();
    URL.revokeObjectURL(url);
  },
};

export const API_URL = API_BASE;
