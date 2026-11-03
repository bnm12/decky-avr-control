import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  TextField,
} from "@decky/ui";
import { useAvr } from "../hooks/useAvr";
import { formatVolume } from "./format";

type Props = { avr: ReturnType<typeof useAvr> };

export function VolumeLimitsSection({ avr }: Props) {
  const { snapshot } = avr;
  if (!snapshot?.scale || !snapshot.capabilities.includes("volume")) return null;

  return (
    <PanelSection title="Volume limits">
      <PanelSectionRow>
        <TextField
          label={formatVolume(null, snapshot.scale.unit, "Volume cap")}
          value={avr.capText}
          onChange={(event) => avr.setCapText(event.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label={formatVolume(null, snapshot.scale.unit, "Late night cap")}
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
