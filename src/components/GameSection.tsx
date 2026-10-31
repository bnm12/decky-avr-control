import { PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { useAvr } from "../hooks/useAvr";

type Props = { avr: ReturnType<typeof useAvr> };

export function GameSection({ avr }: Props) {
  const game = avr.snapshot?.game;
  if (!game) return null;
  return (
    <PanelSection title={`This game: ${game.name}`}>
      <PanelSectionRow>
        <ToggleField
          label="Remember volume"
          description="Restored on launch, updated as you change it"
          checked={game.remembers_volume}
          onChange={(on: boolean) => {
            void avr.send({
              type: "remember_for_game",
              what: "volume",
              on,
            });
          }}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Remember listening mode"
          description={
            avr.snapshot?.modes.current
              ? `Currently ${avr.snapshot.modes.current.label}`
              : "Not reported by this receiver"
          }
          checked={game.remembers_mode}
          disabled={!avr.snapshot?.modes.current && !game.remembers_mode}
          onChange={(on: boolean) => {
            void avr.send({
              type: "remember_for_game",
              what: "mode",
              on,
            });
          }}
        />
      </PanelSectionRow>
    </PanelSection>
  );
}
