import {
  PanelSection,
  PanelSectionRow,
  ToggleField,
} from "@decky/ui";
import { useAvr } from "../hooks/useAvr";

type Props = { avr: ReturnType<typeof useAvr> };

export function LateNightSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot?.scale || !snapshot.capabilities.includes("volume")) return null;
  const { caps, receiver, capabilities, schedule } = snapshot;
  const standby = capabilities.includes("power") && receiver.power === false;
  if (!caps || standby) return null;

  return (
    <PanelSection title="Late night mode">
      <PanelSectionRow>
        <ToggleField
          label="Late night mode"
          description={
            schedule.enabled
              ? `Time-of-day schedule: ${schedule.start}–${schedule.end}`
              : caps.late_night
                ? "Late night mode is on"
                : "Lowers volume to the late night cap"
          }
          checked={caps.late_night}
          onChange={(on: boolean) => {
            void avr.send({ type: "set_late_night", on });
          }}
        />
      </PanelSectionRow>
    </PanelSection>
  );
}
