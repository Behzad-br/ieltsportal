/** DB-backed analytics dashboards — never fall back to fake demo metrics in API mode. */
import { authHeaders, clearAuthSession } from '@/lib/auth-api';
import { apiBase } from '@/lib/api-base';
import { messageFromErrorBody, parseJsonBody } from '@/lib/api-errors';
import { DEMO_MODE } from '@/lib/demo-mode';

async function analytics<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}${path}`, {
      headers: { ...authHeaders() },
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
  return (await parseJsonBody(res)) as T;
}

export type SkillMetric = {
  submitted_count: number;
  average_band: number | null;
  latest_band: number | null;
  policy?: string;
};

export type StudentAnalytics = {
  student_id: string;
  homework: {
    open: number;
    completed: number;
    overdue: number;
    pending_review: number;
    pending: number;
    in_progress: number;
    submitted: number;
    reviewed: number;
    published: number;
  };
  mocks: { open: number; submitted: number; published: number; note?: string };
  skills: {
    reading: SkillMetric;
    listening: SkillMetric;
    writing: SkillMetric;
    speaking: SkillMetric;
    overall_ielts: null;
    overall_note?: string;
  };
};

export type TeacherAnalytics = {
  branch_id: string;
  active_students: number;
  active_students_label?: string;
  batches: { id: string; name: string; active_students: number }[];
  pending_reviews: number;
  active_homework: number;
  skill_average_band: number | null;
  skill_average_note?: string;
  recent_pending_reviews: {
    submission_id: string;
    assignment_id: string;
    title: string;
    student_id: string;
    student_name: string;
    submitted_at?: string | null;
  }[];
};

export type BranchAdminAnalytics = {
  branch_id: string | null;
  active_teachers: number;
  active_students: number;
  inactive_students: number;
  batches: number;
  skill_average_band: number | null;
  skill_average_note?: string;
  pending_reviews: number;
  active_homework: number;
};

export type SuperAdminAnalytics = {
  branches_total: number;
  branches_active: number;
  active_students: number;
  inactive_students: number;
  active_teachers: number;
  active_branch_admins: number;
  batches: number;
  skill_average_band: number | null;
  skill_average_note?: string;
};

export type StudentRecordAnalytics = {
  student_id: string;
  branch_id?: string | null;
  assigned: number;
  completed: number;
  in_progress: number;
  pending: number;
  completion_pct: number;
  practice_sessions: number;
  skills: { skill: string; assigned: number; completed: number }[];
  progress?: StudentAnalytics['skills'];
};

export function formatBand(value: number | null | undefined): string {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return Number(value).toFixed(1);
}

/** Demo sandbox may keep local placeholders; API mode must call the server. */
export function fetchStudentAnalytics() {
  if (DEMO_MODE) {
    return Promise.reject(new Error('DEMO_MODE — analytics API not used in demo sandbox'));
  }
  return analytics<StudentAnalytics>('/analytics/student');
}

export function fetchTeacherAnalytics() {
  if (DEMO_MODE) {
    return Promise.reject(new Error('DEMO_MODE — analytics API not used in demo sandbox'));
  }
  return analytics<TeacherAnalytics>('/analytics/teacher');
}

export function fetchBranchAdminAnalytics() {
  if (DEMO_MODE) {
    return Promise.reject(new Error('DEMO_MODE — analytics API not used in demo sandbox'));
  }
  return analytics<BranchAdminAnalytics>('/analytics/branch-admin');
}

export function fetchSuperAdminAnalytics() {
  if (DEMO_MODE) {
    return Promise.reject(new Error('DEMO_MODE — analytics API not used in demo sandbox'));
  }
  return analytics<SuperAdminAnalytics>('/analytics/super-admin');
}

export function fetchStudentRecordAnalytics(studentId: string) {
  if (DEMO_MODE) {
    return Promise.reject(new Error('DEMO_MODE — analytics API not used in demo sandbox'));
  }
  return analytics<StudentRecordAnalytics>(`/analytics/student-record/${encodeURIComponent(studentId)}`);
}
