import React from 'react';
import {AbsoluteFill, Audio, Sequence, staticFile} from 'remotion';
import {theme} from './theme';
import {TitleScene} from './scenes/TitleScene';
import {ProblemScene} from './scenes/ProblemScene';
import {SolutionFlowScene} from './scenes/SolutionFlowScene';
import {PrincipleScene} from './scenes/PrincipleScene';
import {BenchmarkScene} from './scenes/BenchmarkScene';
import {OutroScene} from './scenes/OutroScene';

const SCENES = [
  {Component: TitleScene, duration: 120},
  {Component: ProblemScene, duration: 210},
  {Component: SolutionFlowScene, duration: 450},
  {Component: PrincipleScene, duration: 180},
  {Component: BenchmarkScene, duration: 240},
  {Component: OutroScene, duration: 150},
];

export const Teaser: React.FC = () => {
  let cursor = 0;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: theme.bg,
        fontFamily: theme.fontFamily,
      }}
    >
      <Audio src={staticFile('theme.wav')} volume={0.8} />
      {SCENES.map(({Component, duration}, i) => {
        const from = cursor;
        cursor += duration;
        return (
          <Sequence key={i} from={from} durationInFrames={duration}>
            <Component />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
