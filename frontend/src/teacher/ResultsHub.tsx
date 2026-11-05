import { useEffect, useState } from 'react';
import { Link } from 'wouter';
import { EmptyState } from '@/components/ui-kit';
import { listTeacherHomework, listSubmissions } from '@/lib/homework-api';
import { listTeacherMocks, listMockStudents, type MockAssignment, type MockStudentRow } from '@/lib/mocks-api';

function formatBand(v: number | null | undefined): string {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(1);
}

type PublishedRow = {
  id: string;
  title: string;
  student: string;
  band: number | null;
  date: string;
  kind: 'homework' | 'mock';
};

/** Teacher Results — published homework + mock outcomes from API (no static demo roster bands). */
export function TeacherResultsLive() {
  const [rows, setRows] = useState<PublishedRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const out: PublishedRow[] = [];

        const [hwResult, mocksResult] = await Promise.allSettled([
          listTeacherHomework(),
          listTeacherMocks(),
        ]);
        if (hwResult.status === 'rejected' && mocksResult.status === 'rejected') {
          throw new Error('Could not load results. Check your connection and try again.');
        }
        const hw =
          hwResult.status === 'fulfilled'
            ? hwResult.value
            : ({ assignments: [] as any[] } as Awaited<ReturnType<typeof listTeacherHomework>>);
        for (const a of (hw.assignments || []).slice(0, 25)) {
          try {
            const pack = await listSubmissions(a.id);
            for (const s of pack.submissions || []) {
              if (s.status !== 'published' && s.status !== 'reviewed') continue;
              out.push({
                id: `hw-${s.id || s.submission_id}`,
                title: a.title,
                student: s.student_name || s.student_id || 'Student',
                band: s.teacher_band ?? s.estimated_band ?? null,
                date: s.reviewed_at || s.submitted_at || '',
                kind: 'homework',
              });
            }
          } catch {
            /* skip assignment */
          }
        }

        const mocks =
          mocksResult.status === 'fulfilled'
            ? mocksResult.value
            : ({ assignments: [] as MockAssignment[] } as Awaited<ReturnType<typeof listTeacherMocks>>);
        for (const m of (mocks.assignments || []).slice(0, 20)) {
          try {
            const pack = await listMockStudents(m.id);
            for (const s of pack.students || ([] as MockStudentRow[])) {
              if (!s.published) continue;
              out.push({
                id: `mk-${s.attempt_id || s.id}`,
                title: m.title,
                student: s.student_name || s.student_id,
                band: s.overall_band ?? s.writing_band ?? s.reading_band ?? s.listening_band ?? s.speaking_band ?? null,
                date: s.last_seen_at || '',
                kind: 'mock',
              });
            }
          } catch {
            /* skip */
          }
        }

        out.sort((a, b) => String(b.date).localeCompare(String(a.date)));
        if (!cancelled) setRows(out);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not load results.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) return <p className="text-sm text-muted-foreground">Loading published results…</p>;
  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (!rows.length) {
    return (
      <EmptyState
        title="No published results yet"
        text="Reviewed homework and published mock scores for your campus appear here. Open Mock Exams → Results to mark attempts."
        action={
          <Link href="/teacher/mocks/results" className="btn-primary rounded-lg px-4 py-2 text-sm font-bold">
            Open mock results
          </Link>
        }
      />
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="mobile-scroll">
        <div className="min-w-[680px]">
          <div className="grid grid-cols-[1.3fr_1fr_.6fr_.7fr_.6fr] gap-4 bg-muted/60 px-5 py-3 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <span>Published result</span>
            <span>Student</span>
            <span>Band</span>
            <span>Kind</span>
            <span>Date</span>
          </div>
          {rows.map((r) => (
            <div
              key={r.id}
              className="data-row grid grid-cols-[1.3fr_1fr_.6fr_.7fr_.6fr] items-center gap-4 px-5 py-4 text-sm"
            >
              <div className="font-semibold">{r.title}</div>
              <div>{r.student}</div>
              <strong>{formatBand(r.band)}</strong>
              <span className="text-xs text-muted-foreground">{r.kind}</span>
              <span className="text-xs text-muted-foreground">{r.date ? String(r.date).slice(0, 10) : '—'}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
