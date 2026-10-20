// One small fetch wrapper for the whole app.
// - The session lives in an HttpOnly cookie the browser sends automatically.
// - Every change (POST/PUT/DELETE) carries the CSRF token from /api/auth/me.

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

let csrfToken = "";
let onUnauthorised: () => void = () => {};

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export function setUnauthorisedHandler(handler: () => void) {
  onUnauthorised = handler;
}

function messageFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    // FastAPI validation errors: [{loc, msg}]
    if (Array.isArray(detail) && detail[0]?.msg) {
      const first = detail[0] as { loc?: unknown[]; msg: string };
      const field = first.loc?.slice(1).join(" › ");
      return field ? `${field}: ${first.msg}` : first.msg;
    }
  }
  return status >= 500 ? "The server had a problem. Please try again." : `Request failed (${status}).`;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const method = (rest.method ?? (json !== undefined ? "POST" : "GET")).toUpperCase();
  const response = await fetch(`/api${path}`, {
    ...rest,
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": csrfToken } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });

  if (response.status === 401 && path !== "/auth/login" && path !== "/auth/me") {
    onUnauthorised();
  }
  if (response.status === 204) return undefined as T;
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, messageFrom(body, response.status));
  return body as T;
}

export function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
