import { PanelSection, PanelSectionRow } from "@decky/ui";
import { FaTrash } from "react-icons/fa";
import { IconButton } from "./IconButton";
import { useAvr } from "../hooks/useAvr";
import { formatVolume } from "./format";

type Props = { avr: ReturnType<typeof useAvr> };

export function ProfilesSection({ avr }: Props) {
  const profiles = avr.snapshot?.profiles ?? [];
  const unit = avr.snapshot?.scale?.unit;
  return (
    <PanelSection title="Profiles">
      {profiles.length === 0 ? (
        <PanelSectionRow>
          <div>No saved game profiles yet.</div>
        </PanelSectionRow>
      ) : (
        profiles.map((profile) => {
          const description = [
            profile.volume_display !== null
              ? profile.same_receiver && unit
                  ? formatVolume(profile.volume_display, unit)
                : "Volume from other receiver"
              : null,
            profile.mode_label,
          ]
            .filter(Boolean)
            .join(", ");
          return (
            <PanelSectionRow key={profile.appid}>
                <div
                  style={{
                    alignItems: "center",
                    display: "flex",
                    gap: 8,
                    minHeight: 40,
                    minWidth: 0,
                    width: "100%",
                }}
              >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 12,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {profile.name}
                    </div>
                    {description && (
                      <div
                        style={{
                          color: "#aeb9c4",
                          fontSize: 10,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {description}
                      </div>
                    )}
                  </div>
                  <IconButton
                    className="avr-icon-button"
                    icon={<FaTrash aria-hidden="true" />}
                    label={`Forget ${profile.name} profile`}
                    onClick={() => {
                      void avr.send({
                        type: "forget_game",
                        appid: profile.appid,
                      });
                    }}
                  />
                </div>
              </PanelSectionRow>
            );
        })
      )}
    </PanelSection>
  );
}
