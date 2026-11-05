import { useEffect, useState } from 'react';
import { Link } from 'wouter';
import { Badge, EmptyState } from '@/components/ui-kit';
import { DEMO_MODE } from '@/lib/demo-mode';
import { readingHistory } from '@/lib/reading/api';
import { listeningHistory } from '@/lib/listening/api';
import { writingHistory } from '@/lib/writing/api';
import { getHistory as speakingHistory } from '@/lib/speaking/api';
import { studentHomework, studentHomeworkDetail } from '@/lib/homework-api';
import { studentMockInbox } from '@/lib/mocks-api';

type ResultRow = {
  id: string;
  skill: string;
  title: string;
  status: string;
  estimated?: number | null;
  final?: number | null;
  source?: string | null;
  date?: string | null;
  href?: string;
};

function formatBand(v: number | null | undefined): string {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(1);
}

function sourceLabel(source?: string | null): string {
  if (source === 'teacher') return 'Teacher final';
  if (source === 'ai_estimated' || source === 'estimated') return 'AI estimate';
  if (source === 'none' || !source) return 'Pending';
  return source;
}

function BandCell({ value, pending }: { value?: number | null; pending?: boolean }) {
  if (pending || value == null) {
    return <span className="text-xs text-muted-foreground">{pending ? 'Pending' : 'N/A'}</span>;
  }
  return <strong className="font-display text-lg">{formatBand(value)}</strong>;
}

