export type Role = 'student' | 'teacher' | 'admin' | 'branch-admin';
export type AssignmentStatus = 'Pending' | 'In progress' | 'Completed';

export type Assignment = {
  id: string; title: string; task: 'Task 1' | 'Task 2'; type: 'Academic' | 'General Training';
  status: AssignmentStatus; due: string; dueLabel: string; teacher: string; batch: string;
  question: string; words: number; duration: number; submitted?: string;
};
export type Student = { id: string; name: string; initials: string; email: string; band: number; trend: string; status: string; lastActive: string; batch: string; username?: string };
export type WritingSession = { attemptId: string; startedAt: number; expiresAt: number; serverTime: number; durationMinutes: number };
export type ReviewDraft = {
  attemptId: string;
  criteria: Record<string, string>;
  overall: string;
  feedback: string;
  strengths: string;
  areas: string;
  corrections: { original: string; corrected: string; category: string; explanation: string }[];
  status: 'Draft' | 'Published';
};

export const student = { name: '', initials: '', email: '', track: 'Academic', batch: '' };
export const assignments: Assignment[] = [];
export const students: Student[] = [];
export const trendData: { month: string; band: number }[] = [];
export const adminTrend: { month: string; attempts: number; band: number }[] = [];

export type StudentWorkItem = { id: string; title: string; skill: string; status: 'Pending' | 'In progress' | 'Completed'; due: string; teacher: string };

export type StudentWorkReport = {
  assigned: number;
  completed: number;
  inProgress: number;
  pending: number;
  completionPct: number;
  practiceSessions: number;
  skills: { skill: string; assigned: number; completed: number }[];
  items: StudentWorkItem[];
};

function skillFromAssignment(a: Assignment): string {
  if (a.task === 'Task 1' || a.task === 'Task 2') return 'Writing';
  return 'Writing';
}

export function homeworkAliasesForStudent(s: { id: string; email?: string; username?: string }) {
  // Staging: do not invent "local"/"s1" aliases. Historical reads use backend DB username/email.
  const ids = [s.id, s.username, s.email].filter(Boolean) as string[];
  return [...new Set(ids.map((id) => String(id)).filter((id) => id && id !== 'local' && id !== 's1'))];
}

export function studentWorkReport(studentId: string, batch: string): StudentWorkReport {
  const fromBatch = assignments.filter((a) => a.batch === batch).map((a) => ({
    id: `${studentId}-${a.id}`,
    title: a.title,
    skill: skillFromAssignment(a),
    status: a.status,
    due: a.dueLabel,
    teacher: a.teacher,
  }));
  const seed: StudentWorkItem[] = [
    { id: `${studentId}-w1`, title: 'Opinion essay · Community', skill: 'Writing', status: 'Completed', due: 'Submitted 2 days ago', teacher: 'Nadia Rahman' },
    { id: `${studentId}-r1`, title: 'Reading · Matching headings', skill: 'Reading', status: 'Completed', due: 'Reviewed last week', teacher: 'Nadia Rahman' },
    { id: `${studentId}-l1`, title: 'Listening Part 2 · Campus tour', skill: 'Listening', status: 'In progress', due: 'Due in 4 days', teacher: 'Kamran Ali' },
    { id: `${studentId}-s1`, title: 'Speaking Part 3 · Cities', skill: 'Speaking', status: 'Pending', due: 'Due in 7 days', teacher: 'Hina Saeed' },
  ];
  const items = fromBatch.length ? fromBatch : seed;
  const assigned = items.length;
  const completed = items.filter((i) => i.status === 'Completed').length;
  const inProgress = items.filter((i) => i.status === 'In progress').length;
  const pending = items.filter((i) => i.status === 'Pending').length;
  const bySkill = ['Writing', 'Reading', 'Listening', 'Speaking'].map((skill) => {
    const rows = items.filter((i) => i.skill === skill);
    const extraAssigned = rows.length ? rows.length : (skill === 'Writing' ? assigned : studentId.charCodeAt(1) % 3);
    const extraDone = rows.length ? rows.filter((i) => i.status === 'Completed').length : Math.max(0, extraAssigned - 1);
    return { skill, assigned: extraAssigned, completed: extraDone };
  });
  return {
    assigned,
    completed,
    inProgress,
    pending,
    completionPct: assigned ? Math.round((completed / assigned) * 100) : 0,
    practiceSessions: 8 + (studentId.charCodeAt(studentId.length - 1) % 12),
    skills: bySkill,
    items,
  };
}
export const reviewEssay = `In my opinion, making public transport free could be an effective way to reduce traffic congestion, although it would need to be introduced carefully.\n\nThe most immediate advantage would be encouraging people to leave their cars at home. Many commuters currently choose private vehicles because they are convenient, but a free and reliable train or bus service would remove one important reason to drive. This could reduce the number of vehicles on the roads during peak hours.\n\nHowever, free transport alone cannot solve congestion. Services would need more routes and better frequency, particularly in growing suburbs. The cost would also be significant for governments, and poorly maintained services might discourage passengers despite being free.\n\nOverall, I agree that free public transport could contribute to less traffic, but it should form part of a wider investment in reliable, well-connected infrastructure.`;

export function getPublishedReview() { return localStorage.getItem('ielts-published-review') === 'true'; }
export function setPublishedReview(value: boolean) { localStorage.setItem('ielts-published-review', String(value)); }
export function getDraft() { return localStorage.getItem('ielts-writing-draft') || ''; }
export function saveDraft(value: string) { localStorage.setItem('ielts-writing-draft', value); }
export function getRole(): Role { return (sessionStorage.getItem('ielts-role') as Role) || 'student'; }
export function setRole(role: Role) { sessionStorage.setItem('ielts-role', role); }

const mockStore = <T,>(key: string, fallback: T): T => {
  try { return JSON.parse(localStorage.getItem(key) || '') as T; } catch { return fallback; }
};

export function startWritingSession(attemptId: string, durationMinutes: number): WritingSession {
  const serverTime = Date.now();
  const session = { attemptId, startedAt: serverTime, expiresAt: serverTime + durationMinutes * 60_000, serverTime, durationMinutes };
  localStorage.setItem(`ielts-session-${attemptId}`, JSON.stringify(session));
  return session;
}

export function getWritingSession(attemptId: string, durationMinutes: number): WritingSession {
  return mockStore<WritingSession>(`ielts-session-${attemptId}`, startWritingSession(attemptId, durationMinutes));
}

export function saveWritingDraft(attemptId: string, value: string): { savedAt: number } {
  localStorage.setItem(`ielts-draft-${attemptId}`, value);
  return { savedAt: Date.now() };
}

