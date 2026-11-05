/** Shared FastAPI error detail → human message (avoids `[object Object]`). */

export function formatApiDetail(detail: unknown, fallback = 'Request failed'): string {
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item;
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg);
        }
        return '';
      })
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  if (detail && typeof detail === 'object') {
    try {
      return JSON.stringify(detail);
    } catch {
      /* ignore */
    }
  }
  return fallback;
}

/** Prefer status-aware UX; keep feature-specific detail when safe (<500). */
export function userMessageForStatus(status: number, detail?: string): string {
  const clean = (detail || '').trim();
  if (clean && status >= 400 && status < 500 && status !== 401) {
    return clean;
  }
  switch (status) {
    case 401:
      return 'Your session expired. Please sign in again.';
    case 403:
      return clean || 'You do not have permission to do that.';
    case 404:
      return clean || 'That resource is unavailable.';
    case 409:
      return clean || 'This action conflicts with the current state.';
    case 413:
      return clean || 'Upload is too large.';
    case 422:
      return clean || 'Please check your input and try again.';
    case 429:
      return clean || 'Too many requests. Please try again later.';
    default:
      if (status >= 500 || status === 0) {
        return 'Temporary server problem. Please try again.';
      }
      return clean || `Request failed (${status})`;
  }
}

export function messageFromErrorBody(body: unknown, status: number): string {
  const detail =
    body && typeof body === 'object' && 'detail' in body
      ? (body as { detail: unknown }).detail
      : undefined;
  const formatted = formatApiDetail(detail, '');
  return userMessageForStatus(status, formatted || undefined);
}

/** Safe JSON parse for empty bodies, HTML 502 pages, and truncated proxies. */
export async function parseJsonBody(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch {
    const snippet = text.replace(/\s+/g, ' ').slice(0, 160);
    return { detail: snippet || `HTTP ${res.status}` };
  }
}

export class NetworkError extends Error {
  readonly kind = 'network' as const;
  constructor(message = 'Cannot connect to server. Make sure the backend is running.') {
    super(message);
    this.name = 'NetworkError';
  }
}

export class AuthSessionError extends Error {
  readonly kind: 'unauthorized' | 'network' | 'invalid';
  constructor(message: string, kind: 'unauthorized' | 'network' | 'invalid') {
    super(message);
    this.name = 'AuthSessionError';
    this.kind = kind;
  }
}
