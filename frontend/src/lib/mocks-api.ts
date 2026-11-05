import { getCurrentStudent, homeworkAliasesForStudent } from '@/lib/mock-api';
import { authHeaders, clearAuthSession } from '@/lib/auth-api';
import { apiBase } from '@/lib/api-base';
import { messageFromErrorBody, parseJsonBody } from '@/lib/api-errors';
import { DEMO_MODE } from '@/lib/demo-mode';
import { getCanonicalStudentId, getCanonicalTeacherId } from '@/lib/identity';
import {
  demoCreateMockAssignment,
  demoGetAttemptWork,
  demoHeartbeat,
  demoListLibrary,
  demoListMockStudents,
  demoListTeacherMocks,
  demoMockEvents,
  demoMockStudentAction,
  demoMonitor,
  demoPatchMockStudent,
  demoReviewAttempt,
  demoSaveAnswers,
  demoStartAttempt,
  demoStudentDetail,
  demoStudentInbox,
  demoSubmitAttempt,
  ensureDemoAssignedMocks,
} from '@/lib/demo-mocks-store';

function studentHeaders(): Record<string, string> {
  const id = getCanonicalStudentId();
  // Staging: backend ignores client aliases for JWT students; only send in demo sandbox.
  const aliases = DEMO_MODE ? homeworkAliasesForStudent(getCurrentStudent()) : [];
  return {
    ...authHeaders(),
    ...(id ? { 'X-Student-Id': id } : {}),
    ...(aliases.length ? { 'X-Student-Aliases': aliases.join(',') } : {}),
  };
}

function teacherHeaders(): Record<string, string> {
  const teacherId = getCanonicalTeacherId();
  return {
    ...authHeaders(),
    ...(teacherId ? { 'X-Teacher-Id': teacherId } : {}),
  };
}

