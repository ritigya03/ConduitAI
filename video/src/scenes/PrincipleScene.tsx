import React from 'react';
import {AbsoluteFill} from 'remotion';
import {theme} from '../theme';
import {Reveal} from '../Reveal';

export const PrincipleScene: React.FC = () => {
  return (
    <AbsoluteFill
      style={{
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: theme.ink,
      }}
    >
      <div style={{textAlign: 'center', maxWidth: 1400}}>
        <Reveal delay={2} distance={26}>
          <div style={{fontSize: 68, fontWeight: 700, color: '#F9FAFB'}}>
            The LLM proposes.
          </div>
        </Reveal>
        <Reveal delay={20} distance={26}>
          <div
            style={{
              fontSize: 68,
              fontWeight: 800,
              color: theme.accentSoft,
              marginTop: 14,
            }}
          >
            The deterministic engine executes.
          </div>
        </Reveal>
        <Reveal delay={48} distance={16}>
          <div
            style={{
              fontSize: 28,
              color: '#9CA3AF',
              marginTop: 40,
              fontWeight: 500,
            }}
          >
            Every mapping is versioned, reviewable, and reproducible.
          </div>
        </Reveal>
      </div>
    </AbsoluteFill>
  );
};
