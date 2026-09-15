// Synthesizes an original, royalty-free background track for the teaser —
// a soft pad + plucked arpeggio over a C-G-Am-F progression with a gentle
// sub-kick pulse. Run: node scripts/generate-music.mjs
import fs from 'fs';
import path from 'path';
import {fileURLToPath} from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const SAMPLE_RATE = 44100;
const DURATION = 45; // seconds — matches the video length
const BPM = 96;
const BEAT = 60 / BPM;

const CHORDS = [
  [130.81, 164.81, 196.0, 261.63], // C
  [196.0, 246.94, 293.66, 392.0], // G
  [220.0, 261.63, 329.63, 440.0], // Am
  [174.61, 220.0, 261.63, 349.23], // F
];
const CHORD_BEATS = 8;
const CHORD_DURATION = CHORD_BEATS * BEAT;

const numSamples = Math.floor(SAMPLE_RATE * DURATION);
const samples = new Float64Array(numSamples);

function chordAt(t) {
  const idx = Math.floor(t / CHORD_DURATION) % CHORDS.length;
  return CHORDS[idx];
}

// Pad layer: sustained chord tones with slow tremolo.
for (let i = 0; i < numSamples; i++) {
  const t = i / SAMPLE_RATE;
  const chord = chordAt(t);
  let padSample = 0;
  for (const freq of chord) {
    padSample += Math.sin(2 * Math.PI * freq * t);
  }
  padSample /= chord.length;
  const tremolo = 1 + 0.06 * Math.sin(2 * Math.PI * 0.18 * t);
  samples[i] += padSample * 0.09 * tremolo;
}

// Arpeggio layer: plucked eighth notes, one octave above the pad.
const NOTE_DURATION = BEAT / 2;
const numNotes = Math.ceil(DURATION / NOTE_DURATION);
for (let n = 0; n < numNotes; n++) {
  const startT = n * NOTE_DURATION;
  if (startT >= DURATION) break;
  const chord = chordAt(startT);
  const noteFreq = chord[n % chord.length] * 2;
  const startSample = Math.floor(startT * SAMPLE_RATE);
  const noteLenSamples = Math.min(
    Math.floor(0.28 * SAMPLE_RATE),
    numSamples - startSample,
  );
  for (let s = 0; s < noteLenSamples; s++) {
    const tt = s / SAMPLE_RATE;
    const envelope = tt < 0.005 ? tt / 0.005 : Math.exp(-(tt - 0.005) * 9);
    const wave =
      Math.sin(2 * Math.PI * noteFreq * tt) +
      0.3 * Math.sin(2 * Math.PI * noteFreq * 2 * tt);
    samples[startSample + s] += wave * envelope * 0.05;
  }
}

// Soft sub-kick on every beat for a gentle pulse.
const numBeats = Math.ceil(DURATION / BEAT);
for (let b = 0; b < numBeats; b++) {
  const startT = b * BEAT;
  if (startT >= DURATION) break;
  const startSample = Math.floor(startT * SAMPLE_RATE);
  const kickLenSamples = Math.min(
    Math.floor(0.09 * SAMPLE_RATE),
    numSamples - startSample,
  );
  for (let s = 0; s < kickLenSamples; s++) {
    const tt = s / SAMPLE_RATE;
    const envelope = Math.exp(-tt * 40);
    const freq = 62 - tt * 20;
    samples[startSample + s] +=
      Math.sin(2 * Math.PI * freq * tt) * envelope * 0.14;
  }
}

// Global fade in/out, then normalize to avoid clipping.
const FADE_IN = 1.5;
const FADE_OUT = 3.0;
let peak = 0;
for (let i = 0; i < numSamples; i++) {
  const t = i / SAMPLE_RATE;
  let g = 1;
  if (t < FADE_IN) g = t / FADE_IN;
  if (t > DURATION - FADE_OUT) g = Math.max(0, (DURATION - t) / FADE_OUT);
  samples[i] *= g;
  peak = Math.max(peak, Math.abs(samples[i]));
}
const scale = peak > 0 ? 0.85 / peak : 1;

// Encode as 16-bit PCM mono WAV.
const bytesPerSample = 2;
const dataSize = numSamples * bytesPerSample;
const buffer = Buffer.alloc(44 + dataSize);

buffer.write('RIFF', 0);
buffer.writeUInt32LE(36 + dataSize, 4);
buffer.write('WAVE', 8);
buffer.write('fmt ', 12);
buffer.writeUInt32LE(16, 16);
buffer.writeUInt16LE(1, 20);
buffer.writeUInt16LE(1, 22);
buffer.writeUInt32LE(SAMPLE_RATE, 24);
buffer.writeUInt32LE(SAMPLE_RATE * bytesPerSample, 28);
buffer.writeUInt16LE(bytesPerSample, 32);
buffer.writeUInt16LE(16, 34);
buffer.write('data', 36);
buffer.writeUInt32LE(dataSize, 40);

for (let i = 0; i < numSamples; i++) {
  const v = Math.max(-1, Math.min(1, samples[i] * scale));
  buffer.writeInt16LE(Math.round(v * 32767), 44 + i * 2);
}

const outPath = path.join(__dirname, '..', 'public', 'theme.wav');
fs.mkdirSync(path.dirname(outPath), {recursive: true});
fs.writeFileSync(outPath, buffer);
console.log(`Wrote ${outPath} (${(dataSize / 1024 / 1024).toFixed(2)} MB)`);
