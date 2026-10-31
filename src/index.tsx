import { definePlugin } from "@decky/api";
import { FaVolumeUp } from "react-icons/fa";
import { dispatchIntent } from "./api";
import { GameSection } from "./components/GameSection";
import { LateNightSection } from "./components/LateNightSection";
import { ModeSection } from "./components/ModeSection";
import { ProfilesSection } from "./components/ProfilesSection";
import { ReceiverSection } from "./components/ReceiverSection";
import { VolumeSection } from "./components/VolumeSection";
import { useAvr } from "./hooks/useAvr";

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
  return (
    <>
      <VolumeSection avr={avr} />
      <ModeSection avr={avr} />
      <LateNightSection avr={avr} />
      <ReceiverSection avr={avr} />
      <GameSection avr={avr} />
      <ProfilesSection avr={avr} />
    </>
  );
}

export default definePlugin(() => {
  let registration: { unregister: () => void } | undefined;
  try {
    registration = SteamClient.GameSessions.RegisterForAppLifetimeNotifications(
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
    );
  } catch (error) {
    console.error("AVR Control: game detection unavailable", error);
  }
  void syncRunningGame();

  return {
    name: "AVR Control",
    titleView: <div>AVR Control</div>,
    content: <Content />,
    icon: <FaVolumeUp />,
    onDismount() {
      try {
        registration?.unregister();
      } catch {
        // Steam's internal registration can disappear during client shutdown.
      }
    },
  };
});
