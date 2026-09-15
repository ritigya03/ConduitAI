import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {theme} from '../theme';
import {Reveal} from '../Reveal';

const BARS = [
  {label: 'Fuzzy baseline', value: 79, highlight: false},
  {label: 'Hybrid, no LLM', value: 75, highlight: false},
  {label: '+ LLM tie-break', value: 83, highlight: true},
];

const MAX_BAR_HEIGHT = 380;
const GROW_START = 14;
const GROW_DURATION = 34;

export const BenchmarkScene: React.FC = () => {
  const frame = useCurrentFrame();
  const growProgress = interpolate(
    frame,
    [GROW_START, GROW_START + GROW_DURATION],
    [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <Reveal delay={2} distance={16} style={{textAlign: 'center', marginBottom: 56}}>
        <div style={{fontSize: 46, fontWeight: 700, color: theme.ink}}>
          Held-out columns, never seen while building the mapping.
        </div>
      </Reveal>

      <div
        style={{
          display: 'flex',
          gap: 90,
          alignItems: 'flex-end',
          height: MAX_BAR_HEIGHT + 90,
        }}
      >
        {BARS.map((bar, i) => {
          const height = bar.value * (MAX_BAR_HEIGHT / 100) * growProgress;
          const displayValue = Math.round(bar.value * growProgress);
          return (
            <Reveal key={bar.label} delay={i * 5} distance={0}>
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  width: 200,
                }}
              >
                <div
                  style={{
                    fontSize: 40,
                    fontWeight: 800,
                    color: bar.highlight ? theme.accent : theme.ink,
                    marginBottom: 14,
                  }}
                >
                  {displayValue}%
                </div>
                <div
                  style={{
                    width: 140,
                    height,
                    borderRadius: 14,
                    backgroundColor: bar.highlight ? theme.accent : '#D1D5DB',
                    boxShadow: bar.highlight ? theme.cardShadow : 'none',
                  }}
                />
                <div
                  style={{
                    fontSize: 20,
                    fontWeight: 600,
                    color: theme.subtle,
                    marginTop: 18,
                    textAlign: 'center',
                  }}
                >
                  {bar.label}
                </div>
              </div>
            </Reveal>
          );
        })}
      </div>

      <Reveal delay={64} distance={14} style={{marginTop: 48, textAlign: 'center'}}>
        <div style={{fontSize: 26, color: theme.subtle, fontWeight: 500, maxWidth: 1100}}>
          The LLM tie-break — called only for genuinely ambiguous columns — recovers
          accuracy past both baselines.
        </div>
      </Reveal>
    </AbsoluteFill>
  );
};
