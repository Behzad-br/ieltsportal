/**
 * Canonical frontend identity helpers (Phase 2).
 * Production identity = auth_users.id from JWT /auth/me (cached in ielts-auth-user).
 */
import { clearAuthSession, getAccessToken, getAuthUser, mapAuthRoleToUi, type AuthUser } from '@/lib/auth-api';
import { isDemoToken } from '@/lib/demo-mode';

const BLOCKED = new Set(['local', 's1', 'demo-student', 'demo-student-1', 'demo-teacher', 'demo-teacher-1', 'teacher']);

export function isBlockedLegacyId(raw: string | null | undefined): boolean {
  const key = String(raw || '').trim().toLowerCase();
  if (!key) return true;
  if (BLOCKED.has(key)) return true;
  if (key.startsWith('demo-')) return true;
  return false;
}

/** Authenticated user from session cache (must be populated by login or /auth/me). */
export function getSessionUser(): AuthUser | null {
  const token = getAccessToken();
  if (!token || isDemoToken(token)) return null;
  return getAuthUser();
}

/**
 * Canonical student id for NEW skill/homework/mock writes.
 * Prefer JWT student id. Never invent local/s1.
 */
export function getCanonicalStudentId(): string {
  const user = getSessionUser();
  if (user?.role === 'student' && user.id && !isBlockedLegacyId(user.id)) {
    return user.id;
  }
  // Staff impersonation / practice-as: only accept UUID-like or non-blocked cached mirror.
  try {
    const raw = localStorage.getItem('ielts-current-student');
    if (raw) {
      const row = JSON.parse(raw) as { id?: string };
      if (row?.id && !isBlockedLegacyId(row.id)) return row.id;
    }
  } catch {
    /* ignore */
  }
  const fallback = localStorage.getItem('writing_student_id') || '';
  if (fallback && !isBlockedLegacyId(fallback)) return fallback;
  return '';
}

export function getCanonicalTeacherId(): string {
  const user = getSessionUser();
  if (user?.role === 'teacher' && user.id) return user.id;
  if (user && (user.role === 'super_admin' || user.role === 'branch_admin') && user.id) {
    const header = localStorage.getItem('ielts-current-teacher-id') || '';
    if (header && !isBlockedLegacyId(header)) return header;
    return user.id;
  }
  const raw = localStorage.getItem('ielts-current-teacher-id') || '';
  return isBlockedLegacyId(raw) ? '' : raw;
}

export function uiRoleFromAuthUser(user: AuthUser | null): 'student' | 'teacher' | 'admin' | 'branch-admin' | null {
  if (!user?.role) return null;
  return mapAuthRoleToUi(user.role);
}

export function homePathForRole(role: string): string {
  if (role === 'super_admin' || role === 'admin') return '/admin/dashboard';
  if (role === 'branch_admin' || role === 'branch-admin') return '/branch-admin/dashboard';
  if (role === 'teacher') return '/teacher/dashboard';
  return '/student/dashboard';
}

/** True if URL path segment role matches authenticated user.role. */
export function pathAllowedForUser(pathname: string, user: AuthUser | null): boolean {
  if (!user?.role) return false;
  const ui = mapAuthRoleToUi(user.role);
  if (pathname.startsWith('/admin')) return ui === 'admin';
  if (pathname.startsWith('/branch-admin')) return ui === 'branch-admin';
  if (pathname.startsWith('/teacher')) return ui === 'teacher';
  if (pathname.startsWith('/student')) return ui === 'student';
  return true;
}

export function forceLogout() {
  clearAuthSession();
  try {
    localStorage.removeItem('ielts-role');
    localStorage.removeItem('ielts-current-student');
    localStorage.removeItem('ielts-current-teacher-id');
    localStorage.removeItem('writing_student_id');
    localStorage.removeItem('ielts-branch-admin-id');
    localStorage.removeItem('ielts-branch-admin-username');
  } catch {
    /* ignore */
  }
}
