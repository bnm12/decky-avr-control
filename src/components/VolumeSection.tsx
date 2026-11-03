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
import type { ReactNode } from "react";
import { IconButton } from "./IconButton";
import { useAvr } from "../hooks/useAvr";
import { formatVolume } from "./format";

type Props = {
  avr: ReturnType<typeof useAvr>;
  summary: string;
  loading: boolean;
  children?: ReactNode;
};

export function VolumeSection({ avr, summary, loading, children }: Props) {
  const { snapshot } = avr;
  if (!snapshot) {
    if (!loading && !children) return null;
    return (
      <PanelSection>
        <PanelSectionRow>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 5,
              minWidth: 0,
              width: "100%",
            }}
          >
            {loading && <div style={{ minHeight: 14 }} />}
            {children}
          </div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const { receiver, scale, caps, capabilities, game } = snapshot;
  const has = (capability: string) => capabilities.includes(capability);
  const standby = has("power") && receiver.power === false;
  const hasVolume = has("volume") && !!scale && !!caps;
  const hasControls = hasVolume || has("mute") || has("power");
  if (!standby && !hasControls && !game && !summary && !children) return null;

  const profileDetails = game
    ? [
        game.remembers_volume ? "volume" : null,
        game.remembers_mode ? "listening mode" : null,
      ].filter((detail): detail is string => detail !== null)
    : [];
  const profileLine =
    game && profileDetails.length > 0
      ? `${game.name} profile: ${profileDetails.join(" & ")}`
      : "";

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
  const gameButton = game && hasVolume && (
    <IconButton
      className="avr-icon-button"
      icon={<FaGamepad aria-hidden="true" />}
      label={`Remember volume for ${game.name}`}
      activeColor="#90ba3c"
      onClick={() =>
        void avr.send({
          type: "remember_for_game",
          what: "volume",
          on: !game.remembers_volume,
        })
      }
      active={game.remembers_volume}
    />
  );

  return (
    <PanelSection>
      <PanelSectionRow>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 5,
            minWidth: 0,
            width: "100%",
          }}
        >
          {standby ? (
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
          ) : (
            <>
              {hasVolume && scale && caps && (
                <div
                  style={{
                    alignItems: "baseline",
                    display: "flex",
                    justifyContent: "space-between",
                    minWidth: 0,
                    paddingBottom: 3,
                    width: "100%",
                  }}
                >
                  <span>Volume</span>
                  <span
                    style={{
                      fontSize: "1.4em",
                      fontVariantNumeric: "tabular-nums",
                      fontWeight: 600,
                      textAlign: "right",
                    }}
                  >
                    {receiver.volume === null
                      ? "—"
                      : formatVolume(receiver.volume.display, scale.unit)}
                  </span>
                </div>
              )}
              {hasControls && (
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
                      }}
                    >
                      <SliderField
                        className={`avr-volume-slider${
                          game?.remembers_volume ? " avr-game-override" : ""
                        }`}
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
              )}
            </>
          )}
          {game && (
            <div
              style={{
                color: "#90ba3c",
                fontSize: 10,
                height: 14,
                lineHeight: "14px",
                marginTop: 3,
                minWidth: 0,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                width: "100%",
              }}
            >
              {profileLine}
            </div>
          )}
          {summary && (
            <div
              style={{
                color: "#aeb9c4",
                fontSize: 11,
                lineHeight: "14px",
                maxWidth: 310,
                minHeight: 14,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                width: "100%",
              }}
              title={summary}
            >
              {summary}
            </div>
          )}
          {!summary && loading && <div style={{ minHeight: 14 }} />}
          {children}
        </div>
      </PanelSectionRow>
    </PanelSection>
  );
}
