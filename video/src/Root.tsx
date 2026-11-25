import { CalculateMetadataFunction, Composition } from "remotion";

import sample from "../sample-report.json";
import { ClipProps, clipPlan, planFrames } from "./plan";
import { SeasonClip } from "./SeasonClip";

const FPS = 30;

// the clip is as long as the season's highlights
const calculateMetadata: CalculateMetadataFunction<ClipProps> = ({ props }) => ({
  durationInFrames: planFrames(clipPlan(props.report), FPS),
});

export const RemotionRoot = () => {
  return (
    <Composition
      id="SeasonClip"
      component={SeasonClip}
      durationInFrames={900}
      fps={FPS}
      width={1080}
      height={1080}
      defaultProps={{
        report: sample as unknown as ClipProps["report"],
        handle: "@Danieldoesx",
        site: "https://agentsurvivor.kurult.ai",
      }}
      calculateMetadata={calculateMetadata}
    />
  );
};
