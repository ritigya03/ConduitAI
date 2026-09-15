import React from 'react';
import {AbsoluteFill} from 'remotion';
import {theme} from '../theme';
import {Reveal} from '../Reveal';

const MESSY_COLUMNS: {label: string; rotate: number}[] = [
  {label: 'CustID', rotate: -4},
  {label: 'COMPNAME', rotate: 3},
  {label: 'CNTRY', rotate: -2},
  {label: 'natural_key', rotate: 5},
  {label: 'phone_no', rotate: -3},
  {label: 'kyc?', rotate: 2},
  {label: 'acct_num', rotate: -5},
  {label: 'DueDt', rotate: 4},
];

export const ProblemScene: React.FC = () => {
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <Reveal delay={2} distance={20} style={{textAlign: 'center', maxWidth: 1300}}>
        <div style={{fontSize: 56, fontWeight: 700, color: theme.ink}}>
          Every B2B integration project dies here.
        </div>
        <div
          style={{
            fontSize: 30,
            color: theme.subtle,
            marginTop: 18,
            fontWeight: 500,
          }}
        >
          A new customer's CRM export never matches your schema.
        </div>
      </Reveal>

      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          justifyContent: 'center',
          gap: 20,
          marginTop: 64,
          maxWidth: 1200,
        }}
      >
        {MESSY_COLUMNS.map((col, i) => (
          <Reveal key={col.label} delay={16 + i * 4} distance={16}>
            <div
              style={{
                backgroundColor: theme.bgAlt,
                border: `1px solid ${theme.border}`,
                borderRadius: 14,
                padding: '16px 26px',
                fontSize: 26,
                fontWeight: 600,
                color: theme.ink,
                boxShadow: theme.cardShadow,
                transform: `rotate(${col.rotate}deg)`,
                fontFamily:
                  "'SF Mono', 'Menlo', 'Consolas', monospace",
              }}
            >
              {col.label}
            </div>
          </Reveal>
        ))}
      </div>
    </AbsoluteFill>
  );
};