export function getWritingDraft(attemptId: string): string {
  return localStorage.getItem(`ielts-draft-${attemptId}`) || getDraft();
}

export function submitHomework(attemptId: string, value: string): { attemptId: string; status: 'Awaiting Teacher Review'; wordCount: number } {
  const result = { attemptId, status: 'Awaiting Teacher Review' as const, wordCount: value.trim() ? value.trim().split(/\s+/).length : 0 };
  localStorage.setItem(`ielts-homework-submission-${attemptId}`, JSON.stringify(result));
  return result;
}

export function submitPractice(attemptId: string, value: string): { attemptId: string; status: 'Analysing'; wordCount: number } {
  const result = { attemptId, status: 'Analysing' as const, wordCount: value.trim() ? value.trim().split(/\s+/).length : 0 };
  localStorage.setItem(`ielts-practice-submission-${attemptId}`, JSON.stringify(result));
  return result;
}

export function getReviewDraft(attemptId: string): ReviewDraft {
  return mockStore<ReviewDraft>(`ielts-review-${attemptId}`, {
    attemptId,
    criteria: { 'Task response': '7.0', 'Coherence & cohesion': '6.5', 'Lexical resource': '6.0', 'Grammatical range & accuracy': '6.5' },
    overall: '6.5',
    feedback: 'Clear position and a logical structure. Make supporting examples more specific to move towards band 7.',
    strengths: 'A clear position, balanced paragraphs, and a confident conclusion.',
    areas: 'Develop examples further and check article use in longer sentences.',
    corrections: [{ original: 'free and reliable train or bus service', corrected: 'a free, reliable train or bus service', category: 'Grammar', explanation: 'Use a comma between coordinate adjectives.' }],
    status: getPublishedReview() ? 'Published' : 'Draft',
  });
}

export function saveReviewDraft(review: ReviewDraft): ReviewDraft {
  localStorage.setItem(`ielts-review-${review.attemptId}`, JSON.stringify(review));
  return review;
}

export function publishReview(review: ReviewDraft): ReviewDraft {
  const published = { ...review, status: 'Published' as const };
  saveReviewDraft(published);
  setPublishedReview(true);
  return published;
}

export function listMockEntities(kind: string): Record<string, string>[] {
  return mockStore<Record<string, string>[]>(`ielts-entities-${kind}`, []);
}

function deletedTeacherKeys(): Set<string> {
  return new Set(mockStore<string[]>('ielts-deleted-teachers', []));
}

function deletedStudentKeys(): Set<string> {
  return new Set(mockStore<string[]>('ielts-deleted-students', []));
}

function sanitizeEntityRow(row: Record<string, string>): Record<string, string> {
  // Never keep plaintext passwords in org entity caches.
  return { ...row, password: '' };
}

function writeEntityCache(
  kind: string,
  saved: Record<string, string>,
  opts?: { keepPassword?: boolean },
) {
  const key = `ielts-entities-${kind}`;
  // Staging/API: never cache plaintext passwords. Demo sandbox may keep them for local login.
  const safe = opts?.keepPassword ? { ...saved } : sanitizeEntityRow(saved);
  const rows = mockStore<Record<string, string>[]>(key, []).filter((row) => row.id !== safe.id);
  localStorage.setItem(key, JSON.stringify([safe, ...rows]));
  if (kind === 'teacher') {
    const deleted = deletedTeacherKeys();
    deleted.delete(safe.id);
    if (safe.email) deleted.delete(safe.email.toLowerCase());
    localStorage.setItem('ielts-deleted-teachers', JSON.stringify([...deleted]));
  }
  if (kind === 'student') {
    const deleted = deletedStudentKeys();
    deleted.delete(safe.id);
    if (safe.email) deleted.delete(safe.email.toLowerCase());
    localStorage.setItem('ielts-deleted-students', JSON.stringify([...deleted]));
  }
  return safe;
}

async function patchOrgUser(userId: string, body: Record<string, unknown>) {
  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) throw new Error('Sign in before updating accounts.');
  return orgFetch<Record<string, unknown>>(`/org/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

async function deactivateOrgUser(userId: string) {
  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) throw new Error('Sign in before deactivating accounts.');
  await orgFetch(`/org/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
}

/**
 * Create/update org entity.
 * DEMO_MODE: localStorage only (sandbox).
 * Staging/API mode: PostgreSQL via /org/* is required; localStorage is cache after success.
 */
