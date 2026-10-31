import { DropdownItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useAvr } from "../hooks/useAvr";

type Props = { avr: ReturnType<typeof useAvr> };

export function ModeSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot?.capabilities.includes("mode")) return null;
  const { current, selectable } = snapshot.modes;
  if (selectable.length === 0 && !current) return null;
  const currentIsSelectable = !!current &&
    selectable.some((option) => option.code === current.code);

  return (
    <PanelSection title="Listening mode">
      {current && !currentIsSelectable && (
        <PanelSectionRow>
          <div>Current mode: {current.label}</div>
        </PanelSectionRow>
      )}
      {selectable.length > 0 && (
        <PanelSectionRow>
          <DropdownItem
            label="Listening mode"
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
      {current && currentIsSelectable && (
        <PanelSectionRow>
          <div>Current mode: {current.label}</div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
