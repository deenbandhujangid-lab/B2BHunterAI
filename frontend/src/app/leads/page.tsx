"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Download,
  ExternalLink,
  Filter,
  X,
} from "lucide-react";
import ConnectionStatus, { useApiError } from "@/components/ConnectionStatus";
import { api, Lead } from "@/lib/api";

const PAGE_SIZES = [25, 50, 100] as const;
const ROLES = ["Founder", "HR", "Admin", "CMO"] as const;

type DatePreset = "all" | "today" | "7d" | "30d" | "custom";

function localDateStr(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function presetRange(preset: DatePreset): { date_from?: string; date_to?: string } {
  if (preset === "all" || preset === "custom") return {};
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const end = localDateStr(today);
  if (preset === "today") return { date_from: end, date_to: end };
  const start = new Date(today);
  start.setDate(start.getDate() - (preset === "7d" ? 6 : 29));
  return { date_from: localDateStr(start), date_to: end };
}

function formatLeadDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString("en-IN", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return "—";
  }
}

function pageNumbers(current: number, total: number): (number | "...")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages: (number | "...")[] = [1];
  if (current > 3) pages.push("...");
  for (let p = Math.max(2, current - 1); p <= Math.min(total - 1, current + 1); p++) {
    pages.push(p);
  }
  if (current < total - 2) pages.push("...");
  pages.push(total);
  return pages;
}

