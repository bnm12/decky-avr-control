import {
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
} from "@decky/ui";
import { useAvr } from "../hooks/useAvr";
import { formatVolume } from "./format";

type Props = { avr: ReturnType<typeof useAvr> };

export function VolumeSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot) return null;
  const { receiver, scale, caps, capabilities } = snapshot;
  const has = (capability: string) => capabilities.includes(capability);
  const standby = has("power") && receiver.power === false;

  return (
    <PanelSection title="Receiver">
      {has("power") && (
        <PanelSectionRow>
          <ToggleField
            label={receiver.power === null ? "Power (unknown)" : "Power"}
            checked={receiver.power ?? false}
            onChange={(on: boolean) => {
              void avr.send({ type: "set_power", on }, "power");
            }}
          />
        </PanelSectionRow>
      )}
      {standby && (
        <PanelSectionRow>
          <div>Receiver is in standby</div>
        </PanelSectionRow>
      )}
      {!standby && has("volume") && scale && caps && (
        <PanelSectionRow>
          <SliderField
            label={
              receiver.volume === null
                ? "Volume unknown"
                : formatVolume(receiver.volume.display, scale.unit, "Volume")
            }
            value={receiver.volume?.display ?? scale.min_display}
            min={scale.min_display}
            max={caps.effective_max_display}
            step={scale.step_display}
            disabled={receiver.volume === null}
            showValue={false}
            onChange={avr.changeVolume}
          />
        </PanelSectionRow>
      )}
      {!standby && has("mute") && (
        <PanelSectionRow>
          <ToggleField
            label={receiver.muted === null ? "Mute (unknown)" : "Mute"}
            checked={receiver.muted ?? false}
            onChange={(muted: boolean) => {
              void avr.send({ type: "set_mute", muted }, "mute");
            }}
          />
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
