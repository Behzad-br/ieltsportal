import { useEffect, useState } from 'react';
import { Badge, EmptyState, SectionTitle, StatusBadge } from '@/components/ui-kit';
import { studentMyAttendance, type AttendanceStatus } from '@/lib/attendance-api';
import { getCurrentStudent } from '@/lib/mock-api';

const TONE: Record<AttendanceStatus, 'green' | 'amber' | 'red' | 'blue'> = {
  present: 'green',
  late: 'amber',
  absent: 'red',
  excused: 'blue',
};

export default function StudentAttendance() {
  const me = getCurrentStudent();
  const [rows, setRows] = useState<Awaited<ReturnType<typeof studentMyAttendance>>['records']>([]);
  const [summary, setSummary] = useState({ present: 0, absent: 0, late: 0, excused: 0, total: 0, rate: 0 });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    studentMyAttendance()
      .then((d) => {
        setRows(d.records || []);
        setSummary(d.summary);
      })
      .catch(() => {
        setRows([]);
      })
      .finally(() => setLoading(false));
  }, [me.id]);

  return (
    <>
      <SectionTitle
        eyebrow="Student portal"
        title="My attendance"
        description="Teachers mark class attendance. Your present, late, absent and excused days appear here."
      />

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {[
          ['Attendance rate', `${summary.rate}%`],
          ['Present', summary.present],
          ['Late', summary.late],
          ['Absent', summary.absent],
          ['Excused', summary.excused],
        ].map(([label, value]) => (
          <div key={String(label)} className="card p-4 text-center">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">{label}</div>
            <div className="mt-1 font-display text-2xl font-bold">{value}</div>
          </div>
        ))}
      </div>

      {loading ? (
        <p className="text-sm text-muted-foreground">Loading attendance…</p>
      ) : !rows.length ? (
        <EmptyState title="No attendance yet" text="When your teacher marks a class, it will show up here." />
      ) : (
        <div className="card overflow-hidden">
          <div className="grid grid-cols-[1fr_1fr_.8fr_1fr] gap-3 bg-muted/60 px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <span>Date</span>
            <span>Batch</span>
            <span>Status</span>
            <span>Teacher</span>
          </div>
          {rows.map((row) => (
            <div key={`${row.sessionId}-${row.date}`} className="data-row grid grid-cols-[1fr_1fr_.8fr_1fr] items-center gap-3 px-4 py-3 text-sm">
              <span className="font-semibold">{row.date}</span>
              <span className="text-muted-foreground">{row.batch}</span>
              <span><Badge tone={TONE[row.status]}>{row.status}</Badge></span>
              <span className="text-muted-foreground">{row.teacherName}</span>
            </div>
          ))}
        </div>
      )}

      {summary.total > 0 && (
        <p className="mt-4 text-xs text-muted-foreground">
          <StatusBadge status={`${summary.total} class days`} /> Rate counts present fully, late as half, excused as three-quarters.
        </p>
      )}
    </>
  );
}
