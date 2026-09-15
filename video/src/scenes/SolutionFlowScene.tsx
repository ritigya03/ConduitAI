import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {theme} from '../theme';
import {Reveal} from '../Reveal';

const NODES = [
  {
    title: 'Upload',
    sub: 'CSV or live API',
    caption: "Drop in a raw CSV, or pull straight from the customer's API.",
  },
  {
    title: 'Profile',
    sub: 'Polars stats',
    caption: 'Polars scans every column — nulls, types, sample values.',
  },
  {
    title: 'Score',
    sub: 'rapidfuzz + embeddings',
    caption: 'Fuzzy matching + embeddings score each column against the schema.',
  },
  {
    title: 'LLM tie-break',
    sub: 'ambiguous only',
    caption: 'Only the genuinely ambiguous columns get an LLM opinion.',
  },
  {
    title: 'Human review',
    sub: 'confirm mapping',
    caption: 'A person confirms — or overrides — every single mapping.',
  },
  {
    title: 'Canonical tables',
    sub: 'idempotent load',
    caption: 'Deterministic transform loads it. Idempotent, fully versioned.',
  },
];

const CARD_STAGGER = 4;
const STEP_START = 40;
const STEP_DURATION = 68;

export const SolutionFlowScene: React.FC = () => {
  const frame = useCurrentFrame();

  const stepFrame = Math.max(0, frame - STEP_START);
  const activeStep = Math.min(
    NODES.length - 1,
    Math.floor(stepFrame / STEP_DURATION),
  );
  const localStepFrame = stepFrame - activeStep * STEP_DURATION;

  const lineWidth = interpolate(
    frame,
    [STEP_START, STEP_START + NODES.length * STEP_DURATION],
    [0, 100],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  const captionOpacity = interpolate(
    localStepFrame,
    [0, 10, STEP_DURATION - 14, STEP_DURATION - 4],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <Reveal delay={2} distance={16} style={{marginBottom: 70}}>
        <div style={{fontSize: 48, fontWeight: 700, color: theme.ink}}>
          One pipeline. Every step reviewable.
        </div>
      </Reveal>

      <div
        style={{
          position: 'relative',
          display: 'flex',
          gap: 34,
          width: 1780,
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            position: 'absolute',
            top: 44,
            left: 90,
            right: 90,
            height: 3,
            backgroundColor: theme.border,
          }}
        />
        <div
          style={{
            position: 'absolute',
            top: 44,
            left: 90,
            height: 3,
            width: `calc(${lineWidth}% - ${(lineWidth / 100) * 180}px)`,
            backgroundColor: theme.accent,
          }}
        />

        {NODES.map((node, i) => {
          const isActiveOrPast = frame - STEP_START >= i * STEP_DURATION;
          const isCurrent = i === activeStep && frame >= STEP_START;
          return (
            <Reveal key={node.title} delay={i * CARD_STAGGER} distance={20}>
              <div
                style={{
                  width: 250,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                }}
              >
                <div
                  style={{
                    width: 20,
                    height: 20,
                    borderRadius: '50%',
                    backgroundColor: isActiveOrPast ? theme.accent : theme.border,
                    marginBottom: 26,
                    boxShadow: isCurrent
                      ? `0 0 0 8px ${theme.accentSoft}`
                      : `0 0 0 6px ${theme.accentSoft}`,
                    transform: isCurrent ? 'scale(1.15)' : 'scale(1)',
                  }}
                />
                <div
                  style={{
                    backgroundColor: theme.bgAlt,
                    border: `1px solid ${
                      isActiveOrPast ? theme.accent : theme.border
                    }`,
                    borderRadius: 16,
                    padding: '22px 18px',
                    width: '100%',
                    textAlign: 'center',
                    boxShadow: theme.cardShadow,
                    transform: isCurrent ? 'translateY(-6px)' : 'translateY(0)',
                  }}
                >
                  <div style={{fontSize: 24, fontWeight: 700, color: theme.ink}}>
                    {node.title}
                  </div>
                  <div
                    style={{
                      fontSize: 17,
                      color: theme.subtle,
                      marginTop: 8,
                      fontWeight: 500,
                    }}
                  >
                    {node.sub}
                  </div>
                </div>
              </div>
            </Reveal>
          );
        })}
      </div>

      <div
        style={{
          marginTop: 64,
          height: 60,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <div
          style={{
            opacity: captionOpacity,
            fontSize: 28,
            fontWeight: 600,
            color: theme.accent,
            textAlign: 'center',
            maxWidth: 1300,
          }}
        >
          {NODES[activeStep].caption}
        </div>
      </div>
    </AbsoluteFill>
  );
};
