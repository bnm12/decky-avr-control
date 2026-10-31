import { useCallback, useEffect, useRef, useState } from "react";
import { addEventListener, removeEventListener } from "@decky/api";
import {
  ControlIntent,
  dispatchIntent,
  discoverReceivers,
  DriverInfo,
  getSnapshot,
  Outcome,
  Receiver,
  Snapshot,
  listDrivers,
} from "../api";

type LocalControl = "volume" | "mute" | "power" | "mode";
type RequestState = Record<LocalControl, number>;
type ControlFlags = Record<LocalControl, boolean>;

const controls: LocalControl[] = ["volume", "mute", "power", "mode"];

const parseDisplay = (text: string): number | null => {
  const normalized = text.trim().replace("−", "-").replace(",", ".");
  if (!normalized || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) {
    return null;
  }
  const value = Number(normalized);
  return Number.isFinite(value) ? value : null;
};

const requestState = (): RequestState => ({
  volume: 0,
  mute: 0,
  power: 0,
  mode: 0,
});

export function useAvr() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);
  const [found, setFound] = useState<Receiver[]>([]);
  const [host, setHost] = useState("");
  const [selectedDriver, setSelectedDriver] = useState("auto");
  const [manualConfig, setManualConfig] = useState<Record<string, string>>({});
  const [capText, setCapText] = useState("");
  const [lateCapText, setLateCapText] = useState("");
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const selectionToken = useRef(0);
  const sequence = useRef<RequestState>(requestState());
  const controlInFlight = useRef<RequestState>(requestState());
  const totalInFlight = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLocal = useRef(0);
  const snapshotRef = useRef<Snapshot | null>(null);
  const chooseRef = useRef<(receiver: Receiver) => Promise<void>>(async () => {});
  const revision = useRef(-1);
  const pushActive = useRef(false);
  const receiverChangeInProgress = useRef(false);

  const clearSliderTimer = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  const updateSnapshot = useCallback(
    (
      next: Snapshot,
      token: number,
      canApply: ControlFlags,
    ) => {
      if (token !== selectionToken.current) return;
      revision.current = Math.max(revision.current, next.revision);
      pushActive.current = next.push;
      setSnapshot((previous) => {
        if (token !== selectionToken.current) return previous;
        const merged: Snapshot = {
          ...next,
          receiver: { ...next.receiver },
          modes: { ...next.modes },
        };
        if (previous) {
          if (!canApply.volume) merged.receiver.volume = previous.receiver.volume;
          if (!canApply.mute) merged.receiver.muted = previous.receiver.muted;
          if (!canApply.power) merged.receiver.power = previous.receiver.power;
          if (!canApply.mode) merged.modes.current = previous.modes.current;
        }
        snapshotRef.current = merged;
        return merged;
      });
    },
    [],
  );

  const fullSnapshot = (value: Snapshot) => {
    if (value.caps) {
      setCapText(String(value.caps.cap_display));
      setLateCapText(String(value.caps.late_cap_display));
    }
    if (value.error) {
      setError(value.connection.configured ? value.error : null);
    } else {
      setError(null);
    }
  };

  const readSnapshot = useCallback(
    async (full: boolean, token = selectionToken.current) => {
      const startSequence = { ...sequence.current };
      const startInFlight = { ...controlInFlight.current };
      const value = await getSnapshot();
      if (token !== selectionToken.current) return;
      if (value.revision < revision.current) return;
      revision.current = value.revision;
      const canApply = Object.fromEntries(
        controls.map((control) => [
          control,
          startSequence[control] === sequence.current[control] &&
            startInFlight[control] === 0 &&
            controlInFlight.current[control] === 0,
        ]),
      ) as ControlFlags;
      updateSnapshot(value, token, canApply);
      if (full) fullSnapshot(value);
      else if (value.error && controls.every((control) => canApply[control])) {
        setError(value.connection.configured ? value.error : null);
      }
    },
    [updateSnapshot],
  );

  const beginReceiverChange = useCallback(() => {
    selectionToken.current += 1;
    receiverChangeInProgress.current = true;
    setSwitching(true);
    sequence.current = requestState();
    controlInFlight.current = requestState();
    totalInFlight.current = 0;
    clearSliderTimer();
    return selectionToken.current;
  }, [clearSliderTimer]);

  const finishReceiverChange = useCallback((token: number) => {
    if (token === selectionToken.current) {
      receiverChangeInProgress.current = false;
      setSwitching(false);
    }
  }, []);

  const invoke = useCallback(
    async (
      intent: ControlIntent,
      token: number,
      control?: LocalControl,
    ): Promise<Outcome | null> => {
      if (token !== selectionToken.current) return null;
      const requestSequence = control ? ++sequence.current[control] : 0;
      if (control) controlInFlight.current[control] += 1;
      totalInFlight.current += 1;
      lastLocal.current = Date.now();
      let outcome: Outcome;
      try {
        outcome = await dispatchIntent(intent);
        if (
          token !== selectionToken.current ||
          (control && requestSequence !== sequence.current[control])
        ) {
          return outcome;
        }
        if (outcome.snapshot) {
          const canApply = Object.fromEntries(
            controls.map((item) => [
              item,
              item === control || controlInFlight.current[item] === 0,
            ]),
          ) as ControlFlags;
          updateSnapshot(outcome.snapshot, token, canApply);
        }
        if (outcome.error) setError(outcome.error);
        else if (outcome.ok) setError(null);
        if (outcome.confirmed === false) {
          setNote("The receiver didn't apply that change");
        } else if (outcome.confirmed === true) {
          setNote((current) =>
            current === "The receiver didn't apply that change" ? null : current,
          );
        }
        return outcome;
      } finally {
        if (token === selectionToken.current) {
          if (control) {
            controlInFlight.current[control] = Math.max(
              0,
              controlInFlight.current[control] - 1,
            );
          }
          totalInFlight.current = Math.max(0, totalInFlight.current - 1);
          lastLocal.current = Date.now();
        }
      }
    },
    [updateSnapshot],
  );

  const send = useCallback(
    (intent: ControlIntent, control?: LocalControl) =>
      invoke(intent, selectionToken.current, control),
    [invoke],
  );

  const search = useCallback(
    async (automatic = false, token = selectionToken.current) => {
      setSearching(true);
      setNote(null);
      try {
        const receivers = await discoverReceivers();
        if (token !== selectionToken.current) return;
        setFound(receivers);
        if (receivers.length === 0) {
          setNote("No receiver found. Enter its IP address manually.");
        } else if (
          automatic &&
          !snapshotRef.current?.connection.configured &&
          receivers.length === 1
        ) {
          await chooseRef.current(receivers[0]);
        }
      } finally {
        if (token === selectionToken.current) setSearching(false);
      }
    },
    [],
  );
  const choose = useCallback(
    async (receiver: Receiver) => {
      const token = beginReceiverChange();
      setSaving(false);
      setSearching(false);
      try {
        const result = await invoke(
          { type: "select_receiver", id: receiver.id },
          token,
        );
        if (token !== selectionToken.current) return;
        if (!result?.ok || !result.snapshot) {
          setError(result?.error ?? "Couldn't select receiver");
          return;
        }
        const selected = result.snapshot.connection;
        setHost(selected.fields.host ?? receiver.ip);
        setManualConfig(selected.fields);
        setSelectedDriver(selected.driver ?? receiver.driver);
        setNote(`Using ${selected.summary || receiver.model} at ${selected.fields.host ?? receiver.ip}`);
        fullSnapshot(result.snapshot);
        finishReceiverChange(token);
        await readSnapshot(true, token);
      } finally {
        finishReceiverChange(token);
      }
    },
    [beginReceiverChange, finishReceiverChange, invoke, readSnapshot],
  );
  chooseRef.current = choose;

  const saveReceiver = useCallback(
    async (config: Record<string, string>) => {
      const token = beginReceiverChange();
      const previousUnit = snapshotRef.current?.scale?.unit;
      const requestedCap = parseDisplay(capText);
      const requestedLateCap = parseDisplay(lateCapText);
      setSaving(true);
      setError(null);
      try {
        const result = await invoke(
          { type: "configure_receiver", driver_key: selectedDriver, config },
          token,
        );
        if (token !== selectionToken.current) return;
        if (!result?.ok || !result.snapshot) {
          setError(result?.error ?? "Couldn't connect to receiver");
          return;
        }
        const connected = result.snapshot;
        const sameUnit =
          previousUnit !== undefined && previousUnit === connected.scale?.unit;
        fullSnapshot(connected);
        setHost(connected.connection.fields.host ?? "");
        setManualConfig(connected.connection.fields);
        setSelectedDriver(connected.connection.driver ?? selectedDriver);
        setNote("Receiver settings saved.");
        if (sameUnit && connected.capabilities.includes("volume")) {
          if (requestedCap === null || requestedLateCap === null) {
            setError(`Enter valid volume caps${connected.scale?.unit === "steps" ? "" : ` (${connected.scale?.unit})`}`);
          } else {
            const capsResult = await invoke(
              {
                type: "set_caps",
                cap_display: requestedCap,
                late_cap_display: requestedLateCap,
              },
              token,
            );
            if (capsResult?.error) setError(capsResult.error);
          }
        } else if (
          previousUnit !== undefined &&
          connected.scale &&
          connected.scale.unit !== previousUnit &&
          connected.capabilities.includes("volume")
        ) {
          setNote(
            `Receiver settings saved. Set volume caps (${connected.scale.unit}).`,
          );
        }
        await readSnapshot(true, token);
      } finally {
        if (token === selectionToken.current) {
          setSaving(false);
          finishReceiverChange(token);
        }
      }
    },
    [
      beginReceiverChange,
      capText,
      finishReceiverChange,
      invoke,
      lateCapText,
      readSnapshot,
      selectedDriver,
    ],
  );

  const saveCaps = useCallback(async () => {
    const cap = parseDisplay(capText);
    const lateCap = parseDisplay(lateCapText);
    if (cap === null || lateCap === null) {
      setError("Enter valid volume caps");
      return;
    }
    const result = await send({
      type: "set_caps",
      cap_display: cap,
      late_cap_display: lateCap,
    });
    if (result?.ok && result.snapshot) fullSnapshot(result.snapshot);
  }, [capText, lateCapText, send]);

  const changeVolume = useCallback(
    (display: number) => {
      if (switching || !snapshotRef.current?.receiver.volume) return;
      const token = selectionToken.current;
      const requestSequence = ++sequence.current.volume;
      lastLocal.current = Date.now();
      setSnapshot((previous) => {
        if (!previous || token !== selectionToken.current) return previous;
        const updated = {
          ...previous,
          receiver: {
            ...previous.receiver,
            volume: { ...previous.receiver.volume!, display },
          },
        };
        snapshotRef.current = updated;
        return updated;
      });
      clearSliderTimer();
      timer.current = setTimeout(async () => {
        if (
          token !== selectionToken.current ||
          requestSequence !== sequence.current.volume
        ) {
          return;
        }
        await invoke({ type: "set_volume", display }, token, "volume");
      }, 150);
    },
    [clearSliderTimer, invoke, switching],
  );

  useEffect(() => {
    let stopped = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    const token = selectionToken.current;
    const onAvrSnapshot = (value: Snapshot) => {
      if (
        stopped ||
        receiverChangeInProgress.current ||
        value.revision <= revision.current
      ) {
        return;
      }
      const currentConnection = snapshotRef.current?.connection;
      if (
        currentConnection &&
        (currentConnection.driver !== value.connection.driver ||
          currentConnection.summary !== value.connection.summary ||
          JSON.stringify(currentConnection.fields) !==
            JSON.stringify(value.connection.fields))
      ) {
        return;
      }
      revision.current = value.revision;
      const eventToken = selectionToken.current;
      const startSequence = { ...sequence.current };
      const startInFlight = { ...controlInFlight.current };
      const canApply = Object.fromEntries(
        controls.map((control) => [
          control,
          startSequence[control] === sequence.current[control] &&
            startInFlight[control] === 0 &&
            controlInFlight.current[control] === 0,
        ]),
      ) as ControlFlags;
      updateSnapshot(value, eventToken, canApply);
      fullSnapshot(value);
    };
    addEventListener<[Snapshot]>("avr_snapshot", onAvrSnapshot);
    void dispatchIntent({ type: "panel_opened" }).catch(() => {});
    const initialize = async () => {
      try {
        const [availableDrivers, firstSnapshot] = await Promise.all([
          listDrivers(),
          getSnapshot(),
        ]);
        if (stopped || token !== selectionToken.current) return;
        setDrivers(availableDrivers);
        if (firstSnapshot.revision >= revision.current) {
          revision.current = firstSnapshot.revision;
          pushActive.current = firstSnapshot.push;
          snapshotRef.current = firstSnapshot;
          setSnapshot(firstSnapshot);
          fullSnapshot(firstSnapshot);
        }
        const initialSnapshot = snapshotRef.current ?? firstSnapshot;
        setHost(initialSnapshot.connection.fields.host ?? "");
        setManualConfig(initialSnapshot.connection.fields);
        setSelectedDriver(
          initialSnapshot.connection.configured
            ? initialSnapshot.connection.driver ?? "auto"
            : "auto",
        );
        try {
          const app: any = SteamUIStore.MainRunningApp;
          if (app?.appid) {
            await dispatchIntent({
              type: "set_current_game",
              appid: app.appid,
              name: app.display_name ?? `App ${app.appid}`,
            });
          } else {
            await dispatchIntent({
              type: "set_current_game",
              appid: 0,
              name: "",
            });
          }
        } catch {
          // Game lifetime notifications still keep the backend synchronized.
        }
        if (stopped || token !== selectionToken.current) return;
        if (initialSnapshot.connection.configured) {
          await readSnapshot(true, token);
        } else {
          await search(true, token);
        }
        const poll = async () => {
          if (stopped) return;
          if (
            snapshotRef.current?.connection.configured &&
            totalInFlight.current === 0 &&
            Date.now() - lastLocal.current > 2000
          ) {
            await readSnapshot(false);
          }
          if (!stopped) {
            pollTimer = setTimeout(
              poll,
              pushActive.current ? 15000 : 2000,
            );
          }
        };
        pollTimer = setTimeout(
          poll,
          pushActive.current ? 15000 : 2000,
        );
      } catch (reason) {
        if (!stopped) setError(String(reason));
      }
    };
    void initialize();
    return () => {
      stopped = true;
      removeEventListener("avr_snapshot", onAvrSnapshot);
      if (pollTimer !== null) clearTimeout(pollTimer);
      clearSliderTimer();
      void dispatchIntent({ type: "panel_closed" }).catch(() => {});
    };
  }, [clearSliderTimer, readSnapshot, search, updateSnapshot]);

  return {
    snapshot,
    drivers,
    found,
    host,
    setHost,
    selectedDriver,
    setSelectedDriver,
    manualConfig,
    setManualConfig,
    capText,
    setCapText,
    lateCapText,
    setLateCapText,
    searching,
    saving,
    switching,
    error,
    setError,
    note,
    send,
    search,
    choose,
    saveReceiver,
    saveCaps,
    changeVolume,
    beginReceiverChange,
    finishReceiverChange,
    readSnapshot,
  };
}
