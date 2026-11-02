import { definePlugin } from "@decky/api";
import {
  ButtonItem,
  DialogButton,
  Focusable,
  PanelSection,
  PanelSectionRow,
} from "@decky/ui";
import {
  FaClock,
  FaCog,
  FaGamepad,
  FaPowerOff,
  FaSlidersH,
  FaVolumeUp,
} from "react-icons/fa";
import { useState, type ReactNode } from "react";
import { dispatchIntent } from "./api";
import { GameSection } from "./components/GameSection";
import { LateNightSection } from "./components/LateNightSection";
import { IconButton } from "./components/IconButton";
import { ModeSection } from "./components/ModeSection";
import { ProfilesSection } from "./components/ProfilesSection";
import { ReceiverSection } from "./components/ReceiverSection";
import { ScheduleSection } from "./components/ScheduleSection";
import { SleepWakeSection } from "./components/SleepWakeSection";
import { VolumeSection } from "./components/VolumeSection";
import { VolumeLimitsSection } from "./components/VolumeLimitsSection";
import { useAvr } from "./hooks/useAvr";

type TabId = "now" | "game" | "auto" | "setup";

const receiverReportUrl =
  "https://github.com/bnm12/decky-avr-control/issues/new?template=receiver-report.yml";

const gameName = (appid: number): string => {
  try {
    const overview: any = appStore.GetAppOverviewByAppID(appid);
    return overview?.display_name ?? `App ${appid}`;
  } catch {
    return `App ${appid}`;
  }
};

const syncRunningGame = async () => {
  try {
    const app: any = SteamUIStore.MainRunningApp;
    await dispatchIntent({
      type: "set_current_game",
      appid: app?.appid ?? 0,
      name: app?.display_name ?? (app?.appid ? gameName(app.appid) : ""),
    });
  } catch {
    // Steam game notifications remain available if the current-app lookup fails.
  }
};

