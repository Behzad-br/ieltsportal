/** Shared authenticated fetch for skill APIs. */
import { authHeaders } from '@/lib/auth-api';
import { getCanonicalStudentId, getCanonicalTeacherId } from '@/lib/identity';

export function studentAuthHeaders(): Record<string, string> {
  const id = getCanonicalStudentId();
  return {
    ...authHeaders(),
    ...(id ? { 'X-Student-Id': id } : {}),
  };
}

export function teacherAuthHeaders(): Record<string, string> {
  const teacherId = getCanonicalTeacherId();
  return {
    ...authHeaders(),
    ...(teacherId ? { 'X-Teacher-Id': teacherId } : {}),
  };
}
