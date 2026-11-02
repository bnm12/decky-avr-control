import {
  Dropdown,
  Focusable,
  PanelSection,
  PanelSectionRow,
} from "@decky/ui";
import { FaGamepad } from "react-icons/fa";
import { IconButton } from "./IconButton";
import { useAvr } from "../hooks/useAvr";

type Props = { avr: ReturnType<typeof useAvr> };

export function ModeSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot) return null;

  const supportsMode = snapshot.capabilities.includes("mode");
  const { current, selectable } = snapshot.modes;
  const game = snapshot.game;
  if (!supportsMode && !game) return null;
  if (supportsMode && selectable.length === 0 && !current && !game) return null;

  const currentIsSelectable =
    !!current && selectable.some((option) => option.code === current.code);
  const unavailable = !supportsMode || (!current && selectable.length === 0);
  const gameButton = game && (
    <IconButton
      className="avr-icon-button"
      icon={<FaGamepad aria-hidden="true" />}
      label={`Remember listening mode for ${game.name}`}
      onClick={() =>
        void avr.send({
          type: "remember_for_game",
          what: "mode",
          on: !game.remembers_mode,
        })
      }
      active={game.remembers_mode}
      disabled={!supportsMode || (!current && !game.remembers_mode)}
    />
  );

  return (
    <PanelSection>
      {supportsMode && selectable.length > 0 && (
        <PanelSectionRow>
          <div style={{ minWidth: 0, width: "100%" }}>
            <div style={{ marginBottom: 4 }}>Listening mode</div>
            <Focusable
              flow-children="horizontal"
              style={{
                alignItems: "center",
                display: "flex",
                gap: 6,
                minWidth: 0,
                width: "100%",
              }}
            >
              <div
                className="avr-mode-dropdown"
                style={{ flex: "1 1 0%", minWidth: 0 }}
              >
                <Dropdown
                  focusable
                  menuLabel="Listening mode"
                  rgOptions={selectable.map((option) => ({
                    data: option.code,
                    label: option.label,
                  }))}
                  selectedOption={currentIsSelectable ? current!.code : ""}
                  onChange={(option: { data: string }) => {
                    void avr.send(
                      { type: "set_mode", code: option.data },
                      "mode",
                    );
                  }}
                />
              </div>
              {gameButton}
            </Focusable>
            {current && !currentIsSelectable && (
              <div style={{ marginTop: 4 }}>Current mode: {current.label}</div>
            )}
          </div>
        </PanelSectionRow>
      )}
      {supportsMode && selectable.length === 0 && current && (
        <PanelSectionRow>
          <div style={{ minWidth: 0, width: "100%" }}>
            <div>Listening mode</div>
            <div style={{ alignItems: "center", display: "flex", gap: 6 }}>
              <span style={{ flex: 1, minWidth: 0 }}>
                {current.label}
              </span>
              {gameButton}
            </div>
          </div>
        </PanelSectionRow>
      )}
      {game && unavailable && (
        <PanelSectionRow>
          <div style={{ minWidth: 0, width: "100%" }}>
            <div>Listening mode unavailable.</div>
            <Focusable
              flow-children="horizontal"
              style={{
                display: "flex",
                justifyContent: "flex-end",
                minWidth: 0,
                width: "100%",
              }}
            >
              {gameButton}
            </Focusable>
          </div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
