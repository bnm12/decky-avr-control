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
  driver?: string;
};
type ModeOption = { code: string; name: string };
type LocalControl = "volume" | "mute" | "power" | "mode";
type VolumeScale = {
  min: number;
  max: number;
  unit: "dB" | "%";
  zero: number;
  steps_per_unit: number;
  default_cap: number;
};
type State = {
  ok: boolean;
  volume?: number | null;
  muted?: boolean | null;
  power?: boolean | null;
  max?: number;
  cap?: number;
  late?: boolean;
  late_cap?: number;
  mode?: string | null;
  mode_code?: string | null;
  modes?: ModeOption[];
  game?: Game | null;
  driver?: string;
  driver_label?: string;
  scale?: VolumeScale;
  capabilities?: string[];
  error?: string;
};
type Receiver = {
  ip: string;
  model: string;
  id: string;
  label: string;
  driver: string;
};
type ConfigField = {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
};
type DriverOption = { key: string; label: string; fields: ConfigField[] };
type Result = { ok: boolean; error?: string };
type CommandResult<T> = Result & { confirmed?: boolean } & T;
type Connection = {
  configured: boolean;
  driver: string | null;
  label: string | null;
  summary: string;
  fields: Record<string, string>;
};

const getState = callable<[], State>("get_state");
const setVolume = callable<[number], CommandResult<{ volume?: number | null }>>("set_volume");
const setMute = callable<[boolean], CommandResult<{ muted?: boolean | null }>>("set_mute");
const getConnection = callable<[], Connection>("get_connection");
const setMax = callable<[number], void>("set_max");
const setLateMax = callable<[number], void>("set_late_max");
const setMode = callable<
  [string],
  CommandResult<{ mode?: string | null; code?: string | null }>
>("set_mode");
const setPower = callable<[boolean], CommandResult<{ power?: boolean | null }>>("set_power");
const setLateNight = callable<[boolean], Result>("set_late_night");
const discover = callable<[], Receiver[]>("discover");
const selectReceiver = callable<[string, string], void>("select_receiver");
const setCurrentGame = callable<[number, string], void>("set_current_game");
const gameStarted = callable<[number, string], { applied: boolean }>("game_started");
const gameStopped = callable<[number], void>("game_stopped");
const setGameRemember = callable<[string, boolean], Result>("set_game_remember");
const listGames = callable<[], GameProfile[]>("list_games");
const forgetGame = callable<[number], void>("forget_game");
const listDrivers = callable<[], DriverOption[]>("list_drivers");
const configure = callable<[string, Record<string, string>], Result>("configure");

const displayValue = (raw: number, scale: VolumeScale): number =>
  (raw - scale.zero) / scale.steps_per_unit;

const displayText = (raw: number, scale: VolumeScale): string => {
  const value = displayValue(raw, scale);
  const rounded = Number(value.toFixed(4));
  const number = scale.unit === "dB" && rounded > 0 ? `+${rounded}` : `${rounded}`;
  return `${number} ${scale.unit}`;
};

const displayInput = (raw: number, scale: VolumeScale): string =>
  `${Number(displayValue(raw, scale).toFixed(4))}`;

