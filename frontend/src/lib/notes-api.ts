import { getCurrentStudent } from '@/lib/mock-api';
import { authHeaders, clearAuthSession, getAuthUser } from '@/lib/auth-api';
import { apiBase } from '@/lib/api-base';
import { messageFromErrorBody, parseJsonBody } from '@/lib/api-errors';
import { getCanonicalStudentId } from '@/lib/identity';

function headers(extra?: Record<string, string>) {
  const user = getAuthUser();
  const me = getCurrentStudent();
  const id = getCanonicalStudentId() || (user?.role === 'student' ? user.id : '') || '';
  return {
    ...authHeaders(),
    ...(id ? { 'X-Student-Id': id } : {}),
    ...(me.batch || user?.batch ? { 'X-Student-Batch': me.batch || user?.batch || '' } : {}),
    ...(me.name || user?.full_name ? { 'X-Student-Name': me.name || user?.full_name || '' } : {}),
    ...extra,
  };
}

async function notes<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}${path}`, {
      ...options,
      headers: {
        ...headers(options?.body instanceof FormData ? undefined : options?.body ? { 'Content-Type': 'application/json' } : undefined),
        ...(options?.headers as Record<string, string> | undefined),
      },
    });
  } catch {
    throw new Error('Cannot connect to server. Make sure the backend is running.');
  }
  if (res.status === 401) clearAuthSession();
  if (!res.ok) {
    const body = await parseJsonBody(res);
    throw new Error(messageFromErrorBody(body, res.status));
  }
  if (res.status === 204) return {} as T;
  return (await parseJsonBody(res)) as T;
}

export type TeacherNote = {
  id: string;
  title: string;
  description: string;
  original_name: string;
  kind?: string;
  page_count: number;
  unlocked_batches: string[];
  unlocked_student_ids: string[];
  created_at?: string | null;
  updated_at?: string | null;
};

export type StudentNote = {
  id: string;
  title: string;
  description: string;
  kind?: string;
  page_count: number | null;
  unlocked: boolean;
  updated_at?: string | null;
};

export function listTeacherNotes() {
  return notes<{ notes: TeacherNote[] }>('/notes/teacher');
}

export function createTeacherNote(body: FormData) {
  return notes<TeacherNote>('/notes/teacher', { method: 'POST', body });
}

export function updateTeacherNote(id: string, body: FormData) {
  return notes<TeacherNote>(`/notes/teacher/${id}`, { method: 'PUT', body });
}

export function setNoteAccess(id: string, unlocked_batches: string[], unlocked_student_ids: string[]) {
  return notes<TeacherNote>(`/notes/teacher/${id}/access`, {
    method: 'PUT',
    body: JSON.stringify({ unlocked_batches, unlocked_student_ids }),
  });
}

export function deleteTeacherNote(id: string) {
  return notes(`/notes/teacher/${id}`, { method: 'DELETE' });
}

export async function teacherNoteFileUrl(id: string) {
  const res = await fetch(`${apiBase()}/notes/teacher/${id}/file`, { headers: headers() });
  if (!res.ok) throw new Error('Could not open this file.');
  return URL.createObjectURL(await res.blob());
}

export function listStudentNotes() {
  return notes<{ notes: StudentNote[] }>('/notes/student');
}

export function studentNoteMeta(id: string) {
  return notes<TeacherNote & { watermark?: string; unlocked: boolean }>(`/notes/student/${id}`);
}

export async function studentNotePageUrl(id: string, page: number) {
  const res = await fetch(`${apiBase()}/notes/student/${id}/page/${page}`, { headers: headers() });
  if (res.status === 401) clearAuthSession();
  if (!res.ok) {
    const body = await parseJsonBody(res);
    throw new Error(messageFromErrorBody(body, res.status) || 'This page is locked.');
  }
  return URL.createObjectURL(await res.blob());
}