export async function saveMockEntity(kind: string, value: Record<string, string>): Promise<Record<string, string>> {
  const { DEMO_MODE } = await import('@/lib/demo-mode');
  const key = `ielts-entities-${kind}`;

  if (DEMO_MODE) {
    const saved: Record<string, string> = { ...value, id: value.id || `${kind}-${Date.now()}` };
    return writeEntityCache(kind, saved, { keepPassword: Boolean(saved.password) });
  }

  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) {
    throw new Error('Sign in with a real account before creating org data.');
  }

  if (kind === 'teacher' || kind === 'student') {
    const created = await orgFetch<{ id: string }>('/org/users', {
      method: 'POST',
      body: JSON.stringify({
        role: kind === 'teacher' ? 'teacher' : 'student',
        email: value.email,
        username: value.username || (value.email || '').split('@')[0],
        password: value.password || 'ChangeMe123!',
        full_name: value.fullName || value.name || '',
        branch_id: value.branchId || '',
        batch: value.batch || '',
        batches: value.batches || '',
      }),
    });
    const saved = {
      ...value,
      id: created.id,
      // Never keep plaintext passwords in the UI cache after a successful API create.
      password: '',
    };
    writeEntityCache(kind, saved);
    return saved;
  }

  if (kind === 'batch') {
    // Update existing campus batch (rename / teacher reassignment) via PATCH.
    if (value.id) {
      const updated = await orgFetch<{
        id: string;
        name?: string;
        branchId?: string;
        teacherId?: string;
        students?: number;
      }>(`/org/batches/${encodeURIComponent(value.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: value.name != null ? String(value.name).trim() : undefined,
          schedule: value.schedule,
          subjects: value.subjects,
          teacher_id: value.teacherId != null ? value.teacherId : undefined,
        }),
      });
      const saved = {
        ...value,
        id: updated.id,
        name: updated.name || value.name,
        branchId: updated.branchId || value.branchId || '',
        teacherId: updated.teacherId || value.teacherId || '',
        students: String(updated.students ?? value.students ?? 0),
      };
      writeEntityCache(kind, saved);
      return saved;
    }
    const created = await orgFetch<{ id: string; name?: string; branchId?: string; teacherId?: string }>(
      '/org/batches',
      {
        method: 'POST',
        body: JSON.stringify({
          name: (value.name || '').trim(),
          schedule: value.schedule || '',
          subjects: value.subjects || '',
          branch_id: value.branchId || '',
          teacher_id: value.teacherId || '',
        }),
      },
    );
    const saved = {
      ...value,
      id: created.id,
      name: created.name || value.name,
      branchId: created.branchId || value.branchId || '',
      teacherId: created.teacherId || value.teacherId || '',
    };
    writeEntityCache(kind, saved);
    return saved;
  }

  // Unknown kinds: do not invent local-only business rows in staging.
  throw new Error(`Unsupported entity kind for API persistence: ${kind}`);
}

const seededTeachers: Record<string, string>[] = [];

export function listTeacherAccounts(): Record<string, string>[] {
  const deleted = deletedTeacherKeys();
  const created = listMockEntities('teacher').filter((t) =>
    !deleted.has(t.id) && !deleted.has((t.email || '').toLowerCase())
  );
  return created;
}

export function getTeacherAccount(id: string): Record<string, string> | undefined {
  return listTeacherAccounts().find((t) => t.id === id);
}

const TEACHER_SESSION_KEY = 'ielts-current-teacher-id';
const TEACHER_CHANGED_EVENT = 'ielts-teacher-changed';

function notifyTeacherSession() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(TEACHER_CHANGED_EVENT));
}

export function subscribeTeacherSession(listener: () => void) {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener(TEACHER_CHANGED_EVENT, listener);
  window.addEventListener('storage', listener);
  return () => {
    window.removeEventListener(TEACHER_CHANGED_EVENT, listener);
    window.removeEventListener('storage', listener);
  };
}

export function setCurrentTeacher(id: string) {
  sessionStorage.setItem(TEACHER_SESSION_KEY, id);
  notifyTeacherSession();
}

export function getCurrentTeacher(): Record<string, string> {
  const id = typeof window === 'undefined' ? '' : (sessionStorage.getItem(TEACHER_SESSION_KEY) || '');
  const accounts = listTeacherAccounts();
  return (
    accounts.find((t) => t.id === id) ||
    accounts[0] ||
    { id: '', fullName: '', name: '', email: '', username: '', password: '', batches: '', branchId: '', branchName: '' }
  );
}

export async function updateCurrentTeacherAccount(patch: { fullName?: string; password?: string }): Promise<Record<string, string> | null> {
  const current = getCurrentTeacher();
  if (!current?.id) return null;
  const nextName = (patch.fullName ?? current.fullName ?? current.name ?? '').trim();
  if (nextName.length < 2) return null;
  const nextPatch: Record<string, string> = { fullName: nextName, name: nextName };
  if (patch.password) nextPatch.password = patch.password;
  const saved = await updateTeacherAccount(current.id, nextPatch);
  if (saved) setCurrentTeacher(saved.id);
  return saved;
}

export type SuperAdminAccount = {
  fullName: string;
  name: string;
  username: string;
  password: string;
};

export type LmsPreferences = {
  academyName: string;
  targetBand: string;
};

const SUPER_ADMIN_KEY = 'ielts-super-admin';
const SUPER_ADMIN_CHANGED_EVENT = 'ielts-super-admin-changed';
const LMS_PREFS_KEY = 'ielts-lms-preferences';

const defaultSuperAdmin: SuperAdminAccount = {
  fullName: 'Super Admin',
  name: 'Super Admin',
  username: 'admin',
  password: '',
};

const defaultLmsPreferences: LmsPreferences = {
  academyName: 'FTI IELTS Learning Management System',
  targetBand: '7.0',
};

function notifySuperAdminSession() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(SUPER_ADMIN_CHANGED_EVENT));
}

export function subscribeSuperAdminSession(listener: () => void) {
  if (typeof window === 'undefined') return () => {};
  window.addEventListener(SUPER_ADMIN_CHANGED_EVENT, listener);
  window.addEventListener('storage', listener);
  return () => {
    window.removeEventListener(SUPER_ADMIN_CHANGED_EVENT, listener);
    window.removeEventListener('storage', listener);
  };
}

export function getCurrentSuperAdmin(): SuperAdminAccount {
  const stored = mockStore<Partial<SuperAdminAccount> | null>(SUPER_ADMIN_KEY, null);
  if (!stored) return { ...defaultSuperAdmin };
  const fullName = (stored.fullName || stored.name || defaultSuperAdmin.fullName).trim() || defaultSuperAdmin.fullName;
  return {
    fullName,
    name: (stored.name || fullName).trim() || fullName,
    username: (stored.username || defaultSuperAdmin.username).trim() || defaultSuperAdmin.username,
    password: stored.password || defaultSuperAdmin.password,
  };
}

/** DEMO/localStorage only — production login uses auth-api → POST /api/auth/login. */
export function matchSuperAdminLogin(username: string, password: string): SuperAdminAccount | null {
  const account = getCurrentSuperAdmin();
  if (username.trim().toLowerCase() !== account.username.toLowerCase()) return null;
  if (account.password !== password) return null;
  return account;
}

export async function updateCurrentSuperAdminAccount(patch: {
  fullName?: string;
  password?: string;
  currentPassword?: string;
}): Promise<SuperAdminAccount | null> {
  const { DEMO_MODE } = await import('@/lib/demo-mode');
  const current = getCurrentSuperAdmin();
  const nextName = (patch.fullName ?? current.fullName ?? current.name ?? '').trim();
  if (nextName.length < 2) return null;

  if (DEMO_MODE) {
    const next: SuperAdminAccount = {
      ...current,
      fullName: nextName,
      name: nextName,
      // Demo sandbox may keep a local password for matchSuperAdminLogin; staging never does.
      password: patch.password || current.password,
    };
    localStorage.setItem(SUPER_ADMIN_KEY, JSON.stringify(next));
    notifySuperAdminSession();
    return next;
  }

  const { getAuthUser, getAccessToken, orgFetch, authHeaders } = await import('@/lib/auth-api');
  const { apiBase } = await import('@/lib/api-base');
  if (!getAccessToken()) throw new Error('Sign in before updating your account.');
  const me = getAuthUser();
  if (!me?.id || me.role !== 'super_admin') throw new Error('Only a signed-in super admin can update this account.');

  await orgFetch(`/org/users/${encodeURIComponent(me.id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ full_name: nextName }),
  });

  if (patch.password) {
    if (!patch.currentPassword) throw new Error('Enter your current password to change it.');
    const res = await fetch(`${apiBase()}/auth/change-password`, {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: patch.currentPassword, new_password: patch.password }),
    });
    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch { /* ignore */ }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
  }

  const next: SuperAdminAccount = {
    fullName: nextName,
    name: nextName,
    username: me.username || current.username,
    password: '',
  };
  localStorage.setItem(SUPER_ADMIN_KEY, JSON.stringify(next));
  notifySuperAdminSession();
  return next;
}

