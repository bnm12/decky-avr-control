import {
  PanelSection,
  PanelSectionRow,
  SliderField,
  Focusable,
} from "@decky/ui";
import { FaGamepad, FaVolumeMute, FaVolumeUp } from "react-icons/fa";
import { IconButton } from "./IconButton";
import { useAvr } from "../hooks/useAvr";
import { formatVolume } from "./format";

type Props = { avr: ReturnType<typeof useAvr> };

export function VolumeSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot) return null;
  const { receiver, scale, caps, capabilities } = snapshot;
  const has = (capability: string) => capabilities.includes(capability);
  const standby = has("power") && receiver.power === false;
  const hasControl =
    (has("volume") && !!scale && !!caps) || has("mute");
  if (!standby && !hasControl) return null;

  return (
    <PanelSection>
      {standby && (
        <PanelSectionRow>
          <div>Receiver is in standby</div>
        </PanelSectionRow>
      )}
      {!standby && ((has("volume") && scale && caps) || has("mute")) && (
        <PanelSectionRow>
          <Focusable
            flow-children="horizontal"
            style={{
              alignItems: "center",
              display: "flex",
              gap: 5,
              minWidth: 0,
              width: "100%",
            }}
          >
            {has("volume") && scale && caps && (
              <div style={{ flex: 1, minWidth: 0 }}>
                <SliderField
                  label={
                    receiver.volume === null
                      ? "Volume unknown"
                      : formatVolume(
                          receiver.volume.display,
                          scale.unit,
                          "Volume",
                        )
                  }
                  description={
                    snapshot.game?.remembers_volume
                      ? `Saved for ${snapshot.game.name}`
                      : undefined
                  }
                  value={receiver.volume?.display ?? scale.min_display}
                  min={scale.min_display}
                  max={caps.effective_max_display}
                  step={scale.step_display}
                  disabled={receiver.volume === null}
                  showValue={false}
                  onChange={avr.changeVolume}
                />
              </div>
            )}
            {has("mute") && (
              <IconButton
                icon={
                  receiver.muted
                    ? <FaVolumeMute aria-hidden="true" />
                    : <FaVolumeUp aria-hidden="true" />
                }
                label={
                  receiver.muted === null
                    ? "Mute receiver; mute state unknown"
                    : receiver.muted
                      ? "Unmute receiver"
                      : "Mute receiver"
                }
                onClick={() =>
                  void avr.send({
                    type: "set_mute",
                    muted: receiver.muted !== true,
                  }, "mute")
                }
                active={receiver.muted === true}
              />
            )}
            {snapshot.game && has("volume") && scale && caps && (
              <IconButton
                icon={<FaGamepad aria-hidden="true" />}
                label={`Remember volume for ${snapshot.game.name}`}
                onClick={() =>
                  void avr.send({
                    type: "remember_for_game",
                    what: "volume",
                    on: !snapshot.game?.remembers_volume,
                  })
                }
                active={snapshot.game.remembers_volume}
              />
            )}
          </Focusable>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
