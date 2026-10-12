import { useEffect, useMemo, useState } from 'react';
import { Badge, EmptyState, SearchInput, SectionTitle, StatusBadge } from '@/components/ui-kit';
import { branchAttendanceOverview, type AttendanceSession } from '@/lib/attendance-api';
import { getCurrentBranchAdmin } from '@/lib/mock-api';

export default function BranchAttendance() {
  const campus = getCurrentBranchAdmin();
  const [sessions, setSessions] = useState<AttendanceSession[]>([]);
  const [students, setStudents] = useState<Awaited<ReturnType<typeof branchAttendanceOverview>>['students']>([]);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    branchAttendanceOverview()
      .then((d) => {
        setSessions(d.sessions || []);
        setStudents(d.students || []);
      })
      .catch(() => {
        setSessions([]);
        setStudents([]);
      })
      .finally(() => setLoading(false));
  }, [campus?.id]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return students;
    return students.filter((s) => `${s.studentName} ${s.batch}`.toLowerCase().includes(q));
  }, [students, query]);

  const totals = useMemo(() => {
    const present = students.reduce((n, s) => n + s.present, 0);
    const absent = students.reduce((n, s) => n + s.absent, 0);
    const late = students.reduce((n, s) => n + s.late, 0);
    return { present, absent, late, sessions: sessions.length, students: students.length };
  }, [students, sessions]);

  return (
    <>
      <SectionTitle
        eyebrow="Branch admin"
        title="Attendance overview"
        description={`${campus?.name || 'Your branch'} — see which classes teachers marked and each student's attendance rate.`}
      />

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {[
          ['Sessions', totals.sessions],
          ['Students tracked', totals.students],
          ['Present marks', totals.present],
          ['Late marks', totals.late],
          ['Absent marks', totals.absent],
        ].map(([label, value]) => (
          <div key={String(label)} className="card p-4 text-center">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</div>
            <div className="mt-1 font-display text-2xl font-bold">{value}</div>
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[.95fr_1.05fr]">
        <div className="card p-5">
          <div className="eyebrow">Recent updates</div>
          <h2 className="mt-1 font-display text-lg font-bold">Teacher attendance sessions</h2>
          {loading ? (
            <p className="mt-4 text-sm text-muted-foreground">Loading…</p>
          ) : !sessions.length ? (
            <div className="mt-4">
              <EmptyState title="No attendance yet" text="When teachers mark a batch, those updates appear here." />
            </div>
          ) : (
            <div className="mt-4 space-y-2">
              {sessions.slice(0, 15).map((row) => {
                const total = Object.keys(row.marks).length;
                const present = Object.values(row.marks).filter((m) => m.status === 'present').length;
                const absent = Object.values(row.marks).filter((m) => m.status === 'absent').length;
                return (
                  <div key={row.id} className="rounded-xl border border-border px-4 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div>
                        <div className="font-semibold">{row.batch} · {row.date}</div>
                        <p className="text-xs text-muted-foreground">{row.teacherName}{row.notes ? ` · ${row.notes}` : ''}</p>
                      </div>
                      <StatusBadge status={`${present} present · ${absent} absent · ${total} total`} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="card p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="eyebrow">Students</div>
              <h2 className="mt-1 font-display text-lg font-bold">Attendance rates</h2>
            </div>
            <SearchInput value={query} onChange={setQuery} placeholder="Search student or batch" />
          </div>
          <div className="mt-4 space-y-2">
            {filtered.map((s) => (
              <div key={s.studentId} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border px-4 py-3">
                <div>
                  <div className="font-semibold">{s.studentName}</div>
                  <p className="text-xs text-muted-foreground">{s.batch} · {s.total} days</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="green">P {s.present}</Badge>
                  <Badge tone="amber">L {s.late}</Badge>
                  <Badge tone="red">A {s.absent}</Badge>
                  <Badge tone="blue">E {s.excused}</Badge>
                  <StatusBadge status={`${s.rate}%`} />
                </div>
              </div>
            ))}
            {!loading && !filtered.length && (
              <p className="text-sm text-muted-foreground">No student attendance rows for this branch yet.</p>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