export function getLmsPreferences(): LmsPreferences {
  const stored = mockStore<Partial<LmsPreferences> | null>(LMS_PREFS_KEY, null);
  if (!stored) return { ...defaultLmsPreferences };
  return {
    academyName: stored.academyName?.trim() || defaultLmsPreferences.academyName,
    targetBand: stored.targetBand || defaultLmsPreferences.targetBand,
  };
}

export function saveLmsPreferences(patch: Partial<LmsPreferences>): LmsPreferences | null {
  const current = getLmsPreferences();
  const academyName = (patch.academyName ?? current.academyName).trim();
  const targetBand = (patch.targetBand ?? current.targetBand).trim();
  if (academyName.length < 2) return null;
  const next: LmsPreferences = { academyName, targetBand };
  localStorage.setItem(LMS_PREFS_KEY, JSON.stringify(next));
  return next;
}

export async function updateTeacherAccount(id: string, patch: Record<string, string>): Promise<Record<string, string> | null> {
  const current = listTeacherAccounts().find((t) => t.id === id);
  if (!current) return null;
  const next = sanitizeEntityRow({ ...current, ...patch, id });

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    const demoRow = { ...current, ...patch, id };
    const saved = writeEntityCache('teacher', demoRow, { keepPassword: true });
    if (typeof window !== 'undefined' && localStorage.getItem(TEACHER_SESSION_KEY) === id) notifyTeacherSession();
    return saved;
  }

  const body: Record<string, unknown> = {
    full_name: next.fullName || next.name || '',
    email: next.email || undefined,
    username: next.username || undefined,
    batches: next.batches || undefined,
    batch: next.batch || undefined,
  };
  if (patch.password && patch.password.length >= 6) body.password = patch.password;
  await patchOrgUser(id, body);
  const saved = writeEntityCache('teacher', next);
  if (typeof window !== 'undefined' && localStorage.getItem(TEACHER_SESSION_KEY) === id) notifyTeacherSession();
  return saved;
}

export async function removeTeacherAccount(id: string): Promise<boolean> {
  const current = getTeacherAccount(id);
  if (!current) return false;

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (!DEMO_MODE) {
    await deactivateOrgUser(id);
  } else {
    await setTeacherBatchAssignments(id, []);
  }

  const created = listMockEntities('teacher').filter((t) =>
    t.id !== id && (t.email || '').toLowerCase() !== (current.email || '').toLowerCase()
  );
  localStorage.setItem('ielts-entities-teacher', JSON.stringify(created.map(sanitizeEntityRow)));
  const deleted = deletedTeacherKeys();
  deleted.add(id);
  if (current.email) deleted.add(current.email.toLowerCase());
  localStorage.setItem('ielts-deleted-teachers', JSON.stringify([...deleted]));
  return true;
}

export function isStudentRemoved(id: string, email?: string): boolean {
  const deleted = deletedStudentKeys();
  return deleted.has(id) || Boolean(email && deleted.has(email.toLowerCase()));
}

export async function removeStudentAccount(id: string, email?: string): Promise<boolean> {
  const created = listMockEntities('student');
  const current = created.find((s) => s.id === id || (email && (s.email || '').toLowerCase() === email.toLowerCase()));
  const mail = (current?.email || email || '').toLowerCase();
  if (!current && !id) return false;

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (!DEMO_MODE) {
    await deactivateOrgUser(id);
  }

  localStorage.setItem(
    'ielts-entities-student',
    JSON.stringify(
      created
        .filter((s) => s.id !== id && (s.email || '').toLowerCase() !== mail)
        .map(sanitizeEntityRow),
    ),
  );
  const deleted = deletedStudentKeys();
  deleted.add(id);
  if (mail) deleted.add(mail);
  localStorage.setItem('ielts-deleted-students', JSON.stringify([...deleted]));
  return true;
}

const seededStudentAccounts: Record<string, string>[] = [];

export function listStudentAccounts(): Record<string, string>[] {
  const deleted = deletedStudentKeys();
  const created = listMockEntities('student').filter((s) =>
    !deleted.has(s.id) && !deleted.has((s.email || '').toLowerCase())
  );
  return created.map((s) => ({
    ...sanitizeEntityRow(s),
    username: s.username || (s.email || '').split('@')[0],
  }));
}

export function getStudentAccount(id: string): Record<string, string> | undefined {
  return listStudentAccounts().find((s) => s.id === id);
}

function rosterInitials(name: string) {
  return name.split(/\s+/).filter(Boolean).map((p) => p[0]).join('').slice(0, 2).toUpperCase() || 'ST';
}

export function listTeacherRoster(): Student[] {
  // API hydrate already branch-scopes students for teachers; still filter by campus id when present.
  const teacher = getCurrentTeacher();
  const campusId = (teacher?.branchId || '').trim();
  return listMockEntities('student')
    .filter((s) => !isStudentRemoved(s.id, s.email))
    .filter((s) => !campusId || !s.branchId || s.branchId === campusId)
    .map((s) => {
      const name = s.fullName || s.name || 'Student';
      return {
        id: s.id,
        name,
        initials: rosterInitials(name),
        email: s.email || '',
        username: s.username || s.studentId || (s.email || '').split('@')[0],
        band: Number(s.band) || 0,
        trend: s.trend || '—',
        status: s.status || 'New',
        lastActive: s.lastActive || 'Just added',
        batch: s.batch || 'Unassigned',
      };
    });
}

export async function updateStudentAccount(id: string, patch: Record<string, string>): Promise<Record<string, string> | null> {
  const current = listStudentAccounts().find((s) => s.id === id);
  if (!current) return null;
  const seed = students.find((s) => s.id === id);
  const next = sanitizeEntityRow({
    ...current,
    trend: current.trend || seed?.trend || '—',
    band: current.band || (seed ? String(seed.band) : ''),
    status: current.status || seed?.status || 'Active',
    lastActive: current.lastActive || seed?.lastActive || 'Just updated',
    ...patch,
    id,
  });

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    const demoRow = {
      ...current,
      trend: current.trend || seed?.trend || '—',
      band: current.band || (seed ? String(seed.band) : ''),
      status: current.status || seed?.status || 'Active',
      lastActive: current.lastActive || seed?.lastActive || 'Just updated',
      ...patch,
      id,
    };
    return writeEntityCache('student', demoRow, { keepPassword: true });
  }

  const body: Record<string, unknown> = {
    full_name: next.fullName || next.name || '',
    email: next.email || undefined,
    username: next.username || undefined,
    batch: next.batch || undefined,
  };
  if (patch.password && patch.password.length >= 6) body.password = patch.password;
  await patchOrgUser(id, body);
  return writeEntityCache('student', next);
}

