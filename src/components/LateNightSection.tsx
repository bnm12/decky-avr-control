import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
} from "@decky/ui";
import { useAvr } from "../hooks/useAvr";
import { formatVolume } from "./format";

type Props = { avr: ReturnType<typeof useAvr> };

export function LateNightSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot?.scale || !snapshot.capabilities.includes("volume")) return null;
  const { scale, caps, receiver, capabilities } = snapshot;
  const standby =
    capabilities.includes("power") && receiver.power === false;

  return (
    <PanelSection title="Volume caps">
      {!standby && caps && (
        <PanelSectionRow>
          <ToggleField
            label="Late night mode"
            description={
              caps.late_night
                ? formatVolume(caps.late_cap_display, scale.unit, "Volume capped at")
                : formatVolume(
                    null,
                    scale.unit,
                    "Lowers the volume and caps it at",
                  )
            }
            checked={caps.late_night}
            onChange={(on: boolean) => {
              void avr.send({ type: "set_late_night", on });
            }}
          />
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <TextField
          label={formatVolume(null, scale.unit, "Max volume")}
          value={avr.capText}
          onChange={(event) => avr.setCapText(event.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label={formatVolume(null, scale.unit, "Late night cap")}
          value={avr.lateCapText}
          onChange={(event) => avr.setLateCapText(event.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => void avr.saveCaps()}>
          Save volume caps
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
