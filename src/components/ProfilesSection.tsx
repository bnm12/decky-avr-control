import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useAvr } from "../hooks/useAvr";
import { formatVolume } from "./format";

type Props = { avr: ReturnType<typeof useAvr> };

export function ProfilesSection({ avr }: Props) {
  const profiles = avr.snapshot?.profiles ?? [];
  const unit = avr.snapshot?.scale?.unit;
  return (
    <PanelSection title="Saved game profiles">
      {profiles.length === 0 ? (
        <PanelSectionRow>
          <div>No saved game profiles yet.</div>
        </PanelSectionRow>
      ) : (
        profiles.map((profile) => {
          const description = [
            profile.volume_display !== null
              ? profile.same_receiver && unit
                ? formatVolume(profile.volume_display, unit, "Volume")
                : "Volume from other receiver"
              : null,
            profile.mode_label,
          ]
            .filter(Boolean)
            .join(", ");
          return (
            <PanelSectionRow key={profile.appid}>
              <ButtonItem
                layout="below"
                description={description}
                onClick={() => {
                  void avr.send({
                    type: "forget_game",
                    appid: profile.appid,
                  });
                }}
              >
                Forget {profile.name}
              </ButtonItem>
            </PanelSectionRow>
          );
        })
      )}
    </PanelSection>
  );
}
