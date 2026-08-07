"use client";

import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Wifi, WifiOff } from "lucide-react";
import { api, API_URL, ApiError, BACKEND_START_HINT } from "@/lib/api";

export default function ConnectionStatus() {
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    const check = () =>
      api.health()
        .then(() => setOnline(true))
        .catch(() => setOnline(false));
    check();
    const id = setInterval(check, 15000);
    return () => clearInterval(id);
  }, []);

  if (online === null) return null;

  return (
    <div
      className={`flex items-center gap-2 px-4 py-3 rounded-lg text-sm ${
        online
          ? "bg-green-50 text-green-800 border border-green-200"
          : "bg-red-50 text-red-800 border border-red-200"
      }`}
    >
      {online ? (
        <>
          <Wifi className="w-4 h-4" />
          <span>
            Backend connected at <code className="font-mono text-xs">{API_URL}</code>
          </span>
          <CheckCircle2 className="w-4 h-4 ml-auto" />
        </>
      ) : (
        <>
          <WifiOff className="w-4 h-4" />
          <div>
            <p className="font-medium">Connection failed — backend not reachable</p>
            <p className="text-xs mt-0.5 opacity-90">
              <strong>WinError 10013?</strong> Port 8002 pe pehle se backend chal raha hai.
            </p>
            <p className="text-xs mt-1 opacity-80">
              Fix: <code>stop-backend.bat</code> → phir <code>start-backend.bat</code>
              <br />
              {BACKEND_START_HINT}
            </p>
          </div>
          <AlertCircle className="w-4 h-4 ml-auto shrink-0" />
        </>
      )}
    </div>
  );
}

export function useApiError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "Unknown error";
}
