import { authHeaders, clearAuthSession, getAuthUser } from "@/lib/auth-api";
import { apiBase } from "@/lib/api-base";
import { messageFromErrorBody, parseJsonBody } from "@/lib/api-errors";
import { getCanonicalStudentId } from "@/lib/identity";

async function hw<T>(path: string, options?: RequestInit): Promise<T> {
  const user = getAuthUser();
  // Students use canonical JWT id. Staff teacher endpoints omit X-Student-Id.
  const studentId =
    user?.role === "student"
      ? getCanonicalStudentId()
      : user?.role === "teacher" || user?.role === "branch_admin" || user?.role === "super_admin"
        ? ""
        : getCanonicalStudentId();

  let res: Response;
  try {
    res = await fetch(`${apiBase()}${path}`, {
      ...options,
      headers: {
        ...authHeaders(),
        ...(studentId ? { "X-Student-Id": studentId } : {}),
        ...(options?.body && !(options.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...(options?.headers as Record<string, string> | undefined),
      },
    });
  } catch {
    throw new Error("Cannot connect to server. Make sure the backend is running.");
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

export type HomeworkItem = {
  id: string;
  title: string;
  module: string;
  scope: string;
  source: string;
  task_label?: string;
  question_type?: string;
  batch_label?: string;
  deadline?: string | null;
  preview?: string;
  status?: string;
  submitted?: number;
  assigned?: number;
  submission_id?: string | null;
};

export function teacherBank(module: string, task: string) {
  return hw<{ items: { id: string; title: string; detail: string; passage_id?: string; part_id?: string }[] }>(
    `/homework/teacher/bank?module=${encodeURIComponent(module)}&task=${encodeURIComponent(task)}`,
  );
}

export function teacherPreview(body: Record<string, unknown>) {
  return hw<{ preview: string; title: string; payload: Record<string, unknown> }>("/homework/teacher/preview", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function createHomework(body: Record<string, unknown>) {
  return hw<HomeworkItem>("/homework/teacher/assignments", { method: "POST", body: JSON.stringify(body) });
}

export function listTeacherHomework() {
  return hw<{ assignments: HomeworkItem[] }>("/homework/teacher/assignments");
}

export function deleteHomework(id: string) {
  return hw(`/homework/teacher/assignments/${id}`, { method: "DELETE" });
}

export function listSubmissions(id: string) {
  return hw<{ assignment: HomeworkItem; submissions: any[] }>(`/homework/teacher/assignments/${id}/submissions`);
}

export function reviewSubmission(id: string, body: Record<string, unknown>) {
  return hw(`/homework/teacher/submissions/${id}/review`, { method: "POST", body: JSON.stringify(body) });
}

export function studentHomework() {
  return hw<{ assignments: HomeworkItem[] }>("/homework/student");
}

export type StudentHomeworkRecordItem = HomeworkItem & {
  estimated_band?: number | null;
  teacher_band?: number | null;
  teacher_comments?: string | null;
  submitted_at?: string | null;
};

export function teacherStudentRecord(ids: string[]) {
  const query = encodeURIComponent(ids.filter(Boolean).join(","));
  return hw<{
    submitted: StudentHomeworkRecordItem[];
    in_progress: StudentHomeworkRecordItem[];
    pending: StudentHomeworkRecordItem[];
  }>(`/homework/teacher/student-record?ids=${query}`);
}

export function studentHomeworkDetail(id: string) {
  return hw<HomeworkItem & { payload?: any }>(`/homework/student/${id}`);
}

export function startHomework(id: string) {
  return hw<{
    submission_id: string;
    kind: string;
    questionId?: string;
    mockId?: string;
    attemptId?: string;
    sessionId?: string;
    ref?: string;
  }>(`/homework/student/${id}/start`, { method: "POST" });
}

export function completeHomework(submissionId: string, extra?: Record<string, unknown>) {
  return hw(`/homework/student/submissions/${submissionId}/complete`, {
    method: "POST",
    body: JSON.stringify(extra || {}),
  });
}

export async function uploadHomeworkAudio(file: File) {
  const body = new FormData();
  body.append("file", file);
  return hw<{ filename: string; url: string }>("/homework/teacher/upload-audio", { method: "POST", body });
}
