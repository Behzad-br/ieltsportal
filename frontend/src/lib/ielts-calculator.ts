/** Official-style IELTS band conversion helpers (practice estimates). */

export type ReadingTrack = 'academic' | 'general';

/** Listening raw correct answers (0–40) → band. Same table for Academic & GT. */
const LISTENING: [number, number][] = [
  [39, 9.0], [37, 8.5], [35, 8.0], [32, 7.5], [30, 7.0], [26, 6.5],
  [23, 6.0], [18, 5.5], [16, 5.0], [13, 4.5], [10, 4.0], [8, 3.5],
  [6, 3.0], [4, 2.5], [3, 2.0], [2, 1.5], [1, 1.0], [0, 0],
];

/** Academic Reading raw (0–40) → band. */
const READING_ACADEMIC: [number, number][] = [
  [39, 9.0], [37, 8.5], [35, 8.0], [33, 7.5], [30, 7.0], [27, 6.5],
  [23, 6.0], [19, 5.5], [15, 5.0], [13, 4.5], [10, 4.0], [8, 3.5],
  [6, 3.0], [4, 2.5], [3, 2.0], [2, 1.5], [1, 1.0], [0, 0],
];

/** General Training Reading raw (0–40) → band. */
const READING_GENERAL: [number, number][] = [
  [40, 9.0], [39, 8.5], [37, 8.0], [36, 7.5], [34, 7.0], [32, 6.5],
  [30, 6.0], [27, 5.5], [23, 5.0], [19, 4.5], [15, 4.0], [12, 3.5],
  [9, 3.0], [6, 2.5], [4, 2.0], [3, 1.5], [2, 1.0], [0, 0],
];

function fromTable(raw: number, table: [number, number][]): number {
  const n = Math.max(0, Math.min(40, Math.floor(raw)));
  for (const [minCorrect, band] of table) {
    if (n >= minCorrect) return band;
  }
  return 0;
}

export function listeningBand(raw: number): number {
  return fromTable(raw, LISTENING);
}

export function readingBand(raw: number, track: ReadingTrack = 'academic'): number {
  return fromTable(raw, track === 'general' ? READING_GENERAL : READING_ACADEMIC);
}

/** Clamp a module band to valid IELTS half-band steps. */
export function clampModuleBand(value: number): number {
  if (!Number.isFinite(value)) return 0;
  const clamped = Math.max(0, Math.min(9, value));
  return Math.round(clamped * 2) / 2;
}

/**
 * Official overall band rounding:
 * average of 4 modules, then to nearest 0.5
 * (.25 → up to .5, .75 → up to next whole).
 */
export function overallBand(listening: number, reading: number, writing: number, speaking: number): number {
  const avg = (listening + reading + writing + speaking) / 4;
  const whole = Math.floor(avg);
  const frac = avg - whole;
  if (frac < 0.25) return whole;
  if (frac < 0.75) return whole + 0.5;
  return whole + 1;
}

export function formatBand(band: number): string {
  return band.toFixed(1);
}

export function bandLabel(band: number): string {
  if (band >= 8.5) return 'Expert / Very good';
  if (band >= 7) return 'Good user';
  if (band >= 6) return 'Competent user';
  if (band >= 5) return 'Modest user';
  if (band >= 4) return 'Limited user';
  if (band > 0) return 'Extremely limited';
  return 'Enter scores';
}