export function matchStudentLogin(emailOrUsername: string, password: string): Record<string, string> | null {
  const key = emailOrUsername.trim().toLowerCase();
  const row = listStudentAccounts().find((s) =>
    (s.email || '').toLowerCase() === key ||
    (s.username || '').toLowerCase() === key ||
    (s.studentId || '').toLowerCase() === key
  );
  if (!row || row.password !== password) return null;
  return row;
}

export type CurrentStudent = {
  id: string;
  name: string;
  batch: string;
  email: string;
  username?: string;
  /** Optional display track/stream label (not required for API identity). */
  track?: string;
};

export function setCurrentStudent(row: Partial<CurrentStudent> & { id: string }) {
  // Never map s1→local; never store blocked legacy ids as production identity.
  const id = row.id === 's1' || row.id === 'local' ? '' : row.id;
  const next: CurrentStudent = {
    id,
    name: row.name || 'Student',
    batch: row.batch || '',
    email: row.email || '',
    username: row.username || '',
    track: row.track || '',
  };
  sessionStorage.setItem('ielts-current-student', JSON.stringify(next));
  if (id) sessionStorage.setItem('writing_student_id', id);
  else sessionStorage.removeItem('writing_student_id');
}

export function getCurrentStudent(): CurrentStudent {
  try {
    const row = JSON.parse(sessionStorage.getItem('ielts-current-student') || '') as CurrentStudent;
    if (row?.id) return row;
  } catch {
    /* ignore */
  }
  return { id: '', name: '', batch: '', email: '', username: '', track: '' };
}

export type CampusBatch = {
  id: string;
  name: string;
  schedule: string;
  teacher: string;
  teacherId: string;
  subjects: string;
  students: number;
  avg: string;
  branchId: string;
};

function defaultBatchSubjects(teacherName: string): string {
  const n = teacherName.toLowerCase();
  if (n.includes('nadia')) return 'Writing · Speaking';
  if (n.includes('hina')) return 'Reading · Listening';
  if (n.includes('kamran')) return 'Writing · Reading';
  return 'Writing · Reading · Listening · Speaking';
}

const seededBatches: Record<string, string>[] = [];

function toCampusBatch(b: Record<string, string>): CampusBatch {
  const teacher = b.teacherName || b.teacher || '';
  return {
    id: b.id,
    name: b.name || b.id,
    schedule: b.schedule || '',
    teacher,
    teacherId: b.teacherId || '',
    subjects: b.subjects || defaultBatchSubjects(teacher),
    students: Number(b.students) || 0,
    avg: b.avg || '—',
    branchId: b.branchId || '',
  };
}

export function listCampusBatches(branchId?: string): CampusBatch[] {
  const created = listMockEntities('batch');
  return created.map(toCampusBatch).filter((b) => !branchId || b.branchId === branchId);
}

function teacherBatchNames(teacher: Record<string, string>): string[] {
  return (teacher.batches || '').split(',').map((n) => n.trim()).filter((n) => n && n !== '—');
}

export function assignedBatchIdsForTeacher(teacherId: string): string[] {
  const teacher = getTeacherAccount(teacherId);
  if (!teacher) return [];
  const named = new Set(teacherBatchNames(teacher));
  return listCampusBatches(teacher.branchId).filter((b) =>
    b.teacherId === teacher.id || named.has(b.name)
  ).map((b) => b.id);
}

export async function setTeacherBatchAssignments(teacherId: string, selectedIds: string[]): Promise<CampusBatch[]> {
  const teacher = getTeacherAccount(teacherId);
  if (!teacher) return [];
  const teacherName = teacher.fullName || teacher.name || '';
  const campusId = teacher.branchId;
  const selected = new Set(selectedIds);
  const campusBefore = listCampusBatches(campusId);
  const assignedNames = campusBefore.filter((b) => selected.has(b.id)).map((b) => b.name);
  const batchesCsv = assignedNames.join(', ') || '—';

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (!DEMO_MODE) {
    // Authoritative write: teacher.batches CSV on the user row (current schema linkage).
    await updateTeacherAccount(teacherId, { batches: batchesCsv });
    // Refresh batch cache from API so UI matches DB (Batch.teacher_id may lag until Phase 5).
    try {
      const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
      if (getAccessToken()) {
        const batches = await orgFetch<{ batches: Record<string, unknown>[] }>(
          `/org/batches${campusId ? `?branch_id=${encodeURIComponent(campusId)}` : ''}`,
        );
        const key = 'ielts-entities-batch';
        const other = listMockEntities('batch').filter((b) => campusId && b.branchId && b.branchId !== campusId);
        const refreshed = (batches.batches || []).map((b) => ({
          id: String(b.id || ''),
          name: String(b.name || ''),
          schedule: String(b.schedule || ''),
          subjects: String(b.subjects || ''),
          branchId: String(b.branchId || ''),
          teacherId: String(b.teacherId || ''),
          teacherName: '',
          students: String(b.students || 0),
          avg: String(b.avg || '—'),
        }));
        localStorage.setItem(key, JSON.stringify([...refreshed, ...other].map(sanitizeEntityRow)));
      }
    } catch {
      /* keep prior cache; teacher.batches already persisted */
    }
    return listCampusBatches(campusId);
  }

  const created = listMockEntities('batch');
  const rows = created.map((b) => {
    const onCampus = b.branchId === campusId;
    if (!onCampus) return b;
    if (selected.has(b.id)) {
      return { ...b, teacherId, teacherName, teacher: teacherName };
    }
    if (b.teacherId === teacherId) {
      return { ...b, teacherId: '', teacherName: '', teacher: '' };
    }
    return b;
  });
  localStorage.setItem('ielts-entities-batch', JSON.stringify(rows.map(sanitizeEntityRow)));

  const campus = listCampusBatches(campusId);
  await updateTeacherAccount(teacherId, { batches: batchesCsv });

  const peers = listTeacherAccounts().filter(
    (t) => t.id !== teacherId && (t.branchId === campusId || (!t.branchId && campusId === 'lahore')),
  );
  for (const t of peers) {
    const names = campus.filter((b) => b.teacherId === t.id).map((b) => b.name);
    await updateTeacherAccount(t.id, { batches: names.join(', ') || '—' });
  }

  return campus;
}

export function matchTeacherLogin(emailOrUsername: string, password: string): Record<string, string> | null {
  const key = emailOrUsername.trim().toLowerCase();
  const row = listTeacherAccounts().find((t) => (t.email || '').toLowerCase() === key || (t.username || '').toLowerCase() === key);
  if (!row || row.password !== password) return null;
  return row;
}

