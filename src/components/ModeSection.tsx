import {
  DropdownItem,
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

  return (
    <PanelSection>
      {supportsMode && selectable.length > 0 && (
        <PanelSectionRow>
          <DropdownItem
            label="Listening mode"
            description={
              game?.remembers_mode ? `Saved for ${game.name}` : undefined
            }
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
        </PanelSectionRow>
      )}
      {supportsMode &&
        selectable.length > 0 &&
        current &&
        !currentIsSelectable && (
        <PanelSectionRow>
          <div>Current mode: {current.label}</div>
        </PanelSectionRow>
      )}
      {supportsMode && selectable.length === 0 && current && (
        <PanelSectionRow>
          <div>
            {game?.remembers_mode
              ? `Saved for ${game.name}`
              : `Current mode: ${current.label}`}
          </div>
        </PanelSectionRow>
      )}
      {game && unavailable && (
        <PanelSectionRow>
          <div>Listening mode unavailable.</div>
        </PanelSectionRow>
      )}
      {game && (
        <PanelSectionRow>
          <Focusable
            flow-children="horizontal"
            style={{
              display: "flex",
              justifyContent: "flex-end",
              minWidth: 0,
              width: "100%",
            }}
          >
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
          </Focusable>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
