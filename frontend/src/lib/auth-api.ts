/** Production auth session + API helpers. */

import { apiBase } from '@/lib/api-base';
import {
  AuthSessionError,
  formatApiDetail,
  messageFromErrorBody,
  parseJsonBody,
} from '@/lib/api-errors';

export { AuthSessionError, formatApiDetail, messageFromErrorBody, parseJsonBody };

export type AuthRole = 'student' | 'teacher' | 'branch_admin' | 'super_admin';

export type AuthUser = {
  id: string;
  role: AuthRole | string;
  email: string;
  username: string;
  full_name: string;
  name?: string;
  branch_id?: string;
  branchId?: string;
  batch?: string;
  batches?: string;
  is_active?: boolean;
};

const TOKEN_KEY = 'ielts-access-token';
const USER_KEY = 'ielts-auth-user';

export function getAccessToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || '';
  } catch {
    return '';
  }
}

export function getAuthUser(): AuthUser | null {
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function setAuthSession(token: string, user: AuthUser) {
  if (!token || !user || typeof user !== 'object') {
    throw new Error('Cannot save session: missing access token or user.');
  }
  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearAuthSession() {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Normalize backend login JSON: { access_token, token_type, user }. */
function parseLoginPayload(body: unknown): { access_token: string; user: AuthUser } {
  const root = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const nested =
    root.data && typeof root.data === 'object' ? (root.data as Record<string, unknown>) : null;
  const source = nested && (nested.access_token || nested.user) ? nested : root;

  const access_token = String(source.access_token || source.accessToken || '').trim();
  const userRaw = source.user;
  if (!access_token) {
    throw new Error('Invalid authentication response: missing access_token.');
  }
  if (!userRaw || typeof userRaw !== 'object') {
    throw new Error('Invalid authentication response: missing user.');
  }
  const user = userRaw as AuthUser;
  if (!user.role) {
    throw new Error('Invalid authentication response: user.role is missing.');
  }
  return { access_token, user };
}

export async function apiLogin(username: string, password: string, role?: string) {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role }),
    });
  } catch {
    throw new Error('Cannot connect to server. Make sure the backend is running.');
  }
  const body = await parseJsonBody(res);
  if (!res.ok) {
    const detail = (body as { detail?: unknown })?.detail;
    throw new Error(formatApiDetail(detail, 'Login failed'));
  }
  const parsed = parseLoginPayload(body);
  setAuthSession(parsed.access_token, parsed.user);
  return parsed;
}

export async function apiMe() {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/auth/me`, { headers: { ...authHeaders() } });
  } catch {
    throw new AuthSessionError('Cannot reach server.', 'network');
  }
  if (res.status === 401 || res.status === 403) {
    clearAuthSession();
    throw new AuthSessionError('Session expired', 'unauthorized');
  }
  if (!res.ok) {
    // Transient 5xx / network-ish failures — keep cached session.
    throw new AuthSessionError(`Session check failed (${res.status})`, 'network');
  }
  const body = await parseJsonBody(res);
  const user = (body && typeof body === 'object' ? body : null) as AuthUser | null;
  if (!user || typeof user !== 'object' || !user.role) {
    clearAuthSession();
    throw new AuthSessionError('Invalid authentication response.', 'invalid');
  }
  sessionStorage.setItem(USER_KEY, JSON.stringify(user));
  return user;
}

export async function orgFetch<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}${path}`, {
      ...options,
      headers: {
        ...authHeaders(),
        ...(options?.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(options?.headers as Record<string, string> | undefined),
      },
    });
  } catch {
    throw new Error('Cannot connect to server. Make sure the backend is running.');
  }
  if (res.status === 401) {
    clearAuthSession();
  }
  if (!res.ok) {
    const body = await parseJsonBody(res);
    throw new Error(messageFromErrorBody(body, res.status));
  }
  if (res.status === 204) return {} as T;
  return (await parseJsonBody(res)) as T;
}

export function mapAuthRoleToUi(role: string): 'student' | 'teacher' | 'admin' | 'branch-admin' {
  if (role === 'super_admin') return 'admin';
  if (role === 'branch_admin') return 'branch-admin';
  if (role === 'teacher') return 'teacher';
  return 'student';
}