export type BranchAdminAccount = {
  name: string;
  username: string;
  password: string;
};

export type CampusBranch = {
  id: string;
  name: string;
  city: string;
  students: number;
  teachers: number;
  batches?: number;
  /** null = no computed campus skill average (never invent 0.0). */
  avg: number | null;
  admin: string;
  adminUsername: string;
  adminPassword: string;
  admins: BranchAdminAccount[];
  imageUrl?: string;
  /** Soft lifecycle — false means deactivated (history preserved). Default true. */
  isActive?: boolean;
};

const BRANCH_KEY = 'ielts-branches-v1';
const BRANCH_ADMIN_SESSION = 'ielts-branch-admin-id';
const BRANCH_ADMIN_USER = 'ielts-branch-admin-username';

function asAdmins(b: Partial<CampusBranch> & { admin?: string; adminUsername?: string; adminPassword?: string; admins?: BranchAdminAccount[] }): BranchAdminAccount[] {
  if (Array.isArray(b.admins) && b.admins.length) {
    return b.admins.map((a) => ({ name: a.name.trim(), username: a.username.trim(), password: a.password }));
  }
  if (b.adminUsername) {
    return [{ name: (b.admin || '').trim() || 'Branch Admin', username: b.adminUsername.trim(), password: b.adminPassword || '' }];
  }
  return [];
}

function syncPrimary(admins: BranchAdminAccount[]) {
  const first = admins[0];
  return {
    admins,
    admin: first?.name || '',
    adminUsername: first?.username || '',
    adminPassword: first?.password || '',
  };
}

const SEED_BRANCHES: CampusBranch[] = [];

function normalizeBranch(b: CampusBranch): CampusBranch {
  const admins = asAdmins(b);
  const imageUrl = (b.imageUrl || '').trim();
  const next: CampusBranch = {
    ...b,
    ...syncPrimary(admins),
    isActive: b.isActive === false ? false : true,
  };
  if (imageUrl) next.imageUrl = imageUrl;
  else delete next.imageUrl;
  return next;
}

function loadBranches(): CampusBranch[] {
  try {
    const raw = localStorage.getItem(BRANCH_KEY);
    if (raw == null) return [];
    const parsed = JSON.parse(raw) as CampusBranch[];
    return Array.isArray(parsed) ? parsed.map(normalizeBranch) : [];
  } catch {
    return [];
  }
}

function writeBranches(rows: CampusBranch[]) {
  try {
    localStorage.setItem(BRANCH_KEY, JSON.stringify(rows));
  } catch {
    throw new Error('Could not save campus data. If you added a photo, try a smaller image (under 2 MB).');
  }
}

export function slugBranchId(name: string) {
  const base = name.toLowerCase().replace(/branch/g, '').trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || `campus-${Date.now()}`;
  const existing = loadBranches().map((b) => b.id);
  if (!existing.includes(base)) return base;
  return `${base}-${Date.now()}`;
}

const SEED_BRANCH_PHOTOS = new Set(['lahore', 'karachi', 'islamabad', 'rawalpindi', 'faisalabad', 'multan']);
const BRANCH_IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif']);
export const MAX_BRANCH_IMAGE_BYTES = 2 * 1024 * 1024;
export const BRANCH_IMAGE_ACCEPT = 'image/jpeg,image/png,image/webp,image/gif,.jpg,.jpeg,.png,.webp,.gif';

function stockBranchPhoto(id: string) {
  return SEED_BRANCH_PHOTOS.has(id) ? `/branch-${id}.jpg` : '/fti-hero-study.jpg';
}

export function branchPhoto(idOrBranch: string | Pick<CampusBranch, 'id' | 'imageUrl'> | null | undefined): string {
  if (idOrBranch && typeof idOrBranch === 'object') {
    const custom = (idOrBranch.imageUrl || '').trim();
    return custom || stockBranchPhoto(idOrBranch.id);
  }
  const id = idOrBranch || '';
  const stored = (getBranch(id)?.imageUrl || '').trim();
  return stored || stockBranchPhoto(id);
}

export function validateBranchImageFile(file: File): string | null {
  const type = (file.type || '').toLowerCase();
  const okType = BRANCH_IMAGE_TYPES.has(type) || /\.(jpe?g|png|webp|gif)$/i.test(file.name);
  if (!okType) return 'Use a JPG, PNG, WebP, or GIF image.';
  if (file.size > MAX_BRANCH_IMAGE_BYTES) return 'Image must be 2 MB or smaller.';
  return null;
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('Could not read that image.'));
    reader.readAsDataURL(file);
  });
}

function compressBranchImage(dataUrl: string): Promise<string> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const maxEdge = 1280;
      const scale = Math.min(1, maxEdge / Math.max(img.width, img.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(img.width * scale));
      canvas.height = Math.max(1, Math.round(img.height * scale));
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        resolve(dataUrl);
        return;
      }
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      try {
        resolve(canvas.toDataURL('image/jpeg', 0.84));
      } catch {
        resolve(dataUrl);
      }
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}

export async function readBranchImageFile(file: File): Promise<string> {
  const problem = validateBranchImageFile(file);
  if (problem) throw new Error(problem);
  const dataUrl = await fileToDataUrl(file);
  return compressBranchImage(dataUrl);
}

export function listBranches(): CampusBranch[] {
  return loadBranches();
}

export function getBranch(id: string): CampusBranch | undefined {
  return loadBranches().find((b) => b.id === id);
}

export function normalizeCampusFromApi(raw: Record<string, unknown>): CampusBranch {
  const admins = asAdmins({
    admins: (raw.admins as BranchAdminAccount[] | undefined) || [],
    admin: String(raw.admin || ''),
    adminUsername: String(raw.adminUsername || ''),
    adminPassword: '',
  }).map((a) => ({ ...a, password: '' }));
  const isActiveRaw = raw.isActive ?? raw.is_active;
  const isActive = isActiveRaw === undefined || isActiveRaw === null ? true : Boolean(isActiveRaw);
  return {
    id: String(raw.id || ''),
    name: String(raw.name || ''),
    city: String(raw.city || ''),
    students: Number(raw.students || 0),
    teachers: Number(raw.teachers || 0),
    batches: Number(raw.batches || 0),
    avg: raw.avg == null || raw.avg === '' ? null : Number(raw.avg),
    imageUrl: String(raw.imageUrl || '') || undefined,
    isActive,
    ...syncPrimary(admins),
  };
}

