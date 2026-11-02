import {
  Focusable,
  PanelSection,
  PanelSectionRow,
  SliderField,
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

export function VolumeSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot) return null;

  const { receiver, scale, caps, capabilities } = snapshot;
  const has = (capability: string) => capabilities.includes(capability);
  const standby = has("power") && receiver.power === false;
  const hasVolume = has("volume") && !!scale && !!caps;
  const hasControl = hasVolume || has("mute") || has("power");
  if (!standby && !hasControl) return null;

  const muteButton = has("mute") && (
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
  );
  const powerButton = has("power") && (
    <IconButton
      className="avr-icon-button"
      icon={<FaPowerOff aria-hidden="true" />}
      label={receiver.power === true ? "Power off" : "Power on"}
      onClick={() =>
        void avr.send({
          type: "set_power",
          on: receiver.power !== true,
        }, "power")
      }
      active={receiver.power === true}
    />
  );
  const gameButton = snapshot.game && hasVolume && (
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
  );

  return (
    <PanelSection>
      {standby ? (
        <PanelSectionRow>
          <div
            style={{
              alignItems: "center",
              display: "flex",
              justifyContent: "space-between",
              minWidth: 0,
              width: "100%",
            }}
          >
            <span>Receiver is in standby</span>
            <Focusable
              flow-children="horizontal"
              preferredFocus
              style={{ display: "flex", justifyContent: "flex-end" }}
            >
              {powerButton}
            </Focusable>
          </div>
        </PanelSectionRow>
      ) : (
        <>
          {hasVolume && scale && caps && (
            <PanelSectionRow>
              <div
                style={{
                  alignItems: "flex-start",
                  display: "flex",
                  justifyContent: "space-between",
                  minWidth: 0,
                  width: "100%",
                }}
              >
                <span>Volume</span>
                <div style={{ minWidth: 0, textAlign: "right" }}>
                  <div
                    style={{
                      fontSize: "1.4em",
                      fontVariantNumeric: "tabular-nums",
                      fontWeight: 600,
                    }}
                  >
                    {receiver.volume === null
                      ? "—"
                      : formatVolume(receiver.volume.display, scale.unit)}
                  </div>
                  {snapshot.game?.remembers_volume && (
                    <div style={{ color: "#aeb9c4", fontSize: 10 }}>
                      Saved for {snapshot.game.name}
                    </div>
                  )}
                </div>
              </div>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <Focusable
              flow-children="horizontal"
              preferredFocus={hasVolume}
              style={{
                alignItems: "center",
                display: "flex",
                gap: 6,
                minWidth: 0,
                width: "100%",
              }}
            >
              {hasVolume && scale && caps && (
                <div
                  style={{
                    flex: "1 1 0%",
                    minWidth: 0,
                    maxWidth: "100%",
                    overflow: "hidden",
                  }}
                >
                  <SliderField
                    className="avr-volume-slider"
                    label=""
                    value={receiver.volume?.display ?? scale.min_display}
                    min={scale.min_display}
                    max={caps.effective_max_display}
                    step={scale.step_display}
                    disabled={receiver.volume === null}
                    showValue={false}
                    bottomSeparator="none"
                    onChange={avr.changeVolume}
                  />
                </div>
              )}
              {muteButton}
              {gameButton}
              {powerButton}
            </Focusable>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}
