/** Local mock-assignment store used when DEMO_MODE is on (no live JWT). */

import type { MockAssignment, MockStudentRow } from '@/lib/mocks-api';
import { demoBusPublish } from '@/lib/demo-mock-bus';

const STORE_KEY = 'ielts-demo-mock-assignments-v1';
const EVENTS_KEY = 'ielts-demo-mock-events-v1';
const ANSWERS_KEY = 'ielts-demo-mock-answers-v1';
const SEED_FLAG = 'ielts-demo-mocks-seeded-v1';

type DemoSlot = MockStudentRow & { assignment_id: string; paused?: boolean };

export type DemoAnswerRow = { question_key: string; value: unknown };
export type DemoAttemptWork = {
  attempt_id: string;
  assignment_id: string;
  student_id: string;
  student_name: string;
  status: string;
  submitted_at?: string | null;
  answers: DemoAnswerRow[];
  writing_band?: number | null;
  listening_band?: number | null;
  reading_band?: number | null;
  speaking_band?: number | null;
  overall_band?: number | null;
  writing_feedback?: string | null;
  published?: boolean;
};

type DemoStore = {
  assignments: MockAssignment[];
  slots: DemoSlot[];
};

type DemoEvent = { id: string; attempt_id: string; event_type: string; timestamp: string };

const WARNING_EVENTS = new Set([
  'FULLSCREEN_EXIT',
  'TAB_HIDDEN',
  'WINDOW_BLUR',
  'SCREEN_SHARE_STOPPED',
  'DISCONNECTED',
]);