export function replaceBranchesFromApi(rows: Record<string, unknown>[]) {
  writeBranches((rows || []).map((row) => normalizeCampusFromApi(row)));
}

function writeCampusLocal(saved: CampusBranch, mode: 'create' | 'update', opts?: { keepAdminPasswords?: boolean }) {
  const scrubbed: CampusBranch = opts?.keepAdminPasswords
    ? saved
    : {
        ...saved,
        adminPassword: '',
        admins: (saved.admins || []).map((a) => ({ ...a, password: '' })),
      };
  const rows = loadBranches();
  if (mode === 'create') {
    writeBranches([scrubbed, ...rows.filter((b) => b.id !== scrubbed.id)]);
    return;
  }
  const index = rows.findIndex((b) => b.id === scrubbed.id);
  if (index < 0) writeBranches([scrubbed, ...rows]);
  else {
    rows[index] = scrubbed;
    writeBranches(rows);
  }
}

export async function addBranch(input: {
  name: string;
  city: string;
  admins: BranchAdminAccount[];
  imageUrl?: string;
}): Promise<CampusBranch> {
  const admins = asAdmins({ admins: input.admins });
  if (!admins.length) throw new Error('At least one branch admin is required.');
  for (const admin of admins) {
    if (admin.password.length < 6) {
      throw new Error(`Password for @${admin.username} must be at least 6 characters.`);
    }
  }

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    const imageUrl = (input.imageUrl || '').trim();
    const saved: CampusBranch = {
      id: slugBranchId(input.name),
      name: input.name.trim(),
      city: input.city.trim(),
      students: 0,
      teachers: 0,
      avg: null,
      ...(imageUrl ? { imageUrl } : {}),
      ...syncPrimary(admins),
    };
    writeCampusLocal(saved, 'create', { keepAdminPasswords: true });
    return saved;
  }

  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) {
    throw new Error('Sign in as Super Admin before creating a branch.');
  }

  const created = await orgFetch<Record<string, unknown>>('/org/branches', {
    method: 'POST',
    body: JSON.stringify({
      name: input.name.trim(),
      city: input.city.trim(),
      image_url: (input.imageUrl || '').trim(),
      admins: admins.map((a) => ({
        name: a.name.trim(),
        username: a.username.trim().toLowerCase(),
        password: a.password,
      })),
    }),
  });

  const saved = normalizeCampusFromApi(created);
  writeCampusLocal(saved, 'create');
  return saved;
}

export async function updateBranch(
  id: string,
  patch: Partial<CampusBranch> & { admins?: BranchAdminAccount[] },
): Promise<CampusBranch> {
  const existing = getBranch(id);
  if (!existing) throw new Error('Branch not found.');

  const nextName = (patch.name ?? existing.name).trim();
  const nextCity = (patch.city ?? existing.city).trim();
  const nextImage = patch.imageUrl !== undefined ? String(patch.imageUrl || '').trim() : (existing.imageUrl || '');
  const admins = asAdmins({
    admins: patch.admins ?? existing.admins,
    admin: patch.admin ?? existing.admin,
    adminUsername: patch.adminUsername ?? existing.adminUsername,
    adminPassword: patch.adminPassword ?? existing.adminPassword,
  });
  if (!admins.length) throw new Error('At least one branch admin is required.');

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    const saved = normalizeBranch({
      ...existing,
      name: nextName,
      city: nextCity,
      imageUrl: nextImage || undefined,
      ...syncPrimary(admins),
    });
    writeCampusLocal(saved, 'update', { keepAdminPasswords: true });
    return saved;
  }

  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) {
    throw new Error('Sign in as Super Admin before updating a branch.');
  }

  const updated = await orgFetch<Record<string, unknown>>(`/org/branches/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify({
      name: nextName,
      city: nextCity,
      image_url: nextImage,
      admins: admins.map((a) => ({
        name: a.name.trim(),
        username: a.username.trim().toLowerCase(),
        password: a.password || '',
      })),
    }),
  });

  const saved = normalizeCampusFromApi(updated);
  writeCampusLocal(saved, 'update');
  return saved;
}

export async function setBranchActive(id: string, isActive: boolean): Promise<CampusBranch> {
  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    const existing = getBranch(id);
    if (!existing) throw new Error('Branch not found.');
    const saved = normalizeBranch({ ...existing, isActive });
    writeCampusLocal(saved, 'update', { keepAdminPasswords: true });
    return saved;
  }
  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) {
    throw new Error('Sign in as Super Admin before changing branch status.');
  }
  const updated = await orgFetch<Record<string, unknown>>(
    `/org/branches/${encodeURIComponent(id)}/status`,
    {
      method: 'PATCH',
      body: JSON.stringify({ is_active: isActive }),
    },
  );
  const saved = normalizeCampusFromApi(updated);
  writeCampusLocal(saved, 'update');
  return saved;
}

/** Soft-deactivate campus (no physical delete of academic history). */
export async function removeBranch(id: string): Promise<CampusBranch> {
  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    const existing = getBranch(id);
    if (!existing) throw new Error('Branch not found.');
    const saved = normalizeBranch({ ...existing, isActive: false });
    writeCampusLocal(saved, 'update', { keepAdminPasswords: true });
    return saved;
  }
  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) {
    throw new Error('Sign in as Super Admin before deactivating a branch.');
  }
  const res = await orgFetch<{ ok?: boolean; branch?: Record<string, unknown> }>(
    `/org/branches/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
  const raw = res.branch || (res as unknown as Record<string, unknown>);
  const saved = normalizeCampusFromApi(raw);
  writeCampusLocal(saved, 'update');
  return saved;
}

export function isAdminUsernameTaken(username: string, except?: { branchId?: string; username?: string }) {
  const key = username.trim().toLowerCase();
  return loadBranches().some((b) =>
    asAdmins(b).some((a) => {
      if (a.username.toLowerCase() !== key) return false;
      if (except?.branchId === b.id && except.username?.toLowerCase() === key) return false;
      return true;
    })
  );
}

export type BranchAdminLogin = { branch: CampusBranch; admin: BranchAdminAccount };

export function matchBranchAdminLogin(username: string, password: string): BranchAdminLogin | null {
  const key = username.trim().toLowerCase();
  for (const branch of loadBranches()) {
    const admin = asAdmins(branch).find((a) => a.username.toLowerCase() === key);
    if (admin && admin.password === password) return { branch, admin };
  }
  return null;
}

export function setCurrentBranchAdmin(id: string, username?: string) {
  sessionStorage.setItem(BRANCH_ADMIN_SESSION, id);
  if (username) sessionStorage.setItem(BRANCH_ADMIN_USER, username);
}

