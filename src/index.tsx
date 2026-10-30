import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  SliderField,
  TextField,
  ToggleField,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { FaVolumeUp } from "react-icons/fa";

type Game = { appid: number; name: string; volume: boolean; mode: boolean };
type GameProfile = {
  appid: number;
  name: string;
  volume: number | null;
  mode: string | null;
  mode_name?: string | null;
};
type ModeOption = { code: string; name: string };
type State = {
  ok: boolean;
  volume?: number;
  muted?: boolean;
  max?: number; // effective slider max (includes late-night cap)
  cap?: number; // normal cap
  late?: boolean;
  late_cap?: number;
  zero?: number;
  mode?: string | null; // display name
  mode_code?: string | null;
  modes?: ModeOption[];
  game?: Game | null;
  error?: string;
};
type Receiver = { ip: string; model: string; id: string };
type Result = { ok: boolean; error?: string };

const getState = callable<[], State>("get_state");
const setVolume = callable<[number], State>("set_volume");
const setMute = callable<[boolean], State>("set_mute");
const getHost = callable<[], string>("get_host");
const setHost = callable<[string], void>("set_host");
const setMax = callable<[number], void>("set_max");
const setLateMax = callable<[number], void>("set_late_max");
const setMode = callable<[string], Result>("set_mode");
const setLateNight = callable<[boolean], Result>("set_late_night");
const discover = callable<[], Receiver[]>("discover");
const selectReceiver = callable<[string, string], void>("select_receiver");
const setCurrentGame = callable<[number, string], void>("set_current_game");
const gameStarted = callable<[number, string], { applied: boolean }>("game_started");
const gameStopped = callable<[number], void>("game_stopped");
const setGameRemember = callable<[string, boolean], Result>("set_game_remember");
const listGames = callable<[], GameProfile[]>("list_games");
const forgetGame = callable<[number], void>("forget_game");

// 0.5 dB per step; `zero` is the raw value that equals 0 dB.
const toDb = (v: number, zero: number) => {
  const d = (v - zero) / 2;
  return `${d > 0 ? "+" : ""}${d.toFixed(1)} dB`;
};
// Caps are entered in dB; the backend stores raw receiver units.
const fmtDb = (raw: number, zero: number) => String((raw - zero) / 2);
const dbToRaw = (text: string, zero: number): number | null => {
  const d = parseFloat(text.replace(",", ".")); // accept 1,5 as well as 1.5
  return Number.isNaN(d) ? null : Math.round(zero + d * 2);
};

const gameName = (appid: number): string => {
  try {
    const o: any = appStore.GetAppOverviewByAppID(appid);
    return o?.display_name ?? `App ${appid}`;
  } catch {
    return `App ${appid}`;
  }
};

// If a game is already running when the plugin loads or the panel opens.
const syncRunningGame = async () => {
  try {
    const app: any = SteamUIStore.MainRunningApp;
    if (app?.appid) await setCurrentGame(app.appid, app.display_name ?? gameName(app.appid));
    else await setCurrentGame(0, "");
  } catch {
    /* not available: game events still work */
  }
};

