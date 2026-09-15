import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {theme} from '../theme';
import {Reveal} from '../Reveal';

export const OutroScene: React.FC = () => {
  const frame = useCurrentFrame();
  const fadeOut = interpolate(frame, [115, 150], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        alignItems: 'center',
        justifyContent: 'center',
        opacity: fadeOut,
      }}
    >
      <div style={{textAlign: 'center'}}>
        <Reveal delay={0} distance={20}>
          <div
            style={{
              fontSize: 84,
              fontWeight: 800,
              color: theme.ink,
              letterSpacing: -1.5,
            }}
          >
            Conduit<span style={{color: theme.accent}}>AI</span>
          </div>
        </Reveal>

        <Reveal delay={14} distance={16}>
          <div
            style={{
              fontSize: 30,
              fontWeight: 600,
              color: theme.accent,
              marginTop: 24,
            }}
          >
            conduit-ai-eosin.vercel.app
          </div>
        </Reveal>

        <Reveal delay={26} distance={12}>
          <div
            style={{
              fontSize: 22,
              color: theme.subtle,
              marginTop: 20,
              fontWeight: 500,
            }}
          >
            FastAPI · Postgres · Polars · Next.js · Groq / Ollama
          </div>
        </Reveal>
      </div>
    </AbsoluteFill>
  );
};