export function getCurrentBranchAdmin(): CampusBranch | null {
  const id = sessionStorage.getItem(BRANCH_ADMIN_SESSION);
  return id ? getBranch(id) || null : null;
}

export function getCurrentBranchAdminUser(): BranchAdminAccount | null {
  const campus = getCurrentBranchAdmin();
  if (!campus) return null;
  const username = (sessionStorage.getItem(BRANCH_ADMIN_USER) || campus.adminUsername || '').toLowerCase();
  return asAdmins(campus).find((a) => a.username.toLowerCase() === username) || asAdmins(campus)[0] || null;
}

export async function updateCurrentBranchAdminAccount(patch: {
  name?: string;
  username?: string;
  password?: string;
}): Promise<BranchAdminAccount | null> {
  const campus = getCurrentBranchAdmin();
  const current = getCurrentBranchAdminUser();
  if (!campus || !current) return null;
  const nextUsername = (patch.username ?? current.username).trim();
  if (nextUsername.length < 3) return null;
  if (isAdminUsernameTaken(nextUsername, { branchId: campus.id, username: current.username })) return null;

  const next: BranchAdminAccount = {
    name: (patch.name ?? current.name).trim() || current.name,
    username: nextUsername,
    password: (patch.password ?? current.password) || current.password,
  };

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    const admins = asAdmins(campus).map((a) =>
      a.username.toLowerCase() === current.username.toLowerCase() ? next : a
    );
    writeCampusLocal(normalizeBranch({ ...campus, ...syncPrimary(admins) }), 'update', {
      keepAdminPasswords: true,
    });
    setCurrentBranchAdmin(campus.id, next.username);
    return next;
  }

  const { orgFetch, getAccessToken, getAuthUser } = await import('@/lib/auth-api');
  if (!getAccessToken()) throw new Error('Session expired. Sign in again.');
  const me = getAuthUser();
  if (!me?.id || me.role !== 'branch_admin') {
    throw new Error('Only a signed-in branch admin can update this account.');
  }

  const body: Record<string, string> = {
    full_name: next.name,
    username: next.username.toLowerCase(),
  };
  if (patch.password && patch.password.length >= 6) {
    body.password = patch.password;
  }

  await orgFetch(`/org/users/${encodeURIComponent(me.id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });

  const saved: BranchAdminAccount = {
    name: body.full_name,
    username: body.username,
    password: '',
  };
  const admins = asAdmins(campus).map((a) =>
    a.username.toLowerCase() === current.username.toLowerCase() ? saved : { ...a, password: '' }
  );
  writeCampusLocal(
    normalizeBranch({
      ...campus,
      ...syncPrimary(admins),
    }),
    'update',
  );
  setCurrentBranchAdmin(campus.id, saved.username);
  return saved;
}

export function listAllBranchAdmins(): { branch: CampusBranch; admin: BranchAdminAccount }[] {
  return loadBranches().flatMap((branch) => asAdmins(branch).map((admin) => ({ branch, admin })));
}

export type BranchAdminUserRow = {
  id: string;
  name: string;
  username: string;
  email: string;
  branchId: string;
  branchName: string;
  isActive: boolean;
};

/** Super Admin: live Branch Admin directory from API (includes inactive). */
export async function fetchBranchAdminUsers(): Promise<BranchAdminUserRow[]> {
  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    return listAllBranchAdmins().map(({ branch, admin }) => ({
      id: `${branch.id}:${admin.username}`,
      name: admin.name,
      username: admin.username,
      email: '',
      branchId: branch.id,
      branchName: branch.name,
      isActive: branch.isActive !== false,
    }));
  }
  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) throw new Error('Sign in as Super Admin to list branch admins.');
  const res = await orgFetch<{ users: Array<Record<string, unknown>> }>('/org/users?role=branch_admin');
  const campuses = listBranches();
  return (res.users || []).map((u) => {
    const branchId = String(u.branch_id || u.branchId || '');
    const campus = campuses.find((c) => c.id === branchId);
    return {
      id: String(u.id || ''),
      name: String(u.full_name || u.name || u.username || ''),
      username: String(u.username || ''),
      email: String(u.email || ''),
      branchId,
      branchName: campus?.name || branchId || '—',
      isActive: u.is_active !== false && u.isActive !== false,
    };
  });
}

export async function createBranchAdmin(input: {
  name: string;
  username: string;
  password: string;
  email?: string;
  branchId: string;
}): Promise<BranchAdminUserRow> {
  const name = input.name.trim();
  const username = input.username.trim().toLowerCase();
  const password = input.password;
  const branchId = input.branchId.trim();
  if (!name) throw new Error('Name is required.');
  if (username.length < 3) throw new Error('Username must be at least 3 characters.');
  if (password.length < 6) throw new Error('Password must be at least 6 characters.');
  if (!branchId) throw new Error('Select a campus.');

  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    const campus = getBranch(branchId);
    if (!campus) throw new Error('Branch not found.');
    if (campus.isActive === false) throw new Error('Cannot assign users to a deactivated campus.');
    if (isAdminUsernameTaken(username)) throw new Error('Username already exists.');
    const admins = [...asAdmins(campus), { name, username, password }];
    writeCampusLocal(normalizeBranch({ ...campus, ...syncPrimary(admins) }), 'update', { keepAdminPasswords: true });
    return {
      id: `${branchId}:${username}`,
      name,
      username,
      email: (input.email || '').trim(),
      branchId,
      branchName: campus.name,
      isActive: true,
    };
  }

  const { orgFetch, getAccessToken } = await import('@/lib/auth-api');
  if (!getAccessToken()) throw new Error('Sign in as Super Admin before creating a branch admin.');
  const email = (input.email || '').trim().toLowerCase() || `${username}@${branchId.replace(/[^a-z0-9]+/gi, '-') || 'branch'}.fti.local`;
  const created = await orgFetch<Record<string, unknown>>('/org/users', {
    method: 'POST',
    body: JSON.stringify({
      role: 'branch_admin',
      email,
      username,
      password,
      full_name: name,
      branch_id: branchId,
    }),
  });
  const campus = getBranch(branchId);
  return {
    id: String(created.id || ''),
    name: String(created.full_name || created.name || name),
    username: String(created.username || username),
    email: String(created.email || email),
    branchId: String(created.branch_id || created.branchId || branchId),
    branchName: campus?.name || branchId,
    isActive: true,
  };
}

export async function setBranchAdminActive(userId: string, isActive: boolean): Promise<void> {
  const { DEMO_MODE } = await import('@/lib/demo-mode');
  if (DEMO_MODE) {
    // Demo rows are keyed by branch:username — soft status tracked via branch only.
    return;
  }
  await patchOrgUser(userId, { is_active: isActive });
}