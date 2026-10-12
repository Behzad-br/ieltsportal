import { useMemo, useState, type ReactNode } from 'react';
import { Calculator, Headphones, BookOpen, PencilLine, Mic } from 'lucide-react';
import { Badge, Button, SectionTitle } from '@/components/ui-kit';
import {
  bandLabel,
  clampModuleBand,
  formatBand,
  listeningBand,
  overallBand,
  readingBand,
  type ReadingTrack,
} from '@/lib/ielts-calculator';

type Props = {
  eyebrow?: string;
};

function ScoreInput({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  min: number;
  max: number;
  step?: number;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-semibold">{label}</span>
      <input
        type="number"
        inputMode="decimal"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-11 w-full rounded-lg border border-input bg-card px-3 text-sm focus:ring-2 focus:ring-ring"
      />
      {hint && <span className="mt-1 block text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

export default function IeltsCalculator({ eyebrow = 'Tools' }: Props) {
  const [listeningRaw, setListeningRaw] = useState('30');
  const [readingRaw, setReadingRaw] = useState('30');
  const [readingTrack, setReadingTrack] = useState<ReadingTrack>('academic');
  const [writing, setWriting] = useState('6.5');
  const [speaking, setSpeaking] = useState('6.5');

  const result = useMemo(() => {
    const L = listeningBand(Number(listeningRaw) || 0);
    const R = readingBand(Number(readingRaw) || 0, readingTrack);
    const W = clampModuleBand(Number(writing) || 0);
    const S = clampModuleBand(Number(speaking) || 0);
    const overall = overallBand(L, R, W, S);
    return { L, R, W, S, overall };
  }, [listeningRaw, readingRaw, readingTrack, writing, speaking]);

  const reset = () => {
    setListeningRaw('30');
    setReadingRaw('30');
    setReadingTrack('academic');
    setWriting('6.5');
    setSpeaking('6.5');
  };

  return (
    <>
      <SectionTitle
        eyebrow={eyebrow}
        title="IELTS band calculator"
        description="Convert Listening & Reading raw scores to bands, then estimate overall from all four modules. Practice estimate only — not an official IELTS result."
      />

      <div className="grid gap-6 lg:grid-cols-[1.15fr_.85fr]">
        <div className="card space-y-6 p-6">
          <div>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="font-display text-lg font-bold">Raw scores → bands</h2>
              <Badge tone="amber">0–40 correct</Badge>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <ScoreInput
                label="Listening correct answers"
                value={listeningRaw}
                onChange={setListeningRaw}
                min={0}
                max={40}
                hint={`≈ band ${formatBand(result.L)}`}
              />
              <ScoreInput
                label="Reading correct answers"
                value={readingRaw}
                onChange={setReadingRaw}
                min={0}
                max={40}
                hint={`≈ band ${formatBand(result.R)} (${readingTrack === 'academic' ? 'Academic' : 'GT'})`}
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setReadingTrack('academic')}
                className={`rounded-lg border px-3 py-1.5 text-xs font-bold ${readingTrack === 'academic' ? 'border-primary bg-teal-50 text-primary' : 'border-border'}`}
              >
                Academic Reading
              </button>
              <button
                type="button"
                onClick={() => setReadingTrack('general')}
                className={`rounded-lg border px-3 py-1.5 text-xs font-bold ${readingTrack === 'general' ? 'border-primary bg-teal-50 text-primary' : 'border-border'}`}
              >
                General Training
              </button>
            </div>
          </div>

          <div className="border-t border-border pt-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="font-display text-lg font-bold">Writing & Speaking bands</h2>
              <Badge>0–9 · half bands</Badge>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <ScoreInput
                label="Writing band"
                value={writing}
                onChange={setWriting}
                min={0}
                max={9}
                step={0.5}
                hint="Examiner / AI estimated band"
              />
              <ScoreInput
                label="Speaking band"
                value={speaking}
                onChange={setSpeaking}
                min={0}
                max={9}
                step={0.5}
                hint="Examiner / AI estimated band"
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="quiet" onClick={reset}>Reset example</Button>
          </div>
        </div>

        <div className="space-y-4">
          <div className="card overflow-hidden p-0">
            <div className="bg-[hsl(var(--sidebar))] px-6 py-5 text-white">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-amber-300">
                <Calculator size={14} /> Overall estimate
              </div>
              <div className="mt-3 font-display text-5xl font-bold tracking-tight">{formatBand(result.overall)}</div>
              <p className="mt-2 text-sm text-white/65">{bandLabel(result.overall)}</p>
            </div>
            <div className="grid grid-cols-2 gap-3 p-5">
              <ModuleChip icon={<Headphones size={15} />} label="Listening" band={result.L} />
              <ModuleChip icon={<BookOpen size={15} />} label="Reading" band={result.R} />
              <ModuleChip icon={<PencilLine size={15} />} label="Writing" band={result.W} />
              <ModuleChip icon={<Mic size={15} />} label="Speaking" band={result.S} />
            </div>
          </div>

          <div className="card p-5 text-sm leading-6 text-muted-foreground">
            <p className="font-semibold text-foreground">How overall is calculated</p>
            <p className="mt-2">
              Average of the four module bands, then rounded to the nearest half band
              (e.g. 6.25 → 6.5, 6.75 → 7.0). Listening/Reading use published-style conversion tables.
            </p>
            <p className="mt-2 text-xs">
              This tool is for classroom practice only. It is not an official IELTS result from British Council, IDP, or Cambridge.
            </p>
          </div>
        </div>
      </div>
    </>
  );
}

function ModuleChip({ icon, label, band }: { icon: ReactNode; label: string; band: number }) {
  return (
    <div className="rounded-xl border border-border bg-muted/40 px-3 py-3">
      <div className="flex items-center gap-2 text-xs font-semibold text-muted-foreground">
        <span className="text-primary">{icon}</span>
        {label}
      </div>
      <div className="mt-1 font-display text-2xl font-bold">{formatBand(band)}</div>
    </div>
  );
}