export default function LeadsPage() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<(typeof PAGE_SIZES)[number]>(25);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [datePreset, setDatePreset] = useState<DatePreset>("all");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [locationFilter, setLocationFilter] = useState("");
  const [industryFilter, setIndustryFilter] = useState("");
  const [companyFilter, setCompanyFilter] = useState("");
  const [emailFilter, setEmailFilter] = useState("");
  const [debouncedCompany, setDebouncedCompany] = useState("");
  const [debouncedEmail, setDebouncedEmail] = useState("");
  const [filterOptions, setFilterOptions] = useState<{ locations: string[]; industries: string[] }>({
    locations: [],
    industries: [],
  });

  useEffect(() => {
    const t = setTimeout(() => setDebouncedCompany(companyFilter.trim()), 400);
    return () => clearTimeout(t);
  }, [companyFilter]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedEmail(emailFilter.trim()), 400);
    return () => clearTimeout(t);
  }, [emailFilter]);

  useEffect(() => {
    api.getLeadFilterOptions().then(setFilterOptions).catch(() => {});
  }, []);

  const dateParams = useMemo(() => {
    if (datePreset === "custom") {
      const params: { date_from?: string; date_to?: string } = {};
      if (customFrom) params.date_from = customFrom;
      if (customTo) params.date_to = customTo;
      return params;
    }
    return presetRange(datePreset);
  }, [datePreset, customFrom, customTo]);

  const hasActiveFilters =
    datePreset !== "all" ||
    !!roleFilter ||
    !!locationFilter ||
    !!industryFilter ||
    !!debouncedCompany ||
    !!debouncedEmail ||
    !!customFrom ||
    !!customTo;

  const filterParams = useMemo(
    () => ({
      ...dateParams,
      ...(roleFilter ? { role: roleFilter } : {}),
      ...(locationFilter ? { location: locationFilter } : {}),
      ...(industryFilter ? { industry: industryFilter } : {}),
      ...(debouncedCompany ? { company: debouncedCompany } : {}),
      ...(debouncedEmail ? { email: debouncedEmail } : {}),
    }),
    [dateParams, roleFilter, locationFilter, industryFilter, debouncedCompany, debouncedEmail]
  );

  const fetchLeads = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .getLeads({
        page,
        page_size: pageSize,
        ...filterParams,
      })
      .then((d) => {
        setLeads(d.leads);
        setTotal(d.total);
      })
      .catch((e) => setError(useApiError(e)))
      .finally(() => setLoading(false));
  }, [page, pageSize, filterParams]);

  useEffect(() => {
    fetchLeads();
  }, [fetchLeads]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const rangeEnd = Math.min(page * pageSize, total);

  const clearFilters = () => {
    setDatePreset("all");
    setCustomFrom("");
    setCustomTo("");
    setRoleFilter("");
    setLocationFilter("");
    setIndustryFilter("");
    setCompanyFilter("");
    setEmailFilter("");
    setDebouncedCompany("");
    setDebouncedEmail("");
    setPage(1);
  };

  const handleExport = () => {
    api.exportLeads(filterParams);
  };

  const handleExportCompanies = () => {
    const params: Record<string, string> = {};
    if (locationFilter) params.location = locationFilter;
    if (industryFilter) params.industry = industryFilter;
    api.exportCompanies(params);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Leads</h1>
          <p className="text-sm text-gray-500 mt-1">
            {Number(total ?? 0).toLocaleString()} verified leads
            {hasActiveFilters ? " (filtered)" : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={handleExportCompanies}
            className="btn-secondary flex items-center gap-1.5 text-sm"
          >
            <Download className="w-4 h-4" /> Companies CSV
          </button>
          <button
            onClick={handleExport}
            className="btn-primary flex items-center gap-1.5 text-sm"
          >
            <Download className="w-4 h-4" /> Export Leads
          </button>
        </div>
      </div>

      <ConnectionStatus />

      <div className="card p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Filter className="w-4 h-4 text-gray-400 shrink-0" />
          <span className="text-sm font-medium text-gray-700">Filters</span>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={clearFilters}
              className="text-xs text-brand-600 hover:text-brand-800 flex items-center gap-1 ml-auto"
            >
              <X className="w-3 h-3" /> Clear all
            </button>
          )}
        </div>

        <div className="flex flex-wrap gap-3">
          <div className="min-w-[160px]">
            <label className="text-xs text-gray-500 block mb-1">Date</label>
            <div className="relative">
              <select
                value={datePreset}
                onChange={(e) => {
                  setDatePreset(e.target.value as DatePreset);
                  setPage(1);
                }}
                className="input-field appearance-none pr-8 py-2 text-sm cursor-pointer"
              >
                <option value="all">All time</option>
                <option value="today">Today</option>
                <option value="7d">Last 7 days</option>
                <option value="30d">Last 30 days</option>
                <option value="custom">Custom range</option>
              </select>
              <ChevronDown className="w-4 h-4 text-gray-400 absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          {datePreset === "custom" && (
            <>
              <div>
                <label className="text-xs text-gray-500 block mb-1">From</label>
                <input
                  type="date"
                  value={customFrom}
                  onChange={(e) => {
                    setCustomFrom(e.target.value);
                    setPage(1);
                  }}
                  className="input-field py-2 text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-gray-500 block mb-1">To</label>
                <input
                  type="date"
                  value={customTo}
                  onChange={(e) => {
                    setCustomTo(e.target.value);
                    setPage(1);
                  }}
                  className="input-field py-2 text-sm"
                />
              </div>
            </>
          )}

          <div className="min-w-[140px]">
            <label className="text-xs text-gray-500 block mb-1">Location</label>
            <div className="relative">
              <select
                value={locationFilter}
                onChange={(e) => {
                  setLocationFilter(e.target.value);
                  setPage(1);
                }}
                className="input-field appearance-none pr-8 py-2 text-sm cursor-pointer"
              >
                <option value="">All locations</option>
                {filterOptions.locations.map((loc) => (
                  <option key={loc} value={loc}>
                    {loc}
                  </option>
                ))}
              </select>
              <ChevronDown className="w-4 h-4 text-gray-400 absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          <div className="min-w-[160px]">
            <label className="text-xs text-gray-500 block mb-1">Industry</label>
            <div className="relative">
              <select
                value={industryFilter}
                onChange={(e) => {
                  setIndustryFilter(e.target.value);
                  setPage(1);
                }}
                className="input-field appearance-none pr-8 py-2 text-sm cursor-pointer"
              >
                <option value="">All industries</option>
                {filterOptions.industries.map((ind) => (
                  <option key={ind} value={ind}>
                    {ind}
                  </option>
                ))}
              </select>
              <ChevronDown className="w-4 h-4 text-gray-400 absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          <div className="min-w-[160px]">
            <label className="text-xs text-gray-500 block mb-1">Company</label>
            <input
              type="text"
              value={companyFilter}
              onChange={(e) => {
                setCompanyFilter(e.target.value);
                setPage(1);
              }}
              placeholder="Search company…"
              className="input-field py-2 text-sm"
            />
          </div>

          <div className="min-w-[180px]">
            <label className="text-xs text-gray-500 block mb-1">Email</label>
            <input
              type="text"
              value={emailFilter}
              onChange={(e) => {
                setEmailFilter(e.target.value);
                setPage(1);
              }}
              placeholder="Search email…"
              className="input-field py-2 text-sm"
            />
          </div>

          <div className="min-w-[140px]">
            <label className="text-xs text-gray-500 block mb-1">Role</label>
            <div className="relative">
              <select
                value={roleFilter}
                onChange={(e) => {
                  setRoleFilter(e.target.value);
                  setPage(1);
                }}
                className="input-field appearance-none pr-8 py-2 text-sm cursor-pointer"
              >
                <option value="">All roles</option>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
              <ChevronDown className="w-4 h-4 text-gray-400 absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>

          <div className="min-w-[120px]">
            <label className="text-xs text-gray-500 block mb-1">Per page</label>
            <div className="relative">
              <select
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value) as (typeof PAGE_SIZES)[number]);
                  setPage(1);
                }}
                className="input-field appearance-none pr-8 py-2 text-sm cursor-pointer"
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n} rows
                  </option>
                ))}
              </select>
              <ChevronDown className="w-4 h-4 text-gray-400 absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            </div>
          </div>
        </div>
      </div>

      {error && (
        <div className="p-3 bg-red-50 text-red-700 text-sm rounded-lg border border-red-200">
          {error}
        </div>
      )}

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b border-gray-100">
              <th className="px-4 py-3 font-medium">Date</th>
              <th className="px-4 py-3 font-medium">Location</th>
              <th className="px-4 py-3 font-medium">Industry</th>
              <th className="px-4 py-3 font-medium">Role</th>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Title</th>
              <th className="px-4 py-3 font-medium">Company</th>
              <th className="px-4 py-3 font-medium">Email</th>
              <th className="px-4 py-3 font-medium">Phone</th>
              <th className="px-4 py-3 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={10} className="px-4 py-10 text-center text-gray-400">
                  Loading...
                </td>
              </tr>
            ) : leads.length === 0 ? (
              <tr>
                <td colSpan={10} className="px-4 py-10 text-center text-gray-400">
                  {hasActiveFilters
                    ? "No leads match your filters"
                    : "No verified leads yet"}
                </td>
              </tr>
            ) : (
              leads.map((l) => (
                <tr key={l.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="px-4 py-2.5 text-gray-600 whitespace-nowrap">
                    {formatLeadDate(l.created_at)}
                  </td>
                  <td className="px-4 py-2.5">{l.target_location || "—"}</td>
                  <td className="px-4 py-2.5">{l.target_industry || "—"}</td>
                  <td className="px-4 py-2.5">{l.role_category || "—"}</td>
                  <td className="px-4 py-2.5 whitespace-nowrap">
                    {[l.first_name, l.last_name].filter(Boolean).join(" ") || "—"}
                  </td>
                  <td className="px-4 py-2.5">{l.job_title || "—"}</td>
                  <td className="px-4 py-2.5">{l.company_name || "—"}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{l.email}</td>
                  <td className="px-4 py-2.5 font-mono text-xs">{l.phone_e164 || "—"}</td>
                  <td className="px-4 py-2.5">
                    {l.source_url && (
                      <a
                        href={l.source_url}
                        target="_blank"
                        rel="noopener"
                        className="text-brand-600 hover:text-brand-800"
                      >
                        <ExternalLink className="w-3.5 h-3.5" />
                      </a>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>

        <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 border-t border-gray-100">
          <span className="text-xs text-gray-500">
            {total === 0
              ? "No results"
              : `Showing ${rangeStart.toLocaleString()}–${rangeEnd.toLocaleString()} of ${total.toLocaleString()}`}
          </span>

          {totalPages > 1 && (
            <div className="flex items-center gap-1">
              <button
                onClick={() => setPage(1)}
                disabled={page === 1 || loading}
                className="btn-secondary p-1.5 disabled:opacity-40"
                title="First page"
              >
                <ChevronsLeft className="w-4 h-4" />
              </button>
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1 || loading}
                className="btn-secondary p-1.5 disabled:opacity-40"
                title="Previous"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>

              <div className="flex items-center gap-0.5 mx-1">
                {pageNumbers(page, totalPages).map((p, i) =>
                  p === "..." ? (
                    <span key={`ellipsis-${i}`} className="px-2 text-gray-400 text-xs">
                      …
                    </span>
                  ) : (
                    <button
                      key={p}
                      onClick={() => setPage(p)}
                      disabled={loading}
                      className={`min-w-[2rem] h-8 rounded text-xs font-medium transition-colors ${
                        p === page
                          ? "bg-brand-600 text-white"
                          : "text-gray-600 hover:bg-gray-100"
                      }`}
                    >
                      {p}
                    </button>
                  )
                )}
              </div>

              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages || loading}
                className="btn-secondary p-1.5 disabled:opacity-40"
                title="Next"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
              <button
                onClick={() => setPage(totalPages)}
                disabled={page === totalPages || loading}
                className="btn-secondary p-1.5 disabled:opacity-40"
                title="Last page"
              >
                <ChevronsRight className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