/** Student Results — DB-backed skill / homework / mock rows only (no static demo bands when API mode). */
export function StudentResultsLive() {
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const settled = await Promise.allSettled([
          readingHistory(),
          listeningHistory(),
          writingHistory(),
          speakingHistory(),
          studentHomework(),
          studentMockInbox(),
        ]);
        const failed = settled.filter((r) => r.status === 'rejected').length;
        if (failed === settled.length) {
          throw new Error('Could not load results. Check your connection and try again.');
        }
        const reading =
          settled[0].status === 'fulfilled'
            ? settled[0].value
            : ({ attempts: [] as any[] } as Awaited<ReturnType<typeof readingHistory>>);
        const listening =
          settled[1].status === 'fulfilled'
            ? settled[1].value
            : ({ attempts: [] as any[] } as Awaited<ReturnType<typeof listeningHistory>>);
        const writing =
          settled[2].status === 'fulfilled'
            ? settled[2].value
            : ({ items: [] as any[], total: 0 } as Awaited<ReturnType<typeof writingHistory>>);
        const speaking =
          settled[3].status === 'fulfilled'
            ? settled[3].value
            : ({ sessions: [] as any[], total: 0 } as Awaited<ReturnType<typeof speakingHistory>>);
        const homework =
          settled[4].status === 'fulfilled'
            ? settled[4].value
            : ({ assignments: [] as any[] } as Awaited<ReturnType<typeof studentHomework>>);
        const mocks =
          settled[5].status === 'fulfilled'
            ? settled[5].value
            : ({ items: [] as any[] } as Awaited<ReturnType<typeof studentMockInbox>>);

        const out: ResultRow[] = [];

        for (const a of reading.attempts || []) {
          if ((a.status || '').toLowerCase() === 'in_progress') continue;
          const band = a.estimated_band ?? null;
          out.push({
            id: `r-${a.id}`,
            skill: 'Reading',
            title: a.test_id || 'Reading attempt',
            status: a.status || 'submitted',
            final: band,
            source: band != null ? 'server' : 'none',
            date: a.submitted_at || a.started_at,
            href: `/student/practice`,
          });
        }
        for (const a of listening.attempts || []) {
          if ((a.status || '').toLowerCase() === 'in_progress') continue;
          const band = a.estimated_band ?? null;
          out.push({
            id: `l-${a.id}`,
            skill: 'Listening',
            title: a.test_id || 'Listening attempt',
            status: a.status || 'submitted',
            final: band,
            source: band != null ? 'server' : 'none',
            date: a.submitted_at || a.started_at,
            href: `/student/practice`,
          });
        }
        for (const a of writing.items || []) {
          out.push({
            id: `w-${a.id}`,
            skill: 'Writing',
            title: (a.topic as string) || `Task ${a.task_number || '?'}`,
            status: String(a.status || ''),
            estimated: a.estimated_band as number | null,
            final: a.final_band as number | null,
            source: String(a.band_source || 'none'),
            date: a.date as string | null,
            href: `/student/practice`,
          });
        }
        for (const s of speaking.sessions || []) {
          out.push({
            id: `s-${s.session_id}`,
            skill: 'Speaking',
            title: s.title || s.mode || 'Speaking session',
            status: s.status || '',
            estimated: s.estimated_band,
            final: s.final_band,
            source: s.band_source,
            date: s.completed_at || s.started_at,
            href: `/student/practice`,
          });
        }

        const hwDone = (homework.assignments || []).filter((h) =>
          ['Completed', 'submitted', 'reviewed', 'published'].includes(String(h.status || '')),
        );
        const hwDetails = await Promise.all(
          hwDone.slice(0, 20).map(async (h) => {
            try {
              const d = await studentHomeworkDetail(h.id);
              return { h, d };
            } catch {
              return { h, d: null };
            }
          }),
        );
        for (const { h, d } of hwDetails) {
          const published = d && (d as any).status === 'published';
          const pending = Boolean((d as any)?.result_pending);
          out.push({
            id: `hw-${h.id}`,
            skill: `Homework · ${h.module || ''}`,
            title: h.title,
            status: String((d as any)?.status || h.status || ''),
            estimated: (d as any)?.estimated_band ?? null,
            final: published ? ((d as any)?.teacher_band ?? null) : null,
            source: (d as any)?.band_source || (pending ? 'none' : null),
            date: h.deadline || null,
            href: `/student/homework/${h.id}`,
          });
        }

        for (const m of mocks.items || []) {
          if (!m.published && m.overall_band == null && m.skill_band == null) {
            if (['submitted', 'reviewed', 'in_progress'].includes(String(m.inbox_status || ''))) {
              out.push({
                id: `m-${m.id}`,
                skill: `Mock · ${m.mock_type || 'skill'}`,
                title: m.title,
                status: m.inbox_status || 'submitted',
                final: null,
                source: 'none',
                date: m.deadline_at || m.available_at,
                href: `/student/mocks/${m.id}`,
              });
            }
            continue;
          }
          if (!m.published) continue;
          out.push({
            id: `m-${m.id}`,
            skill: `Mock · ${m.mock_type || 'skill'}`,
            title: m.title,
            status: 'published',
            final: m.skill_band ?? m.overall_band ?? null,
            source: m.result_mode === 'ai' ? 'ai_estimated' : 'teacher',
            date: m.deadline_at || m.available_at,
            href: `/student/mocks/${m.id}`,
          });
        }

        out.sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
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

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading your results from the server…</p>;
  }
  if (error) {
    return <p className="text-sm text-red-700">{error}</p>;
  }
  if (!rows.length) {
    return (
      <EmptyState
        title="No results yet"
        text={
          DEMO_MODE
            ? 'Demo mode is on — seed practice attempts, or turn off VITE_DEMO_MODE and use live API data.'
            : 'Complete a practice test, homework, or mock to see DB-backed scores here.'
        }
      />
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        Scores come from the database only. Missing values show as Pending / N/A — never Band 0.
      </p>
      <div className="card overflow-hidden">
        <div className="mobile-scroll">
          <div className="min-w-[720px]">
            <div className="grid grid-cols-[.9fr_1.4fr_.7fr_.7fr_.9fr] gap-3 bg-muted/60 px-4 py-3 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
              <span>Skill</span>
              <span>Title</span>
              <span>Estimate</span>
              <span>Final</span>
              <span>Source</span>
            </div>
            {rows.map((r) => {
              const pending = r.source === 'none' || (r.final == null && r.estimated == null);
              return (
                <div
                  key={r.id}
                  className="data-row grid grid-cols-[.9fr_1.4fr_.7fr_.7fr_.9fr] items-center gap-3 px-4 py-3 text-sm"
                >
                  <div>
                    <div className="font-semibold">{r.skill}</div>
                    <div className="text-[11px] text-muted-foreground">{r.status}</div>
                  </div>
                  <div>
                    {r.href ? (
                      <Link href={r.href} className="font-semibold text-primary hover:underline">
                        {r.title}
                      </Link>
                    ) : (
                      <div className="font-semibold">{r.title}</div>
                    )}
                    {r.date ? (
                      <div className="mt-0.5 text-[11px] text-muted-foreground">
                        {String(r.date).slice(0, 10)}
                      </div>
                    ) : null}
                  </div>
                  <BandCell value={r.estimated} pending={r.estimated == null && pending} />
                  <BandCell value={r.final} pending={pending && r.final == null} />
                  <Badge tone={r.source === 'teacher' ? 'green' : r.source === 'none' ? 'amber' : 'blue'}>
                    {sourceLabel(r.source)}
                  </Badge>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Student History — writing archive from `/writing/history` (owned attempts only). */
export function StudentHistoryLive({ query }: { query: string }) {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    writingHistory()
      .then((d) => {
        if (!cancelled) setItems(d.items || []);
      })
      .catch(() => {
        if (!cancelled) setItems([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const q = query.trim().toLowerCase();
  const filtered = items.filter((a) => {
    if (!q) return true;
    const topic = String(a.topic || '');
    const type = String(a.question_type || '');
    return topic.toLowerCase().includes(q) || type.toLowerCase().includes(q);
  });

  if (loading) return <p className="text-sm text-muted-foreground">Loading writing history…</p>;

  if (!filtered.length) {
    return (
      <EmptyState
        title="No writing history"
        text="Submitted writing attempts from your account appear here after refresh."
      />
    );
  }

  return (
    <div className="card overflow-hidden">
      <div className="mobile-scroll">
        <div className="min-w-[680px]">
          <div className="grid grid-cols-[1.4fr_.6fr_.7fr_.7fr_.7fr] gap-4 bg-muted/60 px-5 py-3 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <span>Response</span>
            <span>Task</span>
            <span>Status</span>
            <span>Band</span>
            <span>Date</span>
          </div>
          {filtered.map((a) => {
            const final = a.final_band as number | null | undefined;
            const est = a.estimated_band as number | null | undefined;
            const source = String(a.band_source || 'none');
            const display = final != null ? final : est;
            return (
              <div
                key={String(a.id)}
                className="data-row grid grid-cols-[1.4fr_.6fr_.7fr_.7fr_.7fr] items-center gap-4 px-5 py-4 text-sm"
              >
                <div>
                  <div className="font-semibold">{String(a.topic || 'Writing response')}</div>
                  <div className="mt-1 text-xs text-muted-foreground">{String(a.question_type || '—')}</div>
                </div>
                <span className="text-muted-foreground">Task {String(a.task_number ?? '—')}</span>
                <span className="text-xs">{String(a.status || '—')}</span>
                <div>
                  {display == null ? (
                    <span className="text-xs text-muted-foreground">Pending</span>
                  ) : (
                    <>
                      <strong>{formatBand(display)}</strong>
                      <div className="text-[11px] text-muted-foreground">{sourceLabel(source)}</div>
                    </>
                  )}
                </div>
                <span className="text-xs text-muted-foreground">
                  {a.date ? String(a.date).slice(0, 10) : '—'}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