const inputToRaw = (text: string, scale: VolumeScale): number | null => {
  const normalized = text.trim().replace(",", ".");
  if (!normalized || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) return null;
  const value = Number(normalized);
  if (!Number.isFinite(value)) return null;
  return Math.max(
    scale.min,
    Math.min(scale.max, Math.round(scale.zero + value * scale.steps_per_unit)),
  );
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
  const [vol, setVol] = useState<number | null>(null);
  const [max, setMaxState] = useState(100);
  const [maxText, setMaxText] = useState("");
  const [lateText, setLateText] = useState("");
  const [muted, setMuted] = useState<boolean | null>(null);
  const [power, setPowerState] = useState<boolean | null>(null);
  const [lateOn, setLateOn] = useState(false);
  const [mode, setModeLabel] = useState<string | null>(null);
  const [modeCode, setModeCode] = useState<string | null>(null);
  const [modes, setModes] = useState<ModeOption[]>([]);
  const [game, setGame] = useState<Game | null>(null);
  const [profiles, setProfiles] = useState<GameProfile[]>([]);
  const [receiverState, setReceiverState] = useState<State | null>(null);
  const [host, setHostState] = useState("");
  const [connection, setConnection] = useState<Connection | null>(null);
  const [drivers, setDrivers] = useState<DriverOption[]>([]);
  const [selectedDriver, setSelectedDriver] = useState("auto");
  const [manualConfig, setManualConfig] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [found, setFound] = useState<Receiver[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const commandNotAppliedNote = "The receiver didn't apply that change";
  const timer = useRef<number | undefined>(undefined);
  const lastLocal = useRef(0); // time of the last change made in this panel
  const selectionGeneration = useRef(0);
  const controlSequences = useRef<Record<LocalControl, number>>({
    volume: 0,
    mute: 0,
    power: 0,
    mode: 0,
  });
  const localRequestsInFlight = useRef<Record<LocalControl, number>>({
    volume: 0,
    mute: 0,
    power: 0,
    mode: 0,
  });
  const switchingReceiver = useRef(false);
  const connectionRef = useRef<Connection | null>(null);

  const updateCommandNote = (confirmed?: boolean) => {
    if (confirmed === false) setNote(commandNotAppliedNote);
    else setNote((current) => current === commandNotAppliedNote ? null : current);
  };
  const isCurrent = (generation: number) =>
    generation === selectionGeneration.current;
  const startLocalRequest = (control: LocalControl) => {
    localRequestsInFlight.current[control] += 1;
    lastLocal.current = Date.now();
  };
  const finishLocalRequest = (control: LocalControl, generation: number) => {
    if (!isCurrent(generation)) return;
    localRequestsInFlight.current[control] = Math.max(
      0,
      localRequestsInFlight.current[control] - 1,
    );
    lastLocal.current = Date.now();
  };
  const updateConnection = (value: Connection) => {
    connectionRef.current = value;
    setConnection(value);
  };
  const beginReceiverChange = () => {
    selectionGeneration.current += 1;
    switchingReceiver.current = true;
    localRequestsInFlight.current = { volume: 0, mute: 0, power: 0, mode: 0 };
    window.clearTimeout(timer.current);
    timer.current = undefined;
    return selectionGeneration.current;
  };
  const finishReceiverChange = (generation: number) => {
    if (isCurrent(generation)) switchingReceiver.current = false;
  };

  // full=true also (re)fills the settings fields; polling must not clobber typing.
  const refresh = async (full: boolean, generation = selectionGeneration.current) => {
    const sequencesAtStart = { ...controlSequences.current };
    const inFlightAtStart = { ...localRequestsInFlight.current };
    const r = await getState();
    if (!isCurrent(generation)) return;
    const canApplyControl = (control: LocalControl) =>
      sequencesAtStart[control] === controlSequences.current[control] &&
      inFlightAtStart[control] === 0 &&
      localRequestsInFlight.current[control] === 0;
    const canApplyRefreshError =
      full || (["volume", "mute", "power", "mode"] as LocalControl[]).every(canApplyControl);
    if (!r.ok) {
      if (canApplyRefreshError) {
        if (connectionRef.current?.configured) setErr(r.error ?? "Unknown error");
        else setErr(null);
        setReceiverState(null);
      }
      return;
    }
    if (canApplyRefreshError) setErr(null);
    setReceiverState(r);
    if (canApplyControl("volume")) setVol(r.volume ?? null);
    if (canApplyControl("mute")) setMuted(r.muted ?? null);
    setMaxState(r.max ?? r.scale?.max ?? 100);
    if (canApplyControl("power")) setPowerState(r.power ?? null);
    if (canApplyControl("mode")) {
      setModeLabel(r.mode ?? null);
      setModeCode(r.mode_code ?? null);
    }
    setModes(r.modes ?? []);
    setGame(r.game ?? null);
    setLateOn(!!r.late);
    if (full) {
      setSelectedDriver(r.driver ?? "auto");
      if (r.scale) {
        setMaxText(displayInput(r.cap ?? r.scale.default_cap, r.scale));
        setLateText(displayInput(r.late_cap ?? r.scale.default_cap, r.scale));
      }
      const currentProfiles = await listGames();
      if (!isCurrent(generation)) return;
      setProfiles(currentProfiles);
    }
  };

  const choose = async (r: Receiver) => {
    const generation = beginReceiverChange();
    setSaving(false);
    setSearching(false);
    try {
      await selectReceiver(r.ip, r.id);
      if (!isCurrent(generation)) return;
      const selected = await getConnection();
      if (!isCurrent(generation)) return;
      updateConnection(selected);
      const fields = selected.fields ?? {};
      setHostState(fields.host ?? r.ip);
      setManualConfig(fields);
      setSelectedDriver(selected.driver ?? r.driver);
      setNote(`Using ${selected.summary || r.model} at ${fields.host ?? r.ip}`);
      finishReceiverChange(generation);
      await refresh(true, generation);
    } catch (error) {
      if (isCurrent(generation)) setErr(String(error));
    } finally {
      finishReceiverChange(generation);
    }
  };

  const search = async (auto: boolean, generation = selectionGeneration.current) => {
    setSearching(true);
    setNote(null);
    try {
      const list = await discover();
      if (!isCurrent(generation)) return;
      setFound(list);
      if (list.length === 0) {
        setNote("No receiver found. Enter its IP address manually.");
      } else if (auto && !connectionRef.current?.configured && list.length === 1) {
        await choose(list[0]);
      }
    } finally {
      if (isCurrent(generation)) setSearching(false);
    }
  };

  useEffect(() => {
    let stop = false;
    let t: number | undefined;
    const generation = selectionGeneration.current;
    (async () => {
      const [savedConnection, availableDrivers] = await Promise.all([
        getConnection(),
        listDrivers(),
      ]);
      if (stop || !isCurrent(generation)) return;
      updateConnection(savedConnection);
      const fields = savedConnection.fields ?? {};
      setHostState(fields.host ?? "");
      setManualConfig(fields);
      setDrivers(availableDrivers);
      setSelectedDriver(savedConnection.configured ? savedConnection.driver ?? "auto" : "auto");
      await syncRunningGame();
      if (stop || !isCurrent(generation)) return;
      if (savedConnection.configured) await refresh(true, generation);
      else await search(true, generation); // first run: look for the receiver automatically
      // Live sync: follow changes made with the remote or the receiver app.
      const loop = async () => {
        if (stop) return;
        const hasRequestsInFlight = Object.values(localRequestsInFlight.current).some(
          (count) => count > 0,
        );
        if (
          connectionRef.current?.configured &&
          !hasRequestsInFlight &&
          Date.now() - lastLocal.current > 2000
        ) {
          await refresh(false);
        }
        if (!stop) t = window.setTimeout(loop, 2000);
      };
      t = window.setTimeout(loop, 2000);
    })();
    return () => {
      stop = true;
      window.clearTimeout(t);
      window.clearTimeout(timer.current);
    };
  }, []);

  const toggleRemember = async (what: "volume" | "mode", c: boolean) => {
    if (!game) return;
    const generation = selectionGeneration.current;
    setGame({ ...game, [what]: c });
    if (!isCurrent(generation)) return;
    const r = await setGameRemember(what, c);
    if (!isCurrent(generation)) return;
    if (!r.ok) {
      setErr(r.error ?? "Unknown error");
      setGame({ ...game, [what]: !c });
    }
    const currentProfiles = await listGames();
    if (isCurrent(generation)) setProfiles(currentProfiles);
  };

  const onSlide = (v: number) => {
    if (vol === null || switchingReceiver.current) return;
    const generation = selectionGeneration.current;
    const sequence = ++controlSequences.current.volume;
    lastLocal.current = Date.now();
    setVol(v);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      if (
        !isCurrent(generation) ||
        switchingReceiver.current ||
        sequence !== controlSequences.current.volume
      ) return;
      startLocalRequest("volume");
      let requestActive = true;
      try {
        const r = await setVolume(v);
        if (!isCurrent(generation) || sequence !== controlSequences.current.volume) return;
        if (r.volume !== undefined) setVol(r.volume);
        updateCommandNote(r.confirmed);
        if (!r.ok) {
          setErr(r.error ?? "Unknown error");
          if (r.volume === undefined) {
            finishLocalRequest("volume", generation);
            requestActive = false;
            await refresh(false, generation);
          }
        } else setErr(null);
      } finally {
        if (requestActive) finishLocalRequest("volume", generation);
      }
    }, 150);
  };

  const saveReceiver = async () => {
    const generation = beginReceiverChange();
    setSaving(true);
    setErr(null);
    const enteredCap = maxText;
    const enteredLateCap = lateText;
    const previousScale = receiverState?.scale;
    const config: Record<string, string> = {};
    let validationError: string | null = null;
    if (selectedDriver === "auto") {
      config.host = host.trim();
    } else {
      const driver = drivers.find((item) => item.key === selectedDriver);
      for (const field of driver?.fields ?? []) {
        const value = field.key === "host" ? host : manualConfig[field.key] ?? "";
        const savedSecret =
          field.secret &&
          connection?.configured &&
          connection.driver === selectedDriver;
        if (field.required && !value.trim() && !savedSecret) {
          validationError = `${field.label} is required.`;
          break;
        }
        if (value !== "" || (!field.secret && field.required)) {
          config[field.key] = value;
        }
      }
      if (config.host) config.host = config.host.trim();
    }
    if (validationError) {
      setErr(validationError);
      setSaving(false);
      finishReceiverChange(generation);
      return;
    }
    try {
      const result = await configure(selectedDriver, config);
      if (!isCurrent(generation)) return;
      if (!result.ok) {
        setErr(result.error ?? "Unknown error");
        return;
      }
      const savedConnection = await getConnection();
      if (!isCurrent(generation)) return;
      updateConnection(savedConnection);
      const fields = savedConnection.fields ?? {};
      setHostState(fields.host ?? "");
      setManualConfig(fields);
      setSelectedDriver(savedConnection.driver ?? selectedDriver);
      setNote("Receiver settings saved.");
      const connected = await getState();
      if (!isCurrent(generation)) return;
      let capError: string | null = null;
      let capNote: string | null = null;
      if (
        previousScale &&
        connected.ok &&
        connected.scale &&
        connected.capabilities?.includes("volume") &&
        connected.scale.unit === previousScale.unit
      ) {
        const cap = inputToRaw(enteredCap, connected.scale);
        const lateCap = inputToRaw(enteredLateCap, connected.scale);
        if (cap === null || lateCap === null) {
          capError = `Enter valid volume caps in ${connected.scale.unit}.`;
        } else {
          if (!isCurrent(generation)) return;
          await setMax(cap);
          if (!isCurrent(generation)) return;
          await setLateMax(lateCap);
        }
      } else if (
        previousScale &&
        connected.ok &&
        connected.scale &&
        connected.capabilities?.includes("volume") &&
        connected.scale.unit !== previousScale.unit
      ) {
        capNote = `Receiver settings saved. Set volume caps in ${connected.scale.unit}.`;
      }
      await refresh(true, generation);
      if (!isCurrent(generation)) return;
      if (capError) setErr(capError);
      if (capNote) setNote(capNote);
    } catch (error) {
      if (isCurrent(generation)) setErr(String(error));
    } finally {
      if (isCurrent(generation)) {
        setSaving(false);
        finishReceiverChange(generation);
      }
    }
  };

  const scale = receiverState?.scale;
  const capabilities = receiverState?.capabilities ?? [];
  const hasCapability = (capability: string) => capabilities.includes(capability);
  const standby = hasCapability("power") && power === false;
  const selectedFields =
    selectedDriver === "auto"
      ? []
      : drivers.find((item) => item.key === selectedDriver)?.fields ?? [];

  return (
    <>
      <PanelSection title="Receiver">
        {connection?.configured ? (
          <PanelSectionRow>
            <div>
              {connection.label ??
                receiverState?.driver_label ??
                connection.driver ??
                "Receiver"}: {connection.summary}
            </div>
          </PanelSectionRow>
        ) : receiverState?.driver_label ? (
          <PanelSectionRow>
            <div>{receiverState.driver_label}</div>
          </PanelSectionRow>
        ) : null}
        {hasCapability("power") && (
          <PanelSectionRow>
            <ToggleField
              label={power === null ? "Power (unknown)" : "Power"}
              checked={power ?? false}
              onChange={async (on: boolean) => {
                const generation = selectionGeneration.current;
                if (switchingReceiver.current) return;
                const sequence = ++controlSequences.current.power;
                startLocalRequest("power");
                setPowerState(on);
                let requestActive = true;
                try {
                  if (!isCurrent(generation) || switchingReceiver.current) return;
                  const result = await setPower(on);
                  if (
                    !isCurrent(generation) ||
                    sequence !== controlSequences.current.power
                  ) return;
                  if (result.power !== undefined) setPowerState(result.power);
                  updateCommandNote(result.confirmed);
                  if (!result.ok) {
                    setErr(result.error ?? "Unknown error");
                    if (result.power === undefined) {
                      finishLocalRequest("power", generation);
                      requestActive = false;
                      await refresh(false, generation);
                    }
                  } else {
                    setErr(null);
                  }
                } finally {
                  if (requestActive) finishLocalRequest("power", generation);
                }
              }}
            />
          </PanelSectionRow>
        )}
        {standby && (
          <PanelSectionRow>
            <div>Receiver is in standby</div>
          </PanelSectionRow>
        )}
        {!standby && hasCapability("volume") && scale && (
          <PanelSectionRow>
            <SliderField
              label={vol === null ? "Volume unknown" : `Volume (${displayText(vol, scale)})`}
              value={vol ?? scale.min}
              min={scale.min}
              max={max}
              step={1}
              disabled={vol === null}
              showValue={false}
              onChange={onSlide}
            />
          </PanelSectionRow>
        )}
        {!standby && hasCapability("mute") && (
          <PanelSectionRow>
            <ToggleField
              label={muted === null ? "Mute (unknown)" : "Mute"}
              checked={muted ?? false}
              onChange={async (c: boolean) => {
                const generation = selectionGeneration.current;
                if (switchingReceiver.current) return;
                const sequence = ++controlSequences.current.mute;
                startLocalRequest("mute");
                setMuted(c);
                let requestActive = true;
                try {
                  if (!isCurrent(generation) || switchingReceiver.current) return;
                  const r = await setMute(c);
                  if (
                    !isCurrent(generation) ||
                    sequence !== controlSequences.current.mute
                  ) return;
                  if (r.muted !== undefined) setMuted(r.muted);
                  updateCommandNote(r.confirmed);
                  if (!r.ok) {
                    setErr(r.error ?? "Unknown error");
                    if (r.muted === undefined) {
                      finishLocalRequest("mute", generation);
                      requestActive = false;
                      await refresh(false, generation);
                    }
                  } else setErr(null);
                } finally {
                  if (requestActive) finishLocalRequest("mute", generation);
                }
              }}
            />
          </PanelSectionRow>
        )}
        {!standby && hasCapability("volume") && scale && (
          <PanelSectionRow>
            <ToggleField
              label="Late night mode"
              description={
                lateOn
                  ? `Volume capped at ${displayText(receiverState?.late_cap ?? max, scale)}`
                  : `Lowers the volume and caps it at ${lateText} ${scale.unit}`
              }
              checked={lateOn}
              onChange={async (c: boolean) => {
                const generation = selectionGeneration.current;
                if (switchingReceiver.current) return;
                lastLocal.current = Date.now();
                setLateOn(c);
                if (!isCurrent(generation) || switchingReceiver.current) return;
                const r = await setLateNight(c);
                if (!isCurrent(generation)) return;
                if (!r.ok) setErr(r.error ?? "Unknown error");
                await refresh(false, generation);
              }}
            />
          </PanelSectionRow>
        )}
        {!standby && hasCapability("mode") && modeCode && (
          <PanelSectionRow>
            <DropdownItem
              label="Listening mode"
              rgOptions={[
                ...modes.map((m) => ({ data: m.code, label: m.name })),
                // A code unknown to this driver still remains visible as current.
                ...(modes.some((m) => m.code === modeCode)
                  ? []
                  : [{ data: modeCode, label: mode ?? `Mode ${modeCode}` }]),
              ]}
              selectedOption={modeCode}
              onChange={async (opt: { data: string }) => {
                const generation = selectionGeneration.current;
                if (switchingReceiver.current) return;
                const sequence = ++controlSequences.current.mode;
                startLocalRequest("mode");
                setModeCode(opt.data);
                let requestActive = true;
                try {
                  if (!isCurrent(generation) || switchingReceiver.current) return;
                  const r = await setMode(opt.data);
                  if (
                    !isCurrent(generation) ||
                    sequence !== controlSequences.current.mode
                  ) return;
                  if (r.code !== undefined) {
                    setModeCode(r.code);
                  } else if (r.mode !== undefined) {
                    const observedMode = modes.find(
                      (item) => item.name === r.mode || item.code === r.mode,
                    );
                    setModeCode(r.mode === null ? null : observedMode?.code ?? r.mode);
                  }
                  if (r.mode !== undefined) setModeLabel(r.mode);
                  else if (r.code) {
                    const observedMode = modes.find((item) => item.code === r.code);
                    if (observedMode) setModeLabel(observedMode.name);
                  }
                  updateCommandNote(r.confirmed);
                  if (!r.ok) setErr(r.error ?? "Unknown error");
                  else setErr(null);
                  if (!r.ok && r.code === undefined && r.mode === undefined) {
                    finishLocalRequest("mode", generation);
                    requestActive = false;
                    await refresh(false, generation); // snap back to the real mode
                  }
                } finally {
                  if (requestActive) finishLocalRequest("mode", generation);
                }
              }}
            />
          </PanelSectionRow>
        )}
        {err && (
          <PanelSectionRow>
            <div style={{ color: "#ff6b6b" }}>{err}</div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={searching || saving}
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
          <PanelSectionRow key={`${r.driver}:${r.id}`}>
            <ButtonItem layout="below" onClick={() => choose(r)}>
              {r.model} ({r.label}, {r.ip})
            </ButtonItem>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <DropdownItem
            label="Connection type"
            rgOptions={[
              { data: "auto", label: "Automatic (detect by IP)" },
              ...drivers.map((driver) => ({ data: driver.key, label: driver.label })),
            ]}
            selectedOption={selectedDriver}
            onChange={(option: { data: string }) => {
              setSelectedDriver(option.data);
              setManualConfig({ host });
            }}
          />
        </PanelSectionRow>
        {selectedDriver === "auto" ? (
          <PanelSectionRow>
            <TextField
              label="Receiver IP address"
              value={host}
              onChange={(event) => {
                setHostState(event.target.value);
                setManualConfig((current) => ({ ...current, host: event.target.value }));
              }}
            />
          </PanelSectionRow>
        ) : (
          selectedFields.map((field) => {
            const isCurrentDriver =
              connection?.configured && connection.driver === selectedDriver;
            const value = field.key === "host" ? host : manualConfig[field.key] ?? "";
            return (
              <PanelSectionRow key={field.key}>
                <TextField
                  label={field.label}
                  requiredLabel={field.required ? "*" : undefined}
                  value={value}
                  placeholder={field.secret && isCurrentDriver ? "(saved)" : ""}
                  bIsPassword={field.secret}
                  onChange={(event) => {
                    if (field.key === "host") setHostState(event.target.value);
                    setManualConfig((current) => ({
                      ...current,
                      [field.key]: event.target.value,
                    }));
                  }}
                />
              </PanelSectionRow>
            );
          })
        )}
        {scale && hasCapability("volume") && (
          <>
            <PanelSectionRow>
              <TextField
                label={`Volume cap (${scale.unit})`}
                value={maxText}
                onChange={(event) => setMaxText(event.target.value)}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <TextField
                label={`Late night cap (${scale.unit})`}
                value={lateText}
                onChange={(event) => setLateText(event.target.value)}
              />
            </PanelSectionRow>
          </>
        )}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={saving} onClick={saveReceiver}>
            {saving ? "Connecting…" : "Save & connect"}
          </ButtonItem>
        </PanelSectionRow>
        {scale && hasCapability("volume") && (
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              onClick={async () => {
                const cap = inputToRaw(maxText, scale);
                const lateCap = inputToRaw(lateText, scale);
                const generation = selectionGeneration.current;
                if (switchingReceiver.current) return;
                if (cap === null || lateCap === null) {
                  setErr(`Enter valid volume caps in ${scale.unit}.`);
                  return;
                }
                if (!isCurrent(generation)) return;
                await setMax(cap);
                if (!isCurrent(generation)) return;
                await setLateMax(lateCap);
                if (!isCurrent(generation)) return;
                await refresh(true, generation);
              }}
            >
              Save volume caps
            </ButtonItem>
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
          {profiles.map((profile) => {
            const sameDriver =
              !!profile.driver && profile.driver === receiverState?.driver && !!scale;
            const description = [
              profile.volume !== null
                ? sameDriver
                  ? `Volume ${displayText(profile.volume, scale!)}`
                  : "Volume from other receiver"
                : null,
              profile.mode
                ? profile.mode_name ?? `Mode ${profile.mode}`
                : null,
            ]
              .filter(Boolean)
              .join(", ");
            return (
              <PanelSectionRow key={profile.appid}>
                <ButtonItem
                  layout="below"
                  description={description}
                  onClick={async () => {
                    await forgetGame(profile.appid);
                    setProfiles(await listGames());
                    if (game && game.appid === profile.appid) {
                      setGame({ ...game, volume: false, mode: false });
                    }
                  }}
                >
                  Forget {profile.name}
                </ButtonItem>
              </PanelSectionRow>
            );
          })}
        </PanelSection>
      )}
    </>
  );
}

export default definePlugin(() => {
  // Runs as long as the plugin is loaded, whether or not its panel is open.
  // Steam's internal APIs can change between client updates, so isolate them.
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
    console.error("AVR Control: game detection unavailable", e);
  }
  syncRunningGame();

  return {
    name: "AVR Control",
    titleView: <div>AVR Control</div>,
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
