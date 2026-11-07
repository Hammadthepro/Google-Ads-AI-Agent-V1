// Thin fetch wrapper: same-origin cookies, CSRF double-submit header, typed errors.
export class ApiError extends Error {
  status: number;
  code: string;
  details: Record<string, unknown>;
  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|;\s*)gaa_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = data?.error ?? {};
    throw new ApiError(res.status, err.code ?? "error", err.message ?? res.statusText, err.details ?? {});
  }
  return data as T;
}

export const api = {
  get: <T = any>(p: string) => request<T>("GET", p),
  post: <T = any>(p: string, b: unknown = {}) => request<T>("POST", p, b),
  patch: <T = any>(p: string, b: unknown = {}) => request<T>("PATCH", p, b),
  put: <T = any>(p: string, b: unknown = {}) => request<T>("PUT", p, b),
  del: <T = any>(p: string) => request<T>("DELETE", p),
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    const reasons = (e.details?.reasons as string[] | undefined) ?? [];
    return reasons.length ? `${e.message}: ${reasons.join("; ")}` : e.message;
  }
  return e instanceof Error ? e.message : String(e);
}
