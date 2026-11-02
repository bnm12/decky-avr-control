import {
  PanelSection,
  PanelSectionRow,
  SliderField,
  Focusable,
} from "@decky/ui";
import {
  FaGamepad,
  FaPowerOff,
  FaVolumeMute,
  FaVolumeUp,
} from "react-icons/fa";
import { IconButton } from "./IconButton";
import { useAvr } from "../hooks/useAvr";
import { formatVolume } from "./format";

type Props = { avr: ReturnType<typeof useAvr> };

const visuallyHidden = {
  position: "absolute" as const,
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden" as const,
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap" as const,
  border: 0,
};

export function VolumeSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot) return null;
  const { receiver, scale, caps, capabilities } = snapshot;
  const has = (capability: string) => capabilities.includes(capability);
  const standby = has("power") && receiver.power === false;
  const hasVolume = has("volume") && !!scale && !!caps;
  const hasControl = hasVolume || has("mute") || has("power");
  if (!standby && !hasControl) return null;

  return (
    <PanelSection>
      {standby && (
        <PanelSectionRow>
          <div>Receiver is in standby</div>
        </PanelSectionRow>
      )}
      {!standby && hasVolume && scale && caps && (
        <PanelSectionRow>
          <div style={{ width: "100%" }}>
            <div
              style={{
                fontSize: 12,
                minWidth: 0,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {receiver.volume === null
                ? "Volume unknown"
                : formatVolume(receiver.volume.display, scale.unit, "Volume")}
              {snapshot.game?.remembers_volume
                ? ` · saved for ${snapshot.game.name}`
                : ""}
            </div>
            <SliderField
              label={<span style={visuallyHidden}>Volume</span>}
              value={receiver.volume?.display ?? scale.min_display}
              min={scale.min_display}
              max={caps.effective_max_display}
              step={scale.step_display}
              disabled={receiver.volume === null}
              showValue={false}
              onChange={avr.changeVolume}
            />
          </div>
        </PanelSectionRow>
      )}
      {((standby && has("power")) ||
        (!standby &&
          (has("mute") || (snapshot.game && hasVolume) || has("power")))) && (
        <PanelSectionRow>
          <Focusable
            flow-children="horizontal"
            style={{
              alignItems: "center",
              display: "flex",
              gap: 5,
              justifyContent: "flex-end",
              minWidth: 0,
              width: "100%",
            }}
          >
            {!standby && has("mute") && (
              <IconButton
                className="avr-icon-button"
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
            {!standby && snapshot.game && hasVolume && (
              <IconButton
                className="avr-icon-button"
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
            {has("power") && (
              <IconButton
                className="avr-icon-button"
                icon={<FaPowerOff aria-hidden="true" />}
                label={
                  receiver.power === true ? "Power off" : "Power on"
                }
                onClick={() =>
                  void avr.send({
                    type: "set_power",
                    on: receiver.power !== true,
                  }, "power")
                }
                active={receiver.power === true}
              />
            )}
          </Focusable>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
