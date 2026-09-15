import {Composition} from 'remotion';
import {Teaser} from './Teaser';

const FPS = 30;
const DURATION_IN_SECONDS = 45;

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Teaser"
      component={Teaser}
      durationInFrames={FPS * DURATION_IN_SECONDS}
      fps={FPS}
      width={1920}
      height={1080}
    />
  );
};