function Content() {
  const avr = useAvr();
  const [activeTab, setActiveTab] = useState<TabId>("now");
  const snapshot = avr.snapshot;
  const configured = snapshot?.connection.configured ?? false;
  const summaryParts = snapshot && configured
    ? [
        snapshot.schedule.enabled
          ? `Late night ${snapshot.schedule.start}–${snapshot.schedule.end}`
          : snapshot.caps?.late_night
            ? "Late night on"
            : null,
        snapshot.sleep_wake.wake_on_resume ? "Wakes with Steam" : null,
        snapshot.sleep_wake.off_on_suspend ? "Powers off on sleep" : null,
        snapshot.profiles.length > 0
          ? `${snapshot.profiles.length} game profile${snapshot.profiles.length === 1 ? "" : "s"}`
          : null,
      ].filter((part): part is string => part !== null)
    : [];
  const summary = summaryParts.join(" · ");

  const tabs: { id: TabId; label: string; icon: ReactNode }[] = [
    { id: "now", label: "Now", icon: <FaSlidersH aria-hidden="true" /> },
    { id: "game", label: "Game", icon: <FaGamepad aria-hidden="true" /> },
    { id: "auto", label: "Auto", icon: <FaClock aria-hidden="true" /> },
    { id: "setup", label: "Setup", icon: <FaCog aria-hidden="true" /> },
  ];

  return (
    <>
      <VolumeSection avr={avr} />
      {summary && (
        <PanelSection>
          <PanelSectionRow>
            <div
              style={{
                color: "#aeb9c4",
                fontSize: 11,
                maxWidth: 310,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                width: "100%",
              }}
              title={summary}
            >
              {summary}
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}
      {(avr.error || avr.note) && (
        <PanelSection>
          {avr.error && (
            <PanelSectionRow>
              <div role="alert" style={{ color: "#ff6b6b" }}>
                {avr.error}
              </div>
            </PanelSectionRow>
          )}
          {avr.note && (
            <PanelSectionRow>
              <div aria-live="polite">{avr.note}</div>
            </PanelSectionRow>
          )}
        </PanelSection>
      )}
      {snapshot && !configured && <ReceiverSection avr={avr} />}
      {snapshot && configured && (
        <>
          <PanelSection>
            <PanelSectionRow>
              <Focusable
                flow-children="horizontal"
                style={{
                  display: "flex",
                  gap: 3,
                  minWidth: 0,
                  width: "100%",
                }}
              >
                {tabs.map((tab) => {
                  const selected = activeTab === tab.id;
                  return (
                    <DialogButton
                      key={tab.id}
                      focusable
                      onClick={() => setActiveTab(tab.id)}
                      style={{
                        alignItems: "center",
                        background: selected ? "#304b62" : "transparent",
                        border: `1px solid ${selected ? "#6586a0" : "transparent"}`,
                        boxSizing: "border-box",
                        color: selected ? "#fff" : "#aab7c2",
                        display: "flex",
                        flex: 1,
                        flexDirection: "column",
                        fontSize: 10,
                        gap: 2,
                        justifyContent: "center",
                        minWidth: 0,
                        padding: "5px 2px",
                      }}
                    >
                      <span style={{ fontSize: 15, lineHeight: 1 }}>
                        {tab.icon}
                      </span>
                      <span>{tab.label}</span>
                    </DialogButton>
                  );
                })}
              </Focusable>
            </PanelSectionRow>
          </PanelSection>
          {activeTab === "now" && (
            <>
              <ModeSection avr={avr} />
              {snapshot.capabilities.includes("power") && (
                <PanelSection title="Power">
                  <PanelSectionRow>
                    <IconButton
                      icon={<FaPowerOff aria-hidden="true" />}
                      label={
                        snapshot.receiver.power === null
                          ? "Power unknown; turn receiver on"
                          : `Power ${snapshot.receiver.power ? "on" : "off"}; turn receiver ${snapshot.receiver.power ? "off" : "on"}`
                      }
                      text={
                        snapshot.receiver.power === null
                          ? "Power unknown"
                          : `Power ${snapshot.receiver.power ? "On" : "Off"}`
                      }
                      active={snapshot.receiver.power === true}
                      onClick={() =>
                        void avr.send({
                          type: "set_power",
                          on: snapshot.receiver.power !== true,
                        }, "power")
                      }
                    />
                  </PanelSectionRow>
                </PanelSection>
              )}
              <LateNightSection avr={avr} />
            </>
          )}
          {activeTab === "game" && (
            <>
              <GameSection avr={avr} />
              <ProfilesSection avr={avr} />
            </>
          )}
          {activeTab === "auto" && (
            <>
              <ScheduleSection avr={avr} />
              <SleepWakeSection avr={avr} />
            </>
          )}
          {activeTab === "setup" && (
            <>
              <ReceiverSection avr={avr} />
              <VolumeLimitsSection avr={avr} />
              <AboutSection />
            </>
          )}
        </>
      )}
    </>
  );
}

function AboutSection() {
  return (
    <PanelSection title="About AVR Control">
      <PanelSectionRow>
        <div>
          eISCP is hardware-verified on the Pioneer VSX-935. Experimental
          drivers: Yamaha YXC and YNC, Sony ScalarWebAPI, Denon/Marantz, HEOS,
          legacy Pioneer telnet, UPnP/Sonos, and Home Assistant. These are
          simulator-tested only.
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <div>
          To help improve receiver support, send a receiver report with your
          model and driver details.
        </div>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() => window.open(receiverReportUrl, "_blank")}
        >
          Send a receiver report
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}

export default definePlugin(() => {
  const registrations: { unregister: () => void }[] = [];
  try {
    registrations.push(
      SteamClient.GameSessions.RegisterForAppLifetimeNotifications(
        (notification) => {
          try {
            if (notification.bRunning) {
              void dispatchIntent({
                type: "game_started",
                appid: notification.unAppID,
                name: gameName(notification.unAppID),
              });
            } else {
              void dispatchIntent({
                type: "game_stopped",
                appid: notification.unAppID,
              });
            }
          } catch {
            // Game notifications must not interrupt the Steam UI.
          }
        },
      ),
    );
  } catch (error) {
    console.error("AVR Control: game detection unavailable", error);
  }
  try {
    registrations.push(
      SteamClient.System.RegisterForOnResumeFromSuspend(() => {
        try {
          void dispatchIntent({ type: "system_resumed" }).catch(() => {});
        } catch {
          // Resume notifications must not interrupt the Steam UI.
        }
      }),
    );
  } catch (error) {
    console.error("AVR Control: resume detection unavailable", error);
  }
  try {
    registrations.push(
      SteamClient.System.RegisterForOnSuspendRequest(() => {
        try {
          void dispatchIntent({ type: "system_suspending" }).catch(() => {});
        } catch {
          // Suspend notifications must not interrupt the Steam UI.
        }
      }),
    );
  } catch (error) {
    console.error("AVR Control: suspend detection unavailable", error);
  }
  void syncRunningGame();

  return {
    name: "AVR Control",
    titleView: <div>AVR Control</div>,
    content: <Content />,
    icon: <FaVolumeUp />,
    onDismount() {
      for (const registration of registrations) {
        try {
          registration.unregister();
        } catch {
          // Steam's internal registration can disappear during client shutdown.
        }
      }
    },
  };
});
