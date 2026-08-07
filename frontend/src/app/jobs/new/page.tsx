"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { MapPin, Building2, Target, Play, ChevronDown } from "lucide-react";
import ConnectionStatus, { useApiError } from "@/components/ConnectionStatus";
import { api } from "@/lib/api";

const LOCATIONS = [
  "Bangalore",
  "Mumbai",
  "Delhi",
  "Hyderabad",
  "Chennai",
  "Pune",
  "Gurgaon",
  "Noida",
  "Kolkata",
  "Ahmedabad",
];

const INDUSTRIES = [
  "Corporate",
  "Software / IT",
  "Startup",
  "Fintech",
  "E-commerce",
  "Healthcare / Healthtech",
  "Edtech",
  "Real Estate",
  "Manufacturing",
  "SaaS",
  "Logistics / Supply Chain",
  "BFSI / Banking",
  "Telecom",
  "Media / Entertainment",
  "Automobile",
  "Retail",
  "Consulting",
  "Foodtech / Hospitality",
  "Energy / Cleantech",
  "Pharma / Biotech",
];

const ROLES = ["Founder", "HR", "Admin", "CMO"] as const;

export default function NewJobPage() {
  const router = useRouter();
  const [location, setLocation] = useState("Bangalore");
  const [industry, setIndustry] = useState("Corporate");
  const [selectedRoles, setSelectedRoles] = useState<string[]>(["Founder"]);
  const [targetLimit, setTargetLimit] = useState(5000);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const toggleRole = (role: string) => {
    setSelectedRoles((prev) =>
      prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedRoles.length === 0) {
      setError("Select at least one role");
      return;
    }
    setLoading(true);
    setError("");

    try {
      await api.createJob({
        target_location: location,
        target_industry: industry,
        target_roles: selectedRoles,
        daily_target: targetLimit,
      });
      router.push("/");
    } catch (err) {
      setError(useApiError(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">New Scrape Job</h1>
        <p className="text-sm text-gray-500 mt-1">
          Create pe auto ~500 companies list + background email harvest
        </p>
      </div>

      <ConnectionStatus />

      <p className="text-sm text-blue-800 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2">
        <strong>Flow:</strong> Job create → turant ~500 nayi companies list → background me email harvest.
        Dashboard pe <strong>Stop</strong> = listing + scrape dono band; <strong>Start</strong> = email scrape; <strong>Load More Companies</strong> = +~500 nayi companies.
        Founder role CEO emails bhi cover karta hai.
      </p>

      <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-900 space-y-1">
        <p>
          <strong>Unique data:</strong> pehle se listed domains skip — Load More sirf nayi companies.
        </p>
        <p>
          <strong>Multi-role:</strong> Founder (incl. CEO) + HR + Admin + CMO ek hi job mein.
        </p>
        <p>
          <strong>No API block:</strong> Playwright alag process — /health hamesha fast.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="card p-6 space-y-5">
        <div>
          <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1.5">
            <MapPin className="w-4 h-4" /> Location
          </label>
          <div className="relative">
            <select
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              className="input-field appearance-none pr-10 cursor-pointer"
              required
            >
              {LOCATIONS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            <ChevronDown className="w-4 h-4 text-gray-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
          </div>
        </div>

        <div>
          <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1.5">
            <Building2 className="w-4 h-4" /> Industry Category
          </label>
          <div className="relative">
            <select
              value={industry}
              onChange={(e) => setIndustry(e.target.value)}
              className="input-field appearance-none pr-10 cursor-pointer"
              required
            >
              {INDUSTRIES.map((i) => (
                <option key={i} value={i}>
                  {i}
                </option>
              ))}
            </select>
            <ChevronDown className="w-4 h-4 text-gray-400 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none" />
          </div>
        </div>

        <div>
          <label className="text-sm font-medium text-gray-700 mb-2 block">Role Types</label>
          <div className="grid grid-cols-2 gap-2">
            {ROLES.map((role) => (
              <label
                key={role}
                className={`flex items-center gap-2 p-3 rounded-lg border cursor-pointer transition-colors ${
                  selectedRoles.includes(role)
                    ? "border-brand-500 bg-brand-50"
                    : "border-gray-200 hover:bg-gray-50"
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedRoles.includes(role)}
                  onChange={() => toggleRole(role)}
                  className="w-4 h-4 text-brand-600 rounded"
                />
                <span className="text-sm font-medium">{role}</span>
              </label>
            ))}
          </div>
        </div>

        <div>
          <label className="flex items-center gap-1.5 text-sm font-medium text-gray-700 mb-1.5">
            <Target className="w-4 h-4" /> Target Limit
          </label>
          <input
            type="number"
            value={targetLimit}
            onChange={(e) => setTargetLimit(Number(e.target.value))}
            min={100}
            max={5000}
            step={100}
            className="input-field"
          />
          <p className="text-xs text-gray-400 mt-1">
            Max 5000. Unique skip ke saath pehle se fetched data time waste nahi karega.
          </p>
        </div>

        {error && (
          <div className="p-3 bg-red-50 text-red-700 text-sm rounded-lg border border-red-200">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="btn-primary w-full flex items-center justify-center gap-2 py-3"
        >
          <Play className="w-4 h-4" />
          {loading ? "Creating..." : "Create Job"}
        </button>
      </form>
    </div>
  );
}
