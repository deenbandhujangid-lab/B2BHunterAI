"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  CheckCircle2,
  Circle,
  Loader2,
  Search,
  XCircle,
} from "lucide-react";
import ConnectionStatus, { useApiError } from "@/components/ConnectionStatus";
import { api, CompanyQueueItem, CompanyQueueList } from "@/lib/api";

const PAGE_SIZE = 50;

type StatusFilter = "all" | "pending" | "harvesting" | "done" | "failed" | "scraped";

function StatusIcon({ status }: { status: string }) {
  if (status === "done") {
    return (
      <span title="Scraped">
        <CheckCircle2 className="w-4 h-4 text-green-600 shrink-0" />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span title="Tried — failed / no email">
        <XCircle className="w-4 h-4 text-amber-600 shrink-0" />
      </span>
    );
  }
  if (status === "harvesting") {
    return (
      <span title="Scraping now">
        <Loader2 className="w-4 h-4 text-blue-600 animate-spin shrink-0" />
      </span>
    );
  }
  return (
    <span title="Pending scrape">
      <Circle className="w-4 h-4 text-gray-400 shrink-0" />
    </span>
  );
}

function statusLabel(status: string) {
  if (status === "done") return "Scraped";
  if (status === "failed") return "Failed / no email";
  if (status === "harvesting") return "Scraping…";
  return "Pending";
}

export default function JobCompaniesPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const jobId = Number(params.id);
  const [data, setData] = useState<CompanyQueueList | null>(null);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<StatusFilter>(
    (searchParams.get("status") as StatusFilter) || "all"
  );
  const [q, setQ] = useState("");
  const [qInput, setQInput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!jobId || Number.isNaN(jobId)) return;
    setLoading(true);
    try {
      const res = await api.getJobCompanies(jobId, {
        page,
        page_size: PAGE_SIZE,
        status: status === "all" ? "" : status,
        q,
      });
      setData(res);
      setError("");
    } catch (e) {
      setError(useApiError(e));
    } finally {
      setLoading(false);
    }
  }, [jobId, page, status, q]);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="max-w-5xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <Link
            href="/"
            className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 mb-2"
          >
            <ArrowLeft className="w-4 h-4" /> Dashboard
          </Link>
          <h1 className="text-2xl font-bold text-gray-900">
            {data?.job_name || `Job #${jobId}`} — Companies
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {data?.location} · {data?.industry}
          </p>
        </div>
        {data && (
          <div className="flex flex-wrap gap-2 text-xs">
            <span className="px-2 py-1 rounded bg-gray-100 text-gray-700">
              Total {data.pending + data.harvesting + data.done + data.failed}
            </span>
            <span className="px-2 py-1 rounded bg-amber-50 text-amber-800">
              Pending {data.pending}
            </span>
            <span className="px-2 py-1 rounded bg-blue-50 text-blue-800">
              Scraping {data.harvesting}
            </span>
            <span className="px-2 py-1 rounded bg-green-50 text-green-800">
              Scraped {data.done}
            </span>
            <span className="px-2 py-1 rounded bg-orange-50 text-orange-800">
              Failed {data.failed}
            </span>
          </div>
        )}
      </div>

      <ConnectionStatus />
      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {(
          [
            ["all", "All"],
            ["pending", "Pending"],
            ["harvesting", "Scraping"],
            ["done", "Scraped ✓"],
            ["failed", "Failed"],
            ["scraped", "Done + Failed"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              setStatus(key);
              setPage(1);
            }}
            className={`text-xs px-2.5 py-1.5 rounded-md border ${
              status === key
                ? "bg-gray-900 text-white border-gray-900"
                : "bg-white text-gray-600 border-gray-200 hover:bg-gray-50"
            }`}
          >
            {label}
          </button>
        ))}
        <form
          className="flex items-center gap-1 ml-auto"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(qInput.trim());
            setPage(1);
          }}
        >
          <div className="relative">
            <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Search name / domain"
              className="input-field text-sm pl-7 py-1.5 w-48"
            />
          </div>
          <button type="submit" className="btn-secondary text-xs py-1.5 px-2">
            Search
          </button>
        </form>
      </div>

      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b border-gray-100 bg-gray-50/80">
              <th className="px-4 py-3 font-medium w-10">✓</th>
              <th className="px-4 py-3 font-medium">Company</th>
              <th className="px-4 py-3 font-medium">Size</th>
              <th className="px-4 py-3 font-medium">Domain</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Source</th>
            </tr>
          </thead>
          <tbody>
            {loading && !data ? (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-gray-400">
                  Loading…
                </td>
              </tr>
            ) : !data || data.companies.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-gray-400">
                  No companies yet. Use Load More Companies on the dashboard.
                </td>
              </tr>
            ) : (
              data.companies.map((c: CompanyQueueItem) => (
                <tr key={c.id} className="border-b border-gray-50 hover:bg-gray-50">
                  <td className="px-4 py-2.5">
                    <StatusIcon status={c.status} />
                  </td>
                  <td className="px-4 py-2.5 font-medium text-gray-900">
                    {c.company_name || c.domain}
                  </td>
                  <td className="px-4 py-2.5">
                    {c.size_label ? (
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          c.size_label === "Startup"
                            ? "bg-violet-100 text-violet-800"
                            : c.size_label === "Small"
                            ? "bg-sky-100 text-sky-800"
                            : c.size_label === "Mid"
                            ? "bg-teal-100 text-teal-800"
                            : c.size_label === "Large"
                            ? "bg-orange-100 text-orange-800"
                            : "bg-gray-100 text-gray-700"
                        }`}
                      >
                        {c.size_label}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <a
                      href={c.website || `https://${c.domain}`}
                      target="_blank"
                      rel="noreferrer"
                      className="text-brand-600 hover:underline"
                    >
                      {c.domain}
                    </a>
                    {c.last_error && c.status === "failed" && (
                      <div className="text-xs text-amber-700 truncate max-w-[240px]" title={c.last_error}>
                        {c.last_error}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${
                        c.status === "done"
                          ? "bg-green-100 text-green-800"
                          : c.status === "failed"
                          ? "bg-amber-100 text-amber-800"
                          : c.status === "harvesting"
                          ? "bg-blue-100 text-blue-800"
                          : "bg-gray-100 text-gray-700"
                      }`}
                    >
                      {statusLabel(c.status)}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-gray-500 text-xs">{c.source}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {data && data.total > 0 && (
        <div className="flex items-center justify-between text-sm text-gray-600">
          <span>
            Page {page} / {totalPages} · {data.total} shown filter
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              className="btn-secondary text-xs py-1 px-2 disabled:opacity-40"
            >
              Prev
            </button>
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
              className="btn-secondary text-xs py-1 px-2 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
