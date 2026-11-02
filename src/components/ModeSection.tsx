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
  if (!snapshot?.capabilities.includes("mode")) return null;
  const { current, selectable } = snapshot.modes;
  if (selectable.length === 0 && !current) return null;
  const currentIsSelectable = !!current &&
    selectable.some((option) => option.code === current.code);
  const game = snapshot.game;
  const gameDescription = game?.remembers_mode
    ? `Saved for ${game.name}`
    : current
      ? `Current mode: ${current.label}`
      : "Not reported by this receiver";

  return (
    <PanelSection title="Listening mode">
      {selectable.length > 0 ? (
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
            <div style={{ flex: 1, minWidth: 0 }}>
              <DropdownItem
                layout="inline"
                label="Listening mode"
                description={gameDescription}
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
            {game && (
              <IconButton
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
                disabled={!current && !game.remembers_mode}
              />
            )}
          </Focusable>
        </PanelSectionRow>
      ) : (
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
            <div style={{ flex: 1, minWidth: 0 }}>
              <div>{`Current mode: ${current!.label}`}</div>
            </div>
            {game && (
              <IconButton
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
                disabled={!current && !game.remembers_mode}
              />
            )}
          </Focusable>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
