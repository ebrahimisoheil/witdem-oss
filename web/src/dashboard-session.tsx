import { useEffect, useState } from "react";

export async function connectDashboardSession(token: string, organization: string) {
  if (!token.trim() || !organization.trim()) throw new Error("Enter an access token and organization.");
  const response = await fetch("/api/v1/meta", {headers: {
    Authorization: `Bearer ${token.trim()}`, "X-Witdem-Organization-ID": organization.trim(),
  }});
  if (!response.ok) throw new Error("Connection rejected. Check the token, organization and permissions.");
  window.sessionStorage.setItem("witdem:access-token", token.trim());
  window.sessionStorage.setItem("witdem:organization-id", organization.trim());
}

export function DashboardSession() {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [organization, setOrganization] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const required = () => setOpen(true);
    window.addEventListener("witdem:authentication-required", required);
    return () => window.removeEventListener("witdem:authentication-required", required);
  }, []);
  if (!open) return null;
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
    <form className="w-full max-w-lg space-y-4 rounded-xl bg-white p-8 shadow-xl" onSubmit={async event => {
      event.preventDefault(); setBusy(true); setError("");
      try { await connectDashboardSession(token, organization); setToken(""); window.location.reload(); }
      catch (failure) { setError(failure instanceof Error ? failure.message : "Connection failed."); }
      finally { setBusy(false); }
    }}>
      <h1 className="text-xl font-semibold">Connect to your dashboard</h1>
      <p className="text-sm text-gray-600">Use an access token and organization supplied by your operator. The token is kept in this tab’s session storage and sent only to this server.</p>
      <label className="block">Organization<input className="mt-1 block w-full rounded border p-2" required value={organization} onChange={event => setOrganization(event.target.value)} autoComplete="off" /></label>
      <label className="block">Access token<input className="mt-1 block w-full rounded border p-2" type="password" required value={token} onChange={event => setToken(event.target.value)} autoComplete="off" /></label>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <button className="rounded bg-[#6d4aff] px-4 py-2 text-white" disabled={busy}>{busy ? "Connecting…" : "Connect"}</button>
    </form>
  </div>;
}
