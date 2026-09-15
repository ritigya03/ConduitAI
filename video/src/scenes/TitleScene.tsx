import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame} from 'remotion';
import {theme} from '../theme';
import {Reveal} from '../Reveal';

export const TitleScene: React.FC = () => {
  const frame = useCurrentFrame();
  const blob1 = interpolate(frame, [0, 120], [0, 40]);
  const blob2 = interpolate(frame, [0, 120], [0, -30]);

  return (
    <AbsoluteFill
      style={{
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          position: 'absolute',
          width: 640,
          height: 640,
          borderRadius: '50%',
          background: theme.accentSoft,
          top: -220 + blob1,
          left: -160,
          filter: 'blur(2px)',
        }}
      />
      <div
        style={{
          position: 'absolute',
          width: 520,
          height: 520,
          borderRadius: '50%',
          background: theme.accentSoft,
          bottom: -200 + blob2,
          right: -140,
          filter: 'blur(2px)',
        }}
      />

      <Reveal delay={2} distance={40} style={{textAlign: 'center'}}>
        <div
          style={{
            fontSize: 132,
            fontWeight: 800,
            color: theme.ink,
            letterSpacing: -2,
          }}
        >
          Conduit<span style={{color: theme.accent}}>AI</span>
        </div>
      </Reveal>

      <Reveal delay={16} distance={24} style={{textAlign: 'center'}}>
        <div
          style={{
            fontSize: 40,
            fontWeight: 500,
            color: theme.subtle,
            marginTop: 28,
          }}
        >
          Customer data onboarding, made repeatable.
        </div>
      </Reveal>
    </AbsoluteFill>
  );
};