async function api<T>(path: string, options?: RequestInit & { as?: 'teacher' | 'student' }): Promise<T> {
  const as = options?.as || 'student';
  const res = await fetch(`${apiBase()}${path}`, {
    ...options,
    headers: {
      ...(as === 'teacher' ? teacherHeaders() : studentHeaders()),
      ...(options?.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
      ...(options?.headers as Record<string, string> | undefined),
    },
  });
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

export type MockLibraryItem = {
  id: string;
  title: string;
  mock_type: string;
  ielts_type: string;
  question_count?: number | null;
  duration_minutes?: number | null;
};

export type MockAssignment = {
  id: string;
  title: string;
  mock_type: string;
  ielts_type: string;
  assign_mode?: string;
  batch_id?: string | null;
  batch_label?: string | null;
  available_at?: string | null;
  deadline_at?: string | null;
  duration_minutes: number;
  attempts_allowed?: number;
  secure_mode?: boolean;
  screen_monitoring?: boolean;
  fullscreen_required?: boolean;
  allow_late_start?: boolean;
  auto_submit?: boolean;
  paper_source?: string;
  result_mode?: 'teacher' | 'ai' | string;
  instructions?: string | null;
  assigned?: number;
  submitted?: number;
  inbox_status?: string;
  slot_id?: string;
  attempt_id?: string | null;
  question_count?: number;
  privacy_notice?: string | null;
  published?: boolean;
  listening_band?: number | null;
  reading_band?: number | null;
  writing_band?: number | null;
  speaking_band?: number | null;
  overall_band?: number | null;
  /** Single-skill mock: human label e.g. "Writing band" */
  result_label?: string;
  /** Single-skill mock: the band for mock_type only */
  skill_band?: number | null;
  writing_feedback?: string | null;
};

export type MockStudentRow = {
  id: string;
  student_id: string;
  student_name: string;
  status: string;
  duration_minutes: number;
  cancelled: boolean;
  flagged: boolean;
  attempt_id?: string | null;
  warning_count: number;
  current_section?: string | null;
  current_question?: number | null;
  question_total?: number | null;
  remaining_seconds?: number | null;
  listening_band?: number | null;
  reading_band?: number | null;
  writing_band?: number | null;
  speaking_band?: number | null;
  overall_band?: number | null;
  published?: boolean;
  last_seen_at?: string | null;
  paused?: boolean;
  screen_sharing?: boolean;
  connection?: string;
  last_warning?: string | null;
  livekit_identity?: string;
};

export type NotificationItem = {
  id: string;
  title: string;
  body: string;
  href?: string | null;
  read: boolean;
  created_at?: string | null;
};

export function mocksFlag() {
  if (DEMO_MODE) return Promise.resolve({ enabled: true });
  return api<{ enabled: boolean }>('/mocks/flag', { as: 'teacher' });
}

export function listMockLibrary() {
  if (DEMO_MODE) return Promise.resolve(demoListLibrary());
  return api<{ items: MockLibraryItem[] }>('/mocks/library', { as: 'teacher' });
}

export function createMockAssignment(body: Record<string, unknown>) {
  if (DEMO_MODE) return Promise.resolve(demoCreateMockAssignment(body));
  return api<MockAssignment>('/mocks/assignments', { method: 'POST', body: JSON.stringify(body), as: 'teacher' });
}

export async function uploadMockImage(file: File) {
  if (DEMO_MODE) {
    return { filename: file.name, url: URL.createObjectURL(file) };
  }
  const body = new FormData();
  body.append('file', file);
  return api<{ filename: string; url: string }>('/mocks/teacher/upload-image', { method: 'POST', body, as: 'teacher' });
}

export function listTeacherMocks() {
  if (DEMO_MODE) return Promise.resolve(demoListTeacherMocks());
  return api<{ assignments: MockAssignment[] }>('/mocks/assignments', { as: 'teacher' });
}

export function listMockStudents(id: string) {
  if (DEMO_MODE) return Promise.resolve(demoListMockStudents(id));
  return api<{ assignment: MockAssignment; students: MockStudentRow[] }>(`/mocks/assignments/${id}/students`, { as: 'teacher' });
}

export function patchMockStudent(slotId: string, body: Record<string, unknown>) {
  if (DEMO_MODE) return Promise.resolve(demoPatchMockStudent(slotId, body));
  return api(`/mocks/assignment-students/${slotId}`, { method: 'PATCH', body: JSON.stringify(body), as: 'teacher' });
}

export function mockStudentAction(slotId: string, body: Record<string, unknown>) {
  if (DEMO_MODE) return Promise.resolve(demoMockStudentAction(slotId, body));
  return api(`/mocks/assignment-students/${slotId}/actions`, { method: 'POST', body: JSON.stringify(body), as: 'teacher' });
}

export function studentMockInbox() {
  if (DEMO_MODE) {
    ensureDemoAssignedMocks();
    return Promise.resolve(demoStudentInbox(getCurrentStudent().id));
  }
  return api<{ items: MockAssignment[] }>('/mocks/student/inbox', { as: 'student' });
}

export function studentMockDetail(id: string) {
  if (DEMO_MODE) return Promise.resolve(demoStudentDetail(id, getCurrentStudent().id));
  return api<MockAssignment>(`/mocks/student/${id}`, { as: 'student' });
}

export function startMockAttempt(assignmentId: string, consent = true) {
  if (DEMO_MODE) return Promise.resolve(demoStartAttempt(assignmentId, getCurrentStudent().id));
  return api<{
    attempt_id: string;
    skill: Record<string, string>;
    remaining_seconds: number;
    screen_monitoring: boolean;
    fullscreen_required: boolean;
    auto_submit?: boolean;
    title?: string;
    assignment_id?: string;
    secure_mode?: boolean;
  }>('/mocks/attempts/start', { method: 'POST', body: JSON.stringify({ assignment_id: assignmentId, consent }), as: 'student' });
}

export function saveMockAnswers(attemptId: string, body: Record<string, unknown>) {
  if (DEMO_MODE) return Promise.resolve(demoSaveAnswers(attemptId, body));
  return api(`/mocks/attempts/${attemptId}/answers`, { method: 'POST', body: JSON.stringify(body), as: 'student' });
}

export function getMockAttemptWork(attemptId: string) {
  if (DEMO_MODE) return Promise.resolve(demoGetAttemptWork(attemptId));
  return api<{
    attempt_id: string;
    assignment_id: string;
    student_id: string;
    student_name: string;
    status: string;
    submitted_at?: string | null;
    answers: { question_key: string; value: unknown }[];
    writing_band?: number | null;
    listening_band?: number | null;
    reading_band?: number | null;
    speaking_band?: number | null;
    overall_band?: number | null;
    writing_feedback?: string | null;
    published?: boolean;
  }>(`/mocks/attempts/${attemptId}`, { as: 'teacher' });
}

export function mockHeartbeat(attemptId: string, body: Record<string, unknown>) {
  if (DEMO_MODE) return Promise.resolve(demoHeartbeat(attemptId, body));
  return api<{
    ok: boolean;
    warning_count?: number;
    remaining_seconds?: number;
    status?: string;
    paused?: boolean;
  }>(`/mocks/attempts/${attemptId}/heartbeat`, { method: 'POST', body: JSON.stringify(body), as: 'student' });
}

export function submitMockAttempt(attemptId: string) {
  if (DEMO_MODE) return Promise.resolve(demoSubmitAttempt(attemptId));
  return api(`/mocks/attempts/${attemptId}/submit`, { method: 'POST', body: JSON.stringify({}), as: 'student' });
}

export function mockMonitor(id: string) {
  if (DEMO_MODE) return Promise.resolve(demoMonitor(id));
  return api<{
    assignment: MockAssignment;
    students: MockStudentRow[];
    totals: Record<string, number>;
    screen_note: string;
    livekit?: { configured?: boolean };
  }>(`/mocks/assignments/${id}/monitor`, { as: 'teacher' });
}

export function livekitToken(assignmentId: string, role: 'student' | 'teacher') {
  if (DEMO_MODE) {
    return Promise.resolve({ enabled: false, url: '', token: '', room: '', identity: '' });
  }
  return api<{ enabled: boolean; url: string; token: string; room: string; identity: string }>('/mocks/livekit/token', {
    method: 'POST',
    body: JSON.stringify({ assignment_id: assignmentId, role }),
    as: role === 'teacher' ? 'teacher' : 'student',
  });
}

export function reviewMockAttempt(attemptId: string, body: Record<string, unknown>) {
  if (DEMO_MODE) return Promise.resolve(demoReviewAttempt(attemptId, body));
  return api(`/mocks/attempts/${attemptId}/review`, { method: 'POST', body: JSON.stringify(body), as: 'teacher' });
}

export function mockEvents(attemptId: string) {
  if (DEMO_MODE) return Promise.resolve(demoMockEvents(attemptId));
  return api<{ events: { id: string; event_type: string; timestamp: string }[] }>(`/mocks/attempts/${attemptId}/events`, { as: 'teacher' });
}

export function listNotifications() {
  return api<{ items: NotificationItem[] }>('/notifications');
}

export function markNotificationRead(id: string) {
  return api(`/notifications/${id}/read`, { method: 'POST' });
}
