/** Local attendance store for DEMO_MODE (teacher mark → student + branch admin views). */

export type AttendanceStatus = 'present' | 'absent' | 'late' | 'excused';

export type AttendanceMark = {
  status: AttendanceStatus;
  studentName: string;
};

export type AttendanceSession = {
  id: string;
  date: string;
  batch: string;
  teacherId: string;
  teacherName: string;
  branchId: string;
  notes: string;
  marks: Record<string, AttendanceMark>;
  updatedAt: string;
};

const STORE_KEY = 'ielts-demo-attendance-v1';
const SEED_KEY = 'ielts-demo-attendance-seeded-v1';

function uid(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function readSessions(): AttendanceSession[] {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as AttendanceSession[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeSessions(rows: AttendanceSession[]) {
  localStorage.setItem(STORE_KEY, JSON.stringify(rows));
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoIso(n: number) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

/** Seed a couple of Morning Batch A sessions so student/branch admin see data immediately. */
export function ensureDemoAttendance() {
  if (typeof window === 'undefined') return;
  if (readSessions().length > 0) return;
  localStorage.removeItem(SEED_KEY);

  let roster: { id: string; name: string; batch: string; branchId?: string }[] = [];
  try {
    const rows = JSON.parse(localStorage.getItem('ielts-entities-student') || '[]') as {
      id: string; fullName?: string; name?: string; batch?: string; branchId?: string;
    }[];
    roster = rows
      .filter((s) => (s.batch || '') === 'Morning Batch A')
      .slice(0, 8)
      .map((s) => ({
        id: s.id,
        name: s.fullName || s.name || 'Student',
        batch: s.batch || 'Morning Batch A',
        branchId: s.branchId || 'demo-gujranwala',
      }));
  } catch {
    roster = [];
  }
  if (!roster.length) {
    roster = [
      { id: 'demo-student-1', name: 'Demo Student', batch: 'Morning Batch A', branchId: 'demo-gujranwala' },
      { id: 'demo-student-2', name: 'Sara Ahmed', batch: 'Morning Batch A', branchId: 'demo-gujranwala' },
      { id: 'demo-student-3', name: 'Hassan Raza', batch: 'Morning Batch A', branchId: 'demo-gujranwala' },
    ];
  }

  const teacherId = sessionStorage.getItem('ielts-current-teacher-id') || 'demo-teacher-1';
  const branchId = roster[0]?.branchId || 'demo-gujranwala';
  const statuses: AttendanceStatus[] = ['present', 'present', 'late', 'absent', 'present', 'excused', 'present', 'present'];

  const makeMarks = (offset: number) => {
    const marks: Record<string, AttendanceMark> = {};
    roster.forEach((s, i) => {
      marks[s.id] = {
        status: statuses[(i + offset) % statuses.length],
        studentName: s.name,
      };
    });
    return marks;
  };

  const seeded: AttendanceSession[] = [
    {
      id: uid('att'),
      date: daysAgoIso(1),
      batch: 'Morning Batch A',
      teacherId,
      teacherName: 'Demo Teacher',
      branchId,
      notes: 'Regular class',
      marks: makeMarks(0),
      updatedAt: new Date().toISOString(),
    },
    {
      id: uid('att'),
      date: daysAgoIso(3),
      batch: 'Morning Batch A',
      teacherId,
      teacherName: 'Demo Teacher',
      branchId,
      notes: '',
      marks: makeMarks(2),
      updatedAt: new Date().toISOString(),
    },
  ];
  writeSessions(seeded);
  localStorage.setItem(SEED_KEY, '1');
}

export function demoListAttendanceSessions(filter?: {
  teacherId?: string;
  batch?: string;
  branchId?: string;
}): AttendanceSession[] {
  ensureDemoAttendance();
  let rows = readSessions();
  if (filter?.teacherId) rows = rows.filter((s) => s.teacherId === filter.teacherId);
  if (filter?.batch) rows = rows.filter((s) => s.batch === filter.batch);
  if (filter?.branchId) rows = rows.filter((s) => !s.branchId || s.branchId === filter.branchId);
  return rows.sort((a, b) => b.date.localeCompare(a.date) || b.updatedAt.localeCompare(a.updatedAt));
}

export function demoGetAttendanceSession(id: string): AttendanceSession | null {
  ensureDemoAttendance();
  return readSessions().find((s) => s.id === id) || null;
}

export function demoFindSession(date: string, batch: string, teacherId: string): AttendanceSession | null {
  ensureDemoAttendance();
  return (
    readSessions().find((s) => s.date === date && s.batch === batch && s.teacherId === teacherId) || null
  );
}

export function demoSaveAttendanceSession(input: {
  id?: string;
  date: string;
  batch: string;
  teacherId: string;
  teacherName: string;
  branchId: string;
  notes?: string;
  marks: Record<string, AttendanceMark>;
}): AttendanceSession {
  ensureDemoAttendance();
  const rows = readSessions();
  const existing =
    (input.id && rows.find((s) => s.id === input.id)) ||
    rows.find((s) => s.date === input.date && s.batch === input.batch && s.teacherId === input.teacherId);

  const next: AttendanceSession = {
    id: existing?.id || uid('att'),
    date: input.date || todayIso(),
    batch: input.batch,
    teacherId: input.teacherId,
    teacherName: input.teacherName,
    branchId: input.branchId || existing?.branchId || 'demo-gujranwala',
    notes: input.notes || '',
    marks: input.marks,
    updatedAt: new Date().toISOString(),
  };

  const without = rows.filter((s) => s.id !== next.id);
  writeSessions([next, ...without]);
  return next;
}

export function demoStudentAttendance(studentId: string): {
  studentId: string;
  records: { date: string; batch: string; status: AttendanceStatus; teacherName: string; sessionId: string; notes: string }[];
  summary: { present: number; absent: number; late: number; excused: number; total: number; rate: number };
} {
  ensureDemoAttendance();
  const records = readSessions()
    .filter((s) => s.marks[studentId])
    .map((s) => ({
      date: s.date,
      batch: s.batch,
      status: s.marks[studentId].status,
      teacherName: s.teacherName,
      sessionId: s.id,
      notes: s.notes,
    }))
    .sort((a, b) => b.date.localeCompare(a.date));

  const summary = { present: 0, absent: 0, late: 0, excused: 0, total: records.length, rate: 0 };
  for (const r of records) summary[r.status] += 1;
  summary.rate = summary.total
    ? Math.round(((summary.present + summary.late * 0.5 + summary.excused * 0.75) / summary.total) * 100)
    : 0;
  return { studentId, records, summary };
}

export function demoBranchAttendanceOverview(branchId: string): {
  sessions: AttendanceSession[];
  students: {
    studentId: string;
    studentName: string;
    batch: string;
    present: number;
    absent: number;
    late: number;
    excused: number;
    total: number;
    rate: number;
  }[];
} {
  ensureDemoAttendance();
  const sessions = demoListAttendanceSessions({ branchId });
  const byStudent = new Map<
    string,
    { studentId: string; studentName: string; batch: string; present: number; absent: number; late: number; excused: number; total: number }
  >();

  for (const session of sessions) {
    for (const [studentId, mark] of Object.entries(session.marks)) {
      const row = byStudent.get(studentId) || {
        studentId,
        studentName: mark.studentName,
        batch: session.batch,
        present: 0,
        absent: 0,
        late: 0,
        excused: 0,
        total: 0,
      };
      row[mark.status] += 1;
      row.total += 1;
      row.batch = session.batch;
      byStudent.set(studentId, row);
    }
  }

  const students = [...byStudent.values()]
    .map((s) => ({
      ...s,
      rate: s.total ? Math.round(((s.present + s.late * 0.5 + s.excused * 0.75) / s.total) * 100) : 0,
    }))
    .sort((a, b) => a.studentName.localeCompare(b.studentName));

  return { sessions, students };
}