function Content() {
  const [vol, setVol] = useState(0);
  const [max, setMaxState] = useState(100);
  const [maxText, setMaxText] = useState("-32");
  const [lateText, setLateText] = useState("-50");
  const [zero, setZero] = useState(164);
  const [muted, setMuted] = useState(false);
  const [lateOn, setLateOn] = useState(false);
  const [mode, setModeLabel] = useState<string | null>(null);
  const [modeCode, setModeCode] = useState<string | null>(null);
  const [modes, setModes] = useState<ModeOption[]>([]);
  const [game, setGame] = useState<Game | null>(null);
  const [profiles, setProfiles] = useState<GameProfile[]>([]);
  const [host, setHostState] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [found, setFound] = useState<Receiver[]>([]);
  const [searching, setSearching] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const lastLocal = useRef(0); // time of the last change made in this panel
  const savedHost = useRef("");

  // full=true also (re)fills the settings fields; polling must not clobber typing.
  const refresh = async (full: boolean) => {
    const r = await getState();
    if (!r.ok) {
      if (savedHost.current) setErr(r.error ?? "Unknown error");
      return;
    }
    const z = r.zero ?? 164;
    setErr(null);
    setVol(r.volume ?? 0);
    setMuted(!!r.muted);
    setMaxState(r.max ?? 100);
    setZero(z);
    setModeLabel(r.mode ?? null);
    setModeCode(r.mode_code ?? null);
    setModes(r.modes ?? []);
    setGame(r.game ?? null);
    setLateOn(!!r.late);
    if (full) {
      setMaxText(fmtDb(r.cap ?? 100, z));
      setLateText(fmtDb(r.late_cap ?? 64, z));
      setProfiles(await listGames());
    }
  };

  const choose = async (r: Receiver) => {
    await selectReceiver(r.ip, r.id);
    savedHost.current = r.ip;
    setHostState(r.ip);
    setNote(`Using ${r.model} at ${r.ip}`);
    await refresh(true);
  };

  const search = async (auto: boolean) => {
    setSearching(true);
    setNote(null);
    try {
      const list = await discover();
      setFound(list);
      if (list.length === 0) {
        setNote("No receiver found. Enter its IP address manually.");
      } else if (auto && list.length === 1) {
        await choose(list[0]);
      }
    } finally {
      setSearching(false);
    }
  };

  useEffect(() => {
    let stop = false;
    let t: number | undefined;
    (async () => {
      const h = await getHost();
      savedHost.current = h;
      setHostState(h);
      await syncRunningGame();
      if (h) await refresh(true);
      else search(true); // first run: look for the receiver automatically
      // Live sync: follow changes made with the remote or the Pioneer app.
      const loop = async () => {
        if (stop) return;
        if (savedHost.current && Date.now() - lastLocal.current > 2000) {
          await refresh(false);
        }
        if (!stop) t = window.setTimeout(loop, 2000);
      };
      t = window.setTimeout(loop, 2000);
    })();
    return () => {
      stop = true;
      window.clearTimeout(t);
    };
  }, []);

  const toggleRemember = async (what: "volume" | "mode", c: boolean) => {
    if (!game) return;
    setGame({ ...game, [what]: c });
    const r = await setGameRemember(what, c);
    if (!r.ok) {
      setErr(r.error ?? "Unknown error");
      setGame({ ...game, [what]: !c });
    }
    setProfiles(await listGames());
  };

  const onSlide = (v: number) => {
    lastLocal.current = Date.now();
    setVol(v);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      const r = await setVolume(v);
      lastLocal.current = Date.now();
      if (!r.ok) setErr(r.error ?? "Unknown error");
      else setErr(null);
    }, 150);
  };

  return (
    <>
      <PanelSection title="AVR Volume">
        <PanelSectionRow>
          <SliderField
            label={`Volume (${toDb(vol, zero)})`}
            value={vol}
            min={0}
            max={max}
            step={1}
            showValue={false}
            onChange={onSlide}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Mute"
            checked={muted}
            onChange={async (c: boolean) => {
              lastLocal.current = Date.now();
              setMuted(c);
              const r = await setMute(c);
              if (!r.ok) setErr(r.error ?? "Unknown error");
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Late night mode"
            description={
              lateOn
                ? `Volume capped at ${toDb(max, zero)}`
                : `Lowers the volume and caps it at ${lateText} dB`
            }
            checked={lateOn}
            onChange={async (c: boolean) => {
              lastLocal.current = Date.now();
              setLateOn(c);
              const r = await setLateNight(c);
              if (!r.ok) setErr(r.error ?? "Unknown error");
              await refresh(false);
            }}
          />
        </PanelSectionRow>
        {modeCode && (
          <PanelSectionRow>
            <DropdownItem
              label="Listening mode"
              rgOptions={[
                ...modes.map((m) => ({ data: m.code, label: m.name })),
                // a mode we have no name for: still show it as the current one
                ...(modes.some((m) => m.code === modeCode)
                  ? []
                  : [{ data: modeCode, label: mode ?? `Mode ${modeCode}` }]),
              ]}
              selectedOption={modeCode}
              onChange={async (opt: { data: string }) => {
                lastLocal.current = Date.now();
                setModeCode(opt.data);
                const r = await setMode(opt.data);
                if (!r.ok) setErr(r.error ?? "Unknown error");
                else setErr(null);
                lastLocal.current = Date.now();
                if (!r.ok) await refresh(false); // snap back to the real mode
              }}
            />
          </PanelSectionRow>
        )}
        {err && (
          <PanelSectionRow>
            <div style={{ color: "#ff6b6b" }}>{err}</div>
          </PanelSectionRow>
        )}
      </PanelSection>

      {game && (
        <PanelSection title={`This game: ${game.name}`}>
          <PanelSectionRow>
            <ToggleField
              label="Remember volume"
              description="Restored on launch, updated as you change it"
              checked={game.volume}
              onChange={(c: boolean) => toggleRemember("volume", c)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Remember listening mode"
              description={mode ? `Currently ${mode}` : "Not reported by this receiver"}
              checked={game.mode}
              disabled={!mode && !game.mode}
              onChange={(c: boolean) => toggleRemember("mode", c)}
            />
          </PanelSectionRow>
        </PanelSection>
      )}

      {profiles.length > 0 && (
        <PanelSection title="Saved game profiles">
          {profiles.map((p) => (
            <PanelSectionRow key={p.appid}>
              <ButtonItem
                layout="below"
                description={[
                  p.volume !== null ? toDb(p.volume, zero) : null,
                  p.mode ? (p.mode_name ?? `Mode ${p.mode}`) : null,
                ]
                  .filter(Boolean)
                  .join(", ")}
                onClick={async () => {
                  await forgetGame(p.appid);
                  setProfiles(await listGames());
                  if (game && game.appid === p.appid) {
                    setGame({ ...game, volume: false, mode: false });
                  }
                }}
              >
                Forget {p.name}
              </ButtonItem>
            </PanelSectionRow>
          ))}
        </PanelSection>
      )}

      <PanelSection title="Receiver">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={searching}
            onClick={() => search(false)}
          >
            {searching ? "Searching…" : "Search network"}
          </ButtonItem>
        </PanelSectionRow>
        {note && (
          <PanelSectionRow>
            <div>{note}</div>
          </PanelSectionRow>
        )}
        {found.map((r) => (
          <PanelSectionRow key={r.id}>
            <ButtonItem layout="below" onClick={() => choose(r)}>
              {r.model} ({r.ip})
            </ButtonItem>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <TextField
            label="AVR IP address"
            value={host}
            onChange={(e) => setHostState(e.target.value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <TextField
            label="Max volume (dB)"
            value={maxText}
            onChange={(e) => setMaxText(e.target.value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <TextField
            label="Late night max volume (dB)"
            value={lateText}
            onChange={(e) => setLateText(e.target.value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              await setHost(host);
              savedHost.current = host.trim();
              const n = dbToRaw(maxText, zero);
              if (n !== null) await setMax(n);
              const l = dbToRaw(lateText, zero);
              if (l !== null) await setLateMax(l);
              await refresh(true);
            }}
          >
            Save &amp; connect
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  // Runs for as long as the plugin is loaded, whether or not the panel is open,
  // so a game's saved profile is applied the moment it launches.
  // Steam's internal APIs can change between client updates: if game detection
  // breaks, only the per-game feature stops, and the plugin itself still loads.
  let reg: { unregister: () => void } | undefined;
  try {
    reg = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((n) => {
      try {
        if (n.bRunning) gameStarted(n.unAppID, gameName(n.unAppID)).catch(() => {});
        else gameStopped(n.unAppID).catch(() => {});
      } catch {
        /* ignore: never let a game event break the UI */
      }
    });
  } catch (e) {
    console.error("Pioneer Volume: game detection unavailable", e);
  }
  syncRunningGame();

  return {
    name: "Pioneer Volume",
    titleView: <div>Pioneer Volume</div>,
    content: <Content />,
    icon: <FaVolumeUp />,
    onDismount() {
      try {
        reg?.unregister();
      } catch {
        /* ignore */
      }
    },
  };
});
