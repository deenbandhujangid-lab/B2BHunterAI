"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  Database,
  Building2,
  Eye,
  Loader2,
  Pause,
  Play,
  PlusCircle,
  RefreshCw,
  PauseCircle,
  ShieldAlert,
} from "lucide-react";
import ConnectionStatus, { useApiError } from "@/components/ConnectionStatus";
import { api, DashboardStats, SearchJob } from "@/lib/api";
import { isBlockNotice, formatTimeAgo, isActivityStale, isActivityVeryStale } from "@/lib/jobMessages";

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [jobs, setJobs] = useState<SearchJob[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [actionError, setActionError] = useState("");
  const [loadError, setLoadError] = useState("");

  const load = useCallback(async (showSpinner = false) => {
    if (showSpinner) setRefreshing(true);
    try {
      const [s, j] = await Promise.all([api.getStats(), api.getJobs()]);
      setStats(s);
      setJobs(j.jobs);
      setLoadError("");
    } catch (e) {
      setLoadError(useApiError(e));
    } finally {
      if (showSpinner) setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(() => load(), 3000);
    return () => clearInterval(id);
  }, [load]);

  const runAction = async (
    id: number,
    action: "pause" | "start" | "restart" | "load"
  ) => {
    setBusyId(id);
    setActionError("");
    try {
      if (action === "pause") await api.pauseJob(id);
      else if (action === "start") await api.startJob(id);
      else if (action === "load") await api.loadCompanies(id);
      else await api.restartJob(id);
      await load();
    } catch (e) {
      setActionError(useApiError(e));
    } finally {
      setBusyId(null);
    }
  };

  const cards = stats
    ? [
        {
          label: "Total Extracted",
          value: stats.total_extracted ?? 0,
          icon: Database,
          color: "text-blue-600 bg-blue-50",
        },
        {
          label: "Active Jobs",
          value: stats.active_jobs ?? 0,
          icon: Activity,
          color: "text-orange-600 bg-orange-50",
        },
        {
          label: "Paused Jobs",
          value: stats.paused_jobs ?? 0,
          icon: PauseCircle,
          color: "text-gray-600 bg-gray-100",
        },
      ]
    : [];

  const blockAlerts = jobs.filter(
    (j) => j.status === "RUNNING" && isBlockNotice(j.last_error)
  );

  const runningJobs = jobs.filter((j) => j.status === "RUNNING");
  const liveJobs = runningJobs.filter((j) => !isActivityVeryStale(j.last_activity_at));
  const staleJobs = runningJobs.filter((j) => isActivityVeryStale(j.last_activity_at));

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">Local jobs & lead stats</p>
        </div>
        <Link href="/jobs/new" className="btn-primary flex items-center gap-2">
          <PlusCircle className="w-4 h-4" /> New Scrape Job
        </Link>
      </div>

      <ConnectionStatus />

      {runningJobs.length > 0 && (
        <div className="p-4 bg-blue-50 text-blue-900 text-sm rounded-lg border border-blue-200 space-y-2">
          <div className="flex items-center gap-2 font-medium">
            <Loader2 className="w-4 h-4 shrink-0 animate-spin" />
            Scraping in progress — {runningJobs.length} job(s) active
            <span className="text-xs font-normal text-blue-700 ml-1">
              (auto-refresh every 3s)
            </span>
          </div>
          <p className="text-xs text-blue-800 pl-6">
            Stop = listing + scrape dono band. Start = email scrape. Load More = nayi companies list.
            Founder role me CEO emails bhi map hote hain.
          </p>
          {liveJobs.map((j) => (
            <p key={j.id} className="text-xs pl-6 flex flex-wrap gap-x-2">
              <span className="font-medium">{j.job_name}</span>
              <span className="text-blue-700">
                {j.activity_message || "Working…"} · {formatTimeAgo(j.last_activity_at)}
              </span>
              <span className="text-blue-600">({j.total_found} found)</span>
            </p>
          ))}
          {staleJobs.map((j) => (
            <p key={j.id} className="text-xs pl-6 text-amber-800">
              <span className="font-medium">{j.job_name}:</span> No update {formatTimeAgo(j.last_activity_at)} —
              slow site ya backend restart. Pause → Restart try karo.
            </p>
          ))}
        </div>
      )}

      {blockAlerts.length > 0 && (
        <div className="p-4 bg-amber-50 text-amber-900 text-sm rounded-lg border border-amber-300 space-y-2">
          <div className="flex items-center gap-2 font-medium">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            CAPTCHA / Block detected — job chalu hai, Wikipedia scrape continue ho raha hai
          </div>
          {blockAlerts.map((j) => (
            <p key={j.id} className="text-xs pl-6">
              <span className="font-medium">{j.job_name}:</span> {j.last_error}
            </p>
          ))}
        </div>
      )}

      {(actionError || loadError) && (
        <div className="p-3 bg-red-50 text-red-700 text-sm rounded-lg border border-red-200">
          {actionError || loadError}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {cards.length === 0
          ? [1, 2, 3].map((i) => (
              <div key={i} className="card p-6 animate-pulse">
                <div className="h-4 bg-gray-200 rounded w-1/2 mb-3" />
                <div className="h-8 bg-gray-200 rounded w-1/3" />
              </div>
            ))
          : cards.map((c) => (
              <div key={c.label} className="card p-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-gray-500">{c.label}</p>
                    <p className="text-3xl font-bold mt-1">{Number(c.value ?? 0).toLocaleString()}</p>
                  </div>
                  <div className={`p-3 rounded-lg ${c.color}`}>
                    <c.icon className="w-5 h-5" />
                  </div>
                </div>
              </div>
            ))}
      </div>

      <div className="card">
        <div className="p-5 border-b border-gray-100 flex items-center justify-between">
          <h2 className="font-semibold">All Jobs</h2>
          <button
            type="button"
            onClick={() => load(true)}
            disabled={refreshing}
            className="btn-secondary text-xs py-1.5 px-3 flex items-center gap-1"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b border-gray-100">
                <th className="px-4 py-3 font-medium">Job</th>
                <th className="px-4 py-3 font-medium">Location</th>
                <th className="px-4 py-3 font-medium">Industry</th>
                <th className="px-4 py-3 font-medium">Role</th>
                <th className="px-4 py-3 font-medium">Companies</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Leads</th>
                <th className="px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {jobs.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-gray-400">
                    No jobs yet.{" "}
                    <Link href="/jobs/new" className="text-brand-600 hover:underline">
                      Start a scraper
                    </Link>
                  </td>
                </tr>
              ) : (
                jobs.map((j) => {
                  const busy = busyId === j.id;
                  const restarts = j.restart_count ?? 0;
                  const isRunning = j.status === "RUNNING" || !!j.harvesting;
                  const isListing = !!j.listing;
                  const isCompleted = j.status === "COMPLETED";
                  const isIdle = j.status === "PAUSED" || isCompleted;
                  const pendingCos = j.companies_pending ?? 0;
                  const totalCos = j.companies_total ?? 0;
                  const doneCos = j.companies_done ?? 0;
                  const qPending = j.queries_pending ?? 0;
                  const qTotal = j.queries_total ?? 0;
                  const showStart = isIdle && !isListing && !j.harvesting && (pendingCos > 0 || qPending > 0);
                  const showRestart = restarts >= 1 && isIdle && !isListing && !j.harvesting && pendingCos > 0;
                  const canLoad = !isListing && qPending > 0;
                  const showStop =
                    (isRunning || isListing || !!j.harvesting) && !isCompleted;
                  return (
                    <tr key={j.id} className="border-b border-gray-50 hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <div className="font-medium flex items-center gap-1.5">
                          {(isRunning || isListing) && (
                            <span className="relative flex h-2 w-2">
                              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                              <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
                            </span>
                          )}
                          {j.job_name}
                        </div>
                        {(isRunning || isListing) && j.activity_message && (
                          <div className="text-xs text-blue-700 mt-0.5 max-w-[320px] truncate" title={j.activity_message}>
                            {j.activity_message}
                            <span className="text-blue-500 ml-1">· updated {formatTimeAgo(j.last_activity_at)}</span>
                          </div>
                        )}
                        {!isRunning && !isListing && j.activity_message && (
                          <div className="text-xs text-gray-500 mt-0.5 max-w-[320px] truncate" title={j.activity_message}>
                            {j.activity_message}
                          </div>
                        )}
                        {isRunning && isActivityStale(j.last_activity_at) && !isActivityVeryStale(j.last_activity_at) && (
                          <div className="text-xs text-amber-600 mt-0.5">Slow — checking site / anti-block pause</div>
                        )}
                        {j.last_error && isBlockNotice(j.last_error) && (
                          <div className="text-xs text-amber-700 mt-0.5 max-w-[220px] truncate font-medium" title={j.last_error}>
                            {j.last_error}
                          </div>
                        )}
                        {j.last_error && !isBlockNotice(j.last_error) && j.last_error !== "Paused by user" && (
                          <div
                            className="text-xs mt-0.5 max-w-[220px] truncate text-red-600"
                            title={j.last_error}
                          >
                            {j.last_error}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3">{j.target_location}</td>
                      <td className="px-4 py-3">{j.target_industry}</td>
                      <td className="px-4 py-3">{j.target_role}</td>
                      <td className="px-4 py-3">
                        <div className="text-sm font-medium" title="Listed companies for this location/industry">
                          {totalCos.toLocaleString()} listed
                        </div>
                        <div className="text-xs text-gray-500">
                          {pendingCos} pending scrape · {doneCos} done
                        </div>
                        <div className="text-xs text-gray-400">
                          Queries {((j.queries_completed ?? 0)).toLocaleString()}/{qTotal.toLocaleString()}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`badge ${
                            isCompleted
                              ? "bg-green-100 text-green-800"
                              : isListing && !j.harvesting
                              ? "bg-purple-100 text-purple-800"
                              : isRunning
                              ? "bg-blue-100 text-blue-800"
                              : "bg-amber-100 text-amber-800"
                          }`}
                        >
                          {isCompleted
                            ? "COMPLETED"
                            : isListing && j.harvesting
                            ? "LIST+HARVEST"
                            : isListing
                            ? "LOADING"
                            : j.harvesting
                            ? "HARVESTING"
                            : j.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-medium">{j.total_found}</td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <Link
                            href={`/jobs/${j.id}/companies`}
                            className="btn-secondary text-xs py-1 px-2 flex items-center gap-1"
                            title="View listed companies and scrape status"
                          >
                            <Eye className="w-3 h-3" /> View Companies
                          </Link>
                          <button
                            type="button"
                            disabled={busy || !canLoad}
                            onClick={() => runAction(j.id, "load")}
                            className="btn-primary text-xs py-1 px-2 flex items-center gap-1 disabled:opacity-40 disabled:cursor-not-allowed"
                            title={
                              canLoad
                                ? "List ~500 more new company domains"
                                : qPending <= 0
                                ? "No pending search queries left"
                                : "Company load already running"
                            }
                          >
                            <Building2 className={`w-3 h-3 ${isListing ? "animate-pulse" : ""}`} />
                            {isListing ? "Loading…" : "Load More Companies"}
                          </button>
                          {showStop && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => runAction(j.id, "pause")}
                              className="btn-secondary text-xs py-1 px-2 flex items-center gap-1"
                              title="Full stop — listing + email scrape"
                            >
                              <Pause className="w-3 h-3" /> Stop
                            </button>
                          )}
                          {showStart && !showRestart && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => runAction(j.id, "start")}
                              className="btn-secondary text-xs py-1 px-2 flex items-center gap-1"
                              title="Start email scrape for pending companies"
                            >
                              <Play className="w-3 h-3" /> Start
                            </button>
                          )}
                          {showRestart && (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => runAction(j.id, "restart")}
                              className="btn-secondary text-xs py-1 px-2 flex items-center gap-1"
                              title="Resume email harvest"
                            >
                              <RefreshCw className={`w-3 h-3 ${busy ? "animate-spin" : ""}`} />
                              Start
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