function readEvents(): DemoEvent[] {
  try {
    const raw = localStorage.getItem(EVENTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as DemoEvent[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeEvents(events: DemoEvent[]) {
  localStorage.setItem(EVENTS_KEY, JSON.stringify(events.slice(0, 400)));
}

function readAnswers(): Record<string, DemoAnswerRow[]> {
  try {
    const raw = localStorage.getItem(ANSWERS_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, DemoAnswerRow[]>;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeAnswers(map: Record<string, DemoAnswerRow[]>) {
  localStorage.setItem(ANSWERS_KEY, JSON.stringify(map));
}

function pushEvent(attemptId: string, eventType: string) {
  if (!attemptId || !eventType) return;
  const events = readEvents();
  events.unshift({
    id: uid('evt'),
    attempt_id: attemptId,
    event_type: eventType,
    timestamp: new Date().toISOString(),
  });
  writeEvents(events);
}

function publishSlot(slot: DemoSlot, eventType?: string) {
  demoBusPublish({
    assignmentId: slot.assignment_id,
    studentId: slot.student_id,
    student_id: slot.student_id,
    slotId: slot.id,
    status: slot.status,
    current_section: slot.current_section,
    currentSection: slot.current_section,
    current_question: slot.current_question,
    currentQuestion: slot.current_question,
    question_total: slot.question_total,
    remaining_seconds: slot.remaining_seconds,
    remainingSeconds: slot.remaining_seconds,
    warning_count: slot.warning_count,
    warningCount: slot.warning_count,
    screen_sharing: slot.screen_sharing,
    screenSharing: slot.screen_sharing,
    connection: slot.connection,
    last_warning: slot.last_warning,
    lastWarning: slot.last_warning,
    eventType: eventType || 'HEARTBEAT',
    event_type: eventType || 'HEARTBEAT',
    studentName: slot.student_name,
    student_name: slot.student_name,
    attempt_id: slot.attempt_id,
    attemptId: slot.attempt_id,
    paused: slot.paused,
  });
}

function readStore(): DemoStore {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) return { assignments: [], slots: [] };
    const parsed = JSON.parse(raw) as DemoStore;
    return {
      assignments: Array.isArray(parsed.assignments) ? parsed.assignments : [],
      slots: Array.isArray(parsed.slots) ? parsed.slots : [],
    };
  } catch {
    return { assignments: [], slots: [] };
  }
}

function writeStore(store: DemoStore) {
  localStorage.setItem(STORE_KEY, JSON.stringify(store));
}

function uid(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function inboxStatus(availableAt?: string | null, deadlineAt?: string | null): string {
  const now = Date.now();
  const available = availableAt ? new Date(availableAt).getTime() : 0;
  const deadline = deadlineAt ? new Date(deadlineAt).getTime() : Number.POSITIVE_INFINITY;
  if (available && now < available) return 'upcoming';
  if (Number.isFinite(deadline) && now > deadline) return 'closed';
  return 'available';
}

export function demoCreateMockAssignment(body: Record<string, unknown>): MockAssignment {
  const store = readStore();
  const students = (Array.isArray(body.students) ? body.students : []) as { id: string; name: string }[];
  const id = uid('mock');
  const duration = Number(body.duration_minutes || 60);
  const available_at = String(body.available_at || new Date().toISOString());
  const deadline_at = body.deadline_at ? String(body.deadline_at) : null;
  const assignment: MockAssignment = {
    id,
    title: String(body.title || 'Demo mock'),
    mock_type: String(body.mock_type || 'writing'),
    ielts_type: String(body.ielts_type || 'academic'),
    assign_mode: String(body.assign_mode || 'batch'),
    batch_id: body.batch_id ? String(body.batch_id) : null,
    batch_label: body.batch_label ? String(body.batch_label) : null,
    available_at,
    deadline_at,
    duration_minutes: duration,
    attempts_allowed: Number(body.attempts_allowed || 1),
    secure_mode: Boolean(body.secure_mode),
    screen_monitoring: Boolean(body.screen_monitoring),
    fullscreen_required: Boolean(body.fullscreen_required),
    allow_late_start: Boolean(body.allow_late_start),
    auto_submit: body.auto_submit !== false,
    paper_source: String(body.paper_source || 'manual'),
    result_mode: String(body.result_mode || 'teacher'),
    instructions: String(body.instructions || ''),
    assigned: students.length,
    submitted: 0,
    inbox_status: inboxStatus(available_at, deadline_at),
  };

  // Keep paper payload for student briefing/room in session for this assignment.
  sessionStorage.setItem(`demo-mock-paper-${id}`, JSON.stringify(body.paper_refs || {}));
  sessionStorage.setItem(`demo-mock-meta-${id}`, JSON.stringify({
    teacher_name: body.teacher_name || 'Teacher',
    paper_refs: body.paper_refs || {},
  }));

  const slots: DemoSlot[] = students.map((s) => ({
    id: uid('slot'),
    assignment_id: id,
    student_id: s.id,
    student_name: s.name,
    status: 'assigned',
    duration_minutes: duration,
    cancelled: false,
    flagged: false,
    warning_count: 0,
    remaining_seconds: duration * 60,
    livekit_identity: `student-${s.id}`,
  }));

  store.assignments = [assignment, ...store.assignments];
  store.slots = [...slots, ...store.slots];
  writeStore(store);
  return assignment;
}

/** Seed one assigned mock + sample solved work so Assigned page is never empty in demo. */
export function ensureDemoAssignedMocks() {
  if (typeof window === 'undefined') return;
  const store = readStore();
  if (store.assignments.length > 0) return;
  // Empty store: always reseed (ignore stale flag from earlier sessions).
  localStorage.removeItem(SEED_FLAG);

  const roster = (() => {
    try {
      const rows = JSON.parse(localStorage.getItem('ielts-entities-student') || '[]') as {
        id: string; fullName?: string; name?: string; batch?: string;
      }[];
      return rows
        .filter((s) => (s.batch || '') === 'Morning Batch A')
        .slice(0, 8)
        .map((s) => ({ id: s.id, name: s.fullName || s.name || 'Student' }));
    } catch {
      return [] as { id: string; name: string }[];
    }
  })();

  if (!roster.length) {
    roster.push(
      { id: 'demo-student-1', name: 'Demo Student' },
      { id: 'demo-student-2', name: 'Sara Ahmed' },
      { id: 'demo-student-3', name: 'Hassan Raza' },
    );
  }

  const created = demoCreateMockAssignment({
    title: 'Morning Batch A · Academic Writing Mock',
    mock_type: 'writing',
    ielts_type: 'academic',
    assign_mode: 'batch',
    batch_id: 'demo-batch-1',
    batch_label: 'Morning Batch A',
    available_at: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
    duration_minutes: 60,
    attempts_allowed: 1,
    secure_mode: false,
    screen_monitoring: false,
    fullscreen_required: false,
    allow_late_start: true,
    auto_submit: true,
    paper_source: 'manual',
    result_mode: 'teacher',
    instructions: 'Write at least 250 words. Discuss both views and give your opinion.',
    teacher_name: 'Demo Teacher',
    students: roster,
    paper_refs: {
      prompt:
        'Some people believe that unpaid community service should be a compulsory part of high school programmes. To what extent do you agree or disagree?',
    },
  });

  // Mark first student as submitted with full answer work for the teacher to review here.
  const next = readStore();
  const first = next.slots.find((s) => s.assignment_id === created.id && s.student_id === roster[0].id);
  if (first) {
    const attemptId = uid('attempt');
    first.status = 'submitted';
    first.attempt_id = attemptId;
    first.connection = 'stable';
    first.screen_sharing = false;
    first.current_section = 'writing';
    first.current_question = 1;
    first.question_total = 2;
    first.warning_count = 0;
    first.writing_band = null;
    first.published = false;
    const assignment = next.assignments.find((a) => a.id === created.id);
    if (assignment) assignment.submitted = 1;
    writeStore(next);
    sessionStorage.setItem(
      `demo-mock-attempt-${attemptId}`,
      JSON.stringify({
        attempt_id: attemptId,
        assignment_id: created.id,
        student_id: first.student_id,
        remaining_seconds: 0,
        title: created.title,
      }),
    );
    const answers = readAnswers();
    answers[attemptId] = [
      {
        question_key: 'task_response',
        value:
          'I partly agree that unpaid community service can benefit students. It builds responsibility and empathy, and helps young people understand local needs. However, making it compulsory for every high school programme may create pressure for students who already struggle with academic workload or part-time jobs.\n\nA better approach is to offer structured volunteering options with flexible hours, clear learning goals, and recognition on school records. That way students gain real experience without turning service into a punishment. Schools should also partner with charities so the work is meaningful rather than token.\n\nIn conclusion, community service is valuable, but it works best when encouraged and well designed, not forced on every learner in the same way.',
      },
      { question_key: 'word_count', value: '148' },
    ];
    writeAnswers(answers);
    pushEvent(attemptId, 'TEST_SUBMITTED');
  }

  // Second student in progress
  const second = next.slots.find((s) => s.assignment_id === created.id && s.student_id === roster[1]?.id);
  if (second) {
    const attemptId = uid('attempt');
    second.status = 'in_progress';
    second.attempt_id = attemptId;
    second.connection = 'stable';
    second.remaining_seconds = 42 * 60;
    second.current_section = 'writing';
    second.current_question = 1;
    writeStore(next);
    const answers = readAnswers();
    answers[attemptId] = [
      { question_key: 'task_response', value: 'Draft in progress — I think unpaid service can help society because…' },
    ];
    writeAnswers(answers);
  }

  localStorage.setItem(SEED_FLAG, '1');
}

export function demoListTeacherMocks(): { assignments: MockAssignment[] } {
  ensureDemoAssignedMocks();
  return { assignments: readStore().assignments };
}

export function demoSaveAnswers(attemptId: string, body: Record<string, unknown>) {
  const incoming = Array.isArray(body.answers) ? (body.answers as DemoAnswerRow[]) : [];
  const map = readAnswers();
  const prev = map[attemptId] || [];
  const byKey = new Map<string, DemoAnswerRow>();
  for (const row of prev) byKey.set(row.question_key, row);
  for (const row of incoming) {
    if (!row?.question_key) continue;
    byKey.set(String(row.question_key), { question_key: String(row.question_key), value: row.value });
  }
  map[attemptId] = [...byKey.values()];
  writeAnswers(map);

  // Mirror progress onto the student slot for Assigned page.
  try {
    const raw = sessionStorage.getItem(`demo-mock-attempt-${attemptId}`);
    if (raw) {
      const parsed = JSON.parse(raw) as { assignment_id: string; student_id: string };
      const store = readStore();
      store.slots = store.slots.map((s) =>
        s.assignment_id === parsed.assignment_id && s.student_id === parsed.student_id
          ? {
              ...s,
              current_section: body.current_section ? String(body.current_section) : s.current_section,
              last_seen_at: new Date().toISOString(),
            }
          : s,
      );
      writeStore(store);
    }
  } catch {
    /* ignore */
  }
  return { ok: true };
}

export function demoGetAttemptWork(attemptId: string): DemoAttemptWork {
  const store = readStore();
  const slot = store.slots.find((s) => s.attempt_id === attemptId);
  if (!slot) throw new Error('Attempt not found.');
  const answers = readAnswers()[attemptId] || [];
  return {
    attempt_id: attemptId,
    assignment_id: slot.assignment_id,
    student_id: slot.student_id,
    student_name: slot.student_name,
    status: slot.status,
    submitted_at: slot.status === 'submitted' ? slot.last_seen_at || new Date().toISOString() : null,
    answers,
    writing_band: slot.writing_band ?? null,
    listening_band: slot.listening_band ?? null,
    reading_band: slot.reading_band ?? null,
    speaking_band: slot.speaking_band ?? null,
    overall_band: slot.overall_band ?? null,
    writing_feedback: (slot as DemoSlot & { writing_feedback?: string }).writing_feedback ?? null,
    published: Boolean(slot.published),
  };
}

export function demoReviewAttempt(attemptId: string, body: Record<string, unknown>) {
  const store = readStore();
  const slot = store.slots.find((s) => s.attempt_id === attemptId);
  if (!slot) throw new Error('Attempt not found.');
  const writing = body.writing_band != null ? Number(body.writing_band) : slot.writing_band;
  const updated: DemoSlot & { writing_feedback?: string } = {
    ...slot,
    writing_band: writing ?? null,
    listening_band: body.listening_band != null ? Number(body.listening_band) : slot.listening_band,
    reading_band: body.reading_band != null ? Number(body.reading_band) : slot.reading_band,
    speaking_band: body.speaking_band != null ? Number(body.speaking_band) : slot.speaking_band,
    overall_band: body.overall_band != null ? Number(body.overall_band) : writing ?? slot.overall_band,
    writing_feedback: body.writing_feedback != null ? String(body.writing_feedback) : undefined,
    published: body.publish !== false,
    status: 'submitted',
  };
  store.slots = store.slots.map((s) => (s.attempt_id === attemptId ? updated : s));
  writeStore(store);
  return { ok: true };
}

export function demoListMockStudents(assignmentId: string): {
  assignment: MockAssignment;
  students: MockStudentRow[];
} {
  const store = readStore();
  const assignment = store.assignments.find((a) => a.id === assignmentId);
  if (!assignment) throw new Error('Mock assignment not found.');
  const students = store.slots
    .filter((s) => s.assignment_id === assignmentId)
    .map(({ assignment_id: _a, ...row }) => row);
  return { assignment, students };
}

export function demoStudentInbox(studentId: string): { items: MockAssignment[] } {
  if (!studentId) return { items: [] };
  const store = readStore();
  const myIds = new Set(
    store.slots.filter((s) => s.student_id === studentId).map((s) => s.assignment_id),
  );
  const items = store.assignments
    .filter((a) => myIds.has(a.id))
    .map((a) => {
      const slot = store.slots.find((s) => s.assignment_id === a.id && s.student_id === studentId);
      let status = inboxStatus(a.available_at, a.deadline_at);
      if (slot?.status === 'in_progress') status = 'in_progress';
      if (slot?.status === 'submitted') status = 'completed';
      return {
        ...a,
        inbox_status: status,
        slot_id: slot?.id,
        attempt_id: slot?.attempt_id || null,
      };
    });
  return { items };
}

export function demoStudentDetail(id: string, studentId: string): MockAssignment {
  const store = readStore();
  const slot = store.slots.find((s) => s.assignment_id === id && s.student_id === studentId);
  const assignment = store.assignments.find((a) => a.id === id);
  if (!slot || !assignment) throw new Error('This mock is not assigned to you.');
  let status = inboxStatus(assignment.available_at, assignment.deadline_at);
  if (slot.status === 'in_progress') status = 'in_progress';
  if (slot.status === 'submitted') status = 'completed';
  return {
    ...assignment,
    inbox_status: status,
    slot_id: slot.id,
    attempt_id: slot.attempt_id || null,
  };
}

export function demoStartAttempt(assignmentId: string, studentId: string) {
  const detail = demoStudentDetail(assignmentId, studentId);
  const attemptId = uid('attempt');
  let paper_refs: Record<string, unknown> = {};
  try {
    paper_refs = JSON.parse(sessionStorage.getItem(`demo-mock-paper-${assignmentId}`) || '{}') as Record<string, unknown>;
  } catch {
    paper_refs = {};
  }
  sessionStorage.setItem(
    `demo-mock-attempt-${attemptId}`,
    JSON.stringify({
      attempt_id: attemptId,
      assignment_id: assignmentId,
      student_id: studentId,
      remaining_seconds: (detail.duration_minutes || 60) * 60,
      paper_refs,
      title: detail.title,
    }),
  );
  const store = readStore();
  let started: DemoSlot | null = null;
  store.slots = store.slots.map((s) => {
    if (s.assignment_id !== assignmentId || s.student_id !== studentId) return s;
    started = {
      ...s,
      status: 'in_progress',
      attempt_id: attemptId,
      remaining_seconds: (detail.duration_minutes || 60) * 60,
      connection: 'stable',
      screen_sharing: Boolean(detail.screen_monitoring),
      current_section: detail.mock_type || 'writing',
      current_question: 1,
      question_total: detail.mock_type === 'writing' || detail.mock_type === 'speaking' ? 2 : 40,
      last_seen_at: new Date().toISOString(),
      paused: false,
    };
    return started;
  });
  writeStore(store);
  if (started) {
    pushEvent(attemptId, 'ATTEMPT_STARTED');
    publishSlot(started, 'ATTEMPT_STARTED');
  }
  const mockType = detail.mock_type || 'writing';
  return {
    attempt_id: attemptId,
    skill: { kind: mockType === 'writing' ? 'demo-writing' : mockType },
    remaining_seconds: (detail.duration_minutes || 60) * 60,
    screen_monitoring: Boolean(detail.screen_monitoring),
    fullscreen_required: false,
    auto_submit: detail.auto_submit !== false,
    title: detail.title,
    assignment_id: assignmentId,
    secure_mode: Boolean(detail.secure_mode),
    current_section: mockType,
  };
}

export function demoSubmitAttempt(attemptId: string) {
  try {
    const raw = sessionStorage.getItem(`demo-mock-attempt-${attemptId}`);
    if (!raw) return { ok: true };
    const parsed = JSON.parse(raw) as { assignment_id: string; student_id: string };
    const store = readStore();
    let submittedSlot: DemoSlot | null = null;
    store.slots = store.slots.map((s) => {
      if (s.assignment_id !== parsed.assignment_id || s.student_id !== parsed.student_id) return s;
      submittedSlot = { ...s, status: 'submitted', connection: 'stable', screen_sharing: false };
      return submittedSlot;
    });
    const assignment = store.assignments.find((a) => a.id === parsed.assignment_id);
    if (assignment) {
      assignment.submitted = store.slots.filter(
        (s) => s.assignment_id === parsed.assignment_id && s.status === 'submitted',
      ).length;
    }
    writeStore(store);
    pushEvent(attemptId, 'TEST_SUBMITTED');
    if (submittedSlot) publishSlot(submittedSlot, 'TEST_SUBMITTED');
  } catch {
    /* ignore */
  }
  return { ok: true };
}

export function demoHeartbeat(attemptId: string, body: Record<string, unknown> = {}) {
  try {
    const raw = sessionStorage.getItem(`demo-mock-attempt-${attemptId}`);
    if (!raw) return { ok: true, remaining_seconds: 0, status: 'submitted' };
    const parsed = JSON.parse(raw) as {
      remaining_seconds?: number;
      assignment_id: string;
      student_id: string;
      paused?: boolean;
    };
    const eventType = String(body.event_type || body.eventType || 'HEARTBEAT');
    const store = readStore();
    const slot = store.slots.find(
      (s) => s.assignment_id === parsed.assignment_id && s.student_id === parsed.student_id,
    );
    if (!slot) return { ok: true, remaining_seconds: 0, status: 'submitted' };

    const paused = Boolean(slot.paused);
    let next = parsed.remaining_seconds ?? slot.remaining_seconds ?? 0;
    if (!paused && eventType === 'HEARTBEAT') {
      next = Math.max(0, next - 8);
    }
    if (typeof body.remaining_seconds === 'number') next = body.remaining_seconds;

    parsed.remaining_seconds = next;
    sessionStorage.setItem(`demo-mock-attempt-${attemptId}`, JSON.stringify(parsed));

    let warningCount = slot.warning_count || 0;
    let lastWarning = slot.last_warning || null;
    if (WARNING_EVENTS.has(eventType)) {
      warningCount += 1;
      lastWarning = eventType;
      pushEvent(attemptId, eventType);
    } else if (eventType !== 'HEARTBEAT') {
      pushEvent(attemptId, eventType);
    }

    const updated: DemoSlot = {
      ...slot,
      status: eventType === 'DISCONNECTED' ? 'disconnected' : slot.status === 'submitted' ? 'submitted' : 'in_progress',
      remaining_seconds: next,
      warning_count: warningCount,
      last_warning: lastWarning,
      last_seen_at: new Date().toISOString(),
      connection: eventType === 'DISCONNECTED' ? 'disconnected' : 'stable',
      current_section: body.current_section
        ? String(body.current_section)
        : body.currentSection
          ? String(body.currentSection)
          : slot.current_section || 'writing',
      current_question:
        typeof body.current_question === 'number'
          ? body.current_question
          : typeof body.currentQuestion === 'number'
            ? body.currentQuestion
            : slot.current_question || 1,
      screen_sharing:
        eventType === 'SCREEN_SHARE_STARTED'
          ? true
          : eventType === 'SCREEN_SHARE_STOPPED'
            ? false
            : slot.screen_sharing ?? true,
      paused,
    };

    store.slots = store.slots.map((s) => (s.id === slot.id ? updated : s));
    writeStore(store);
    publishSlot(updated, eventType);

    return {
      ok: true,
      remaining_seconds: next,
      status: updated.status,
      warning_count: warningCount,
      paused,
    };
  } catch {
    return { ok: true, remaining_seconds: 0 };
  }
}

export function demoPatchMockStudent(slotId: string, body: Record<string, unknown>) {
  const store = readStore();
  const slot = store.slots.find((s) => s.id === slotId);
  if (!slot) throw new Error('Student slot not found.');
  const addMinutes = Number(body.add_minutes || 0);
  const nextSeconds = Math.max(0, (slot.remaining_seconds || 0) + addMinutes * 60);
  const updated: DemoSlot = { ...slot, remaining_seconds: nextSeconds };
  if (slot.attempt_id) {
    try {
      const raw = sessionStorage.getItem(`demo-mock-attempt-${slot.attempt_id}`);
      if (raw) {
        const parsed = JSON.parse(raw) as { remaining_seconds?: number };
        parsed.remaining_seconds = nextSeconds;
        sessionStorage.setItem(`demo-mock-attempt-${slot.attempt_id}`, JSON.stringify(parsed));
      }
    } catch {
      /* ignore */
    }
  }
  store.slots = store.slots.map((s) => (s.id === slotId ? updated : s));
  writeStore(store);
  publishSlot(updated, 'TIME_ADDED');
  return { ok: true };
}

export function demoMockStudentAction(slotId: string, body: Record<string, unknown>) {
  const store = readStore();
  const slot = store.slots.find((s) => s.id === slotId);
  if (!slot) throw new Error('Student slot not found.');
  const action = String(body.action || '');
  let updated: DemoSlot = { ...slot };

  if (action === 'pause') {
    updated = { ...updated, paused: true };
  } else if (action === 'resume') {
    updated = { ...updated, paused: false };
  } else if (action === 'flag') {
    updated = { ...updated, flagged: true };
  } else if (action === 'force_submit') {
    updated = { ...updated, status: 'submitted', screen_sharing: false, paused: false };
  } else if (action === 'message') {
    demoBusPublish({
      assignmentId: slot.assignment_id,
      studentId: slot.student_id,
      eventType: 'TEACHER_MESSAGE',
      message: String(body.message || ''),
    });
    if (slot.attempt_id) pushEvent(slot.attempt_id, 'TEACHER_MESSAGE');
    return { ok: true };
  }

  store.slots = store.slots.map((s) => (s.id === slotId ? updated : s));
  if (action === 'force_submit') {
    const assignment = store.assignments.find((a) => a.id === slot.assignment_id);
    if (assignment) {
      assignment.submitted = store.slots.filter(
        (s) => s.assignment_id === slot.assignment_id && s.status === 'submitted',
      ).length;
    }
  }
  writeStore(store);
  const eventType =
    action === 'pause'
      ? 'TEACHER_PAUSE'
      : action === 'resume'
        ? 'TEACHER_RESUME'
        : action === 'flag'
          ? 'TEACHER_FLAG'
          : action === 'force_submit'
            ? 'FORCE_SUBMIT'
            : action.toUpperCase();
  if (slot.attempt_id) pushEvent(slot.attempt_id, eventType);
  publishSlot(updated, eventType);
  return { ok: true };
}

export function demoMockEvents(attemptId: string) {
  return {
    events: readEvents()
      .filter((e) => e.attempt_id === attemptId)
      .map((e) => ({ id: e.id, event_type: e.event_type, timestamp: e.timestamp })),
  };
}

export function demoMonitor(assignmentId: string) {
  const { assignment, students } = demoListMockStudents(assignmentId);
  return {
    assignment,
    students,
    totals: {
      total: students.length,
      active: students.filter((s) => s.status === 'in_progress').length,
      not_started: students.filter((s) => s.status === 'assigned' || s.status === 'available' || s.status === 'upcoming').length,
      disconnected: students.filter((s) => s.status === 'disconnected').length,
      submitted: students.filter((s) => s.status === 'submitted' || s.status === 'completed').length,
      warnings: students.reduce((sum, s) => sum + (s.warning_count || 0), 0),
    },
    screen_note: 'Demo live monitoring — student status, timer, and warnings update live. Screen video needs LiveKit in production.',
    livekit: { configured: false },
  };
}

export function demoListLibrary(): { items: { id: string; title: string; mock_type: string; ielts_type: string; question_count: number; duration_minutes: number }[] } {
  return {
    items: [
      { id: 'demo-lib-writing', title: 'Demo Academic Writing Mock', mock_type: 'writing', ielts_type: 'academic', question_count: 2, duration_minutes: 60 },
      { id: 'demo-lib-reading', title: 'Demo Academic Reading Mock', mock_type: 'reading', ielts_type: 'academic', question_count: 40, duration_minutes: 60 },
      { id: 'demo-lib-listening', title: 'Demo Listening Mock', mock_type: 'listening', ielts_type: 'academic', question_count: 40, duration_minutes: 40 },
      { id: 'demo-lib-speaking', title: 'Demo Speaking Mock', mock_type: 'speaking', ielts_type: 'academic', question_count: 3, duration_minutes: 15 },
    ],
  };
}
