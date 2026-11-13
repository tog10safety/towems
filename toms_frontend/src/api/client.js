/**
 * TOWEMS API client.
 *
 * - Session cookie authentication (credentials: "include").
 * - CSRF: Django requires the token on unsafe methods; the SPA first calls
 *   /auth/csrf/ and echoes the cookie in X-CSRFToken.
 * - Errors are normalised to { message, status, fieldErrors } so forms can show
 *   per-field messages instead of a generic banner.
 */

const BASE = import.meta.env.VITE_API_BASE_URL || "/api";

/** Same base the request helper uses — handy for plain `<a href>` downloads. */
export const API_BASE = BASE;

export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
    this.fieldErrors = flattenErrors(payload?.errors);
  }
}

function flattenErrors(errors) {
  const out = {};
  if (!errors || typeof errors !== "object") return out;
  for (const [key, value] of Object.entries(errors)) {
    if (Array.isArray(value)) out[key] = value.join(" ");
    else if (typeof value === "string") out[key] = value;
    else if (value && typeof value === "object") out[key] = flattenErrors(value)[0] || JSON.stringify(value);
  }
  return out;
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[2]) : null;
}

let csrfReady = false;

export async function ensureCsrf() {
  await fetch(`${BASE}/auth/csrf/`, { credentials: "include" });
  csrfReady = true;
}

async function request(path, { method = "GET", body, raw = false } = {}) {
  const headers = {};
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  // Never set a JSON content type on a multipart body: the browser must add
  // the boundary itself, or Django cannot parse the upload.
  if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    if (!csrfReady) await ensureCsrf();
    const token = getCookie("csrftoken");
    if (token) headers["X-CSRFToken"] = token;
  }

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: "include",
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
  });

  if (raw) {
    if (!response.ok) throw new ApiError("Download failed", response.status, null);
    return response;
  }

  if (response.status === 204) return null;

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message = payload?.detail || payload?.message || `Request failed (${response.status})`;
    throw new ApiError(message, response.status, payload);
  }
  return payload;
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: "POST", body }),
  patch: (path, body) => request(path, { method: "PATCH", body }),
  put: (path, body) => request(path, { method: "PUT", body }),
  del: (path, body) => request(path, { method: "DELETE", body }),
  download: (path) => request(path, { raw: true }),
  /** Multipart upload (avatar, signature). Let the browser set the boundary. */
  upload: (path, formData, method = "POST") =>
    request(path, { method, body: formData }),
  ensureCsrf,
};

/** Build a query string, omitting empty values. */
export function qs(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export function pageFrom(results) {
  return {
    items: results?.results ?? [],
    count: results?.count ?? 0,
    next: results?.next ?? null,
    previous: results?.previous ?? null,
  };
}
