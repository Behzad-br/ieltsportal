import { useEffect, useMemo, useState } from 'react';
import { Badge, Button, EmptyState, SectionTitle, StatusBadge, Toast } from '@/components/ui-kit';
import {
  getAttendanceForDate,
  listTeacherAttendance,
  saveAttendanceSession,
  type AttendanceMark,
  type AttendanceSession,
  type AttendanceStatus,
} from '@/lib/attendance-api';
import { assignedBatchIdsForTeacher, getCurrentTeacher, listCampusBatches, listTeacherRoster } from '@/lib/mock-api';

const STATUS_OPTIONS: { id: AttendanceStatus; label: string }[] = [
  { id: 'present', label: 'Present' },
  { id: 'late', label: 'Late' },
  { id: 'absent', label: 'Absent' },
  { id: 'excused', label: 'Excused' },
];

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

export default function TeacherAttendance() {
  const teacher = getCurrentTeacher();
  const roster = listTeacherRoster();
  const batches = useMemo(() => {
    const ids = new Set(assignedBatchIdsForTeacher(teacher.id));
    const campus = listCampusBatches(teacher.branchId);
    const named = campus.filter((b) => ids.has(b.id) || b.teacherId === teacher.id || (teacher.batches || '').includes(b.name));
    const fromRoster = Array.from(new Set(roster.map((s) => s.batch).filter(Boolean)));
    const names = named.map((b) => b.name);
    return Array.from(new Set([...names, ...fromRoster, 'Morning Batch A']));
  }, [teacher, roster]);

  const [batch, setBatch] = useState(batches[0] || 'Morning Batch A');
  const [date, setDate] = useState(todayIso());
  const [notes, setNotes] = useState('');
  const [marks, setMarks] = useState<Record<string, AttendanceMark>>({});
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [history, setHistory] = useState<AttendanceSession[]>([]);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');
  const [error, setError] = useState('');

  const batchStudents = roster.filter((s) => s.batch === batch && !String(s.status || '').toLowerCase().includes('removed'));

  const loadHistory = () => {
    listTeacherAttendance(batch)
      .then((d) => setHistory(d.sessions || []))
      .catch(() => setHistory([]));
  };

  useEffect(() => {
    if (!batch && batches[0]) setBatch(batches[0]);
  }, [batches, batch]);

  useEffect(() => {
    loadHistory();
  }, [batch]);

  useEffect(() => {
    let cancelled = false;
    getAttendanceForDate(date, batch)
      .then((d) => {
        if (cancelled) return;
        if (d.session) {
          setSessionId(d.session.id);
          setNotes(d.session.notes || '');
          setMarks({ ...d.session.marks });
          return;
        }
        setSessionId(undefined);
        setNotes('');
        const next: Record<string, AttendanceMark> = {};
        for (const s of batchStudents) {
          next[s.id] = { status: 'present', studentName: s.name };
        }
        setMarks(next);
      })
      .catch(() => {
        if (cancelled) return;
        setSessionId(undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [date, batch]);

  // Keep marks in sync when batch roster changes and no saved session for this date.
  useEffect(() => {
    if (sessionId) return;
    setMarks((prev) => {
      const next = { ...prev };
      for (const s of batchStudents) {
        if (!next[s.id]) next[s.id] = { status: 'present', studentName: s.name };
        else next[s.id] = { ...next[s.id], studentName: s.name };
      }
      return next;
    });
  }, [batch, sessionId, batchStudents.length]);

  const setStatus = (studentId: string, studentName: string, status: AttendanceStatus) => {
    setMarks((prev) => ({ ...prev, [studentId]: { status, studentName } }));
  };

  const markAll = (status: AttendanceStatus) => {
    const next: Record<string, AttendanceMark> = {};
    for (const s of batchStudents) next[s.id] = { status, studentName: s.name };
    setMarks(next);
  };

  const save = async () => {
    if (!batchStudents.length) {
      setError('Is batch mein koi student nahi hai.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const payload: Record<string, AttendanceMark> = {};
      for (const s of batchStudents) {
        payload[s.id] = marks[s.id] || { status: 'present', studentName: s.name };
      }
      const saved = await saveAttendanceSession({
        id: sessionId,
        date,
        batch,
        notes,
        marks: payload,
      });
      setSessionId(saved.session.id);
      setToast('Attendance saved. Students and branch admin can see the update.');
      loadHistory();
    } catch (e: any) {
      setError(e?.message || 'Could not save attendance.');
    } finally {
      setBusy(false);
    }
  };

  const counts = useMemo(() => {
    const c = { present: 0, absent: 0, late: 0, excused: 0 };
    for (const s of batchStudents) {
      const st = marks[s.id]?.status || 'present';
      c[st] += 1;
    }
    return c;
  }, [marks, batchStudents]);

  return (
    <>
      <SectionTitle
        eyebrow="Teacher workspace"
        title="Attendance"
        description="Mark daily attendance for a batch. Students see it in their portal; branch admin can review campus updates."
      />

      <div className="grid gap-6 lg:grid-cols-[1.1fr_.9fr]">
        <div className="card space-y-4 p-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-sm font-semibold">
              Batch
              <select className="mt-1 h-11 w-full rounded-lg border border-input bg-card px-3 text-sm" value={batch} onChange={(e) => setBatch(e.target.value)}>
                {batches.map((b) => (
                  <option key={b} value={b}>{b}</option>
                ))}
              </select>
            </label>
            <label className="text-sm font-semibold">
              Date
              <input type="date" className="mt-1 h-11 w-full rounded-lg border border-input bg-card px-3 text-sm" value={date} onChange={(e) => setDate(e.target.value)} />
            </label>
          </div>

          <label className="block text-sm font-semibold">
            Class note (optional)
            <input className="mt-1 h-11 w-full rounded-lg border border-input bg-card px-3 text-sm" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="e.g. Writing Task 2 practice" />
          </label>

          <div className="flex flex-wrap gap-2">
            <Badge tone="green">Present {counts.present}</Badge>
            <Badge tone="amber">Late {counts.late}</Badge>
            <Badge tone="red">Absent {counts.absent}</Badge>
            <Badge tone="blue">Excused {counts.excused}</Badge>
          </div>

          <div className="flex flex-wrap gap-2">
            {STATUS_OPTIONS.map((opt) => (
              <Button key={opt.id} variant="quiet" onClick={() => markAll(opt.id)}>Mark all {opt.label.toLowerCase()}</Button>
            ))}
          </div>

          {!batchStudents.length ? (
            <EmptyState title="No students in this batch" text="Add students to this batch first, then mark attendance." />
          ) : (
            <div className="space-y-2">
              {batchStudents.map((s) => {
                const current = marks[s.id]?.status || 'present';
                return (
                  <div key={s.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border px-4 py-3">
                    <div>
                      <div className="font-semibold">{s.name}</div>
                      <p className="text-xs text-muted-foreground">{s.email || s.username}</p>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {STATUS_OPTIONS.map((opt) => (
                        <button
                          key={opt.id}
                          type="button"
                          onClick={() => setStatus(s.id, s.name, opt.id)}
                          className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold ${
                            current === opt.id ? 'border-primary bg-teal-50 text-primary' : 'border-border text-muted-foreground hover:bg-muted'
                          }`}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {error && <p className="text-sm text-red-700">{error}</p>}
          <Button disabled={busy || !batchStudents.length} onClick={() => void save()}>
            {busy ? 'Saving…' : sessionId ? 'Update attendance' : 'Save attendance'}
          </Button>
        </div>

        <div className="card p-5">
          <div className="eyebrow">Recent for {batch}</div>
          <h2 className="mt-1 font-display text-lg font-bold">Saved sessions</h2>
          <div className="mt-4 space-y-2">
            {history.slice(0, 12).map((row) => {
              const total = Object.keys(row.marks).length;
              const present = Object.values(row.marks).filter((m) => m.status === 'present' || m.status === 'late').length;
              return (
                <button
                  key={row.id}
                  type="button"
                  className="w-full rounded-xl border border-border px-4 py-3 text-left hover:border-primary/40"
                  onClick={() => {
                    setDate(row.date);
                    setBatch(row.batch);
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold">{row.date}</span>
                    <StatusBadge status={`${present}/${total} in class`} />
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{row.notes || 'No note'} · {row.teacherName}</p>
                </button>
              );
            })}
            {!history.length && <p className="text-sm text-muted-foreground">No attendance saved for this batch yet.</p>}
          </div>
        </div>
      </div>
      {toast && <Toast message={toast} onClose={() => setToast('')} />}
    </>
  );
}
