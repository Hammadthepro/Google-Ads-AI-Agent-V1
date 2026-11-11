/** The browser only follows an authorization URL that is exactly what Google requires; otherwise we show an error instead of sending a broken request. */
export const REQUIRED_AUTH_PARAMS = ["client_id", "redirect_uri", "response_type", "scope", "state", "access_type"] as const;

export function checkAuthorizationUrl(raw: unknown): { ok: true; url: string } | { ok: false; reason: string } {
  if (typeof raw !== "string" || !raw) return { ok: false, reason: "The server returned no authorization URL." };
  let u: URL;
  try { u = new URL(raw); } catch { return { ok: false, reason: "The authorization URL is not a valid URL." }; }
  if (u.origin !== "https://accounts.google.com" || u.pathname !== "/o/oauth2/v2/auth") return { ok: false, reason: "The authorization URL does not point at Google's sign-in page." };
  const missing = REQUIRED_AUTH_PARAMS.filter((k) => !u.searchParams.get(k));
  if (missing.length) return { ok: false, reason: `The authorization URL is missing: ${missing.join(", ")}.` };
  if (u.searchParams.get("response_type") !== "code") return { ok: false, reason: "response_type must be code." };
  return { ok: true, url: raw }; // returned untouched: the exact string the server built
}
