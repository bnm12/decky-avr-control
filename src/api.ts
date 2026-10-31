import { callable } from "@decky/api";

export type Volume = { raw: number; display: number } | null;
export type Snapshot = {
  connection: {
    configured: boolean;
    driver: string | null;
    label: string | null;
    summary: string;
    fields: Record<string, string>;
  };
  receiver: {
    power: boolean | null;
    muted: boolean | null;
    volume: Volume;
  };
  scale: {
    unit: "dB" | "%" | "steps";
    min_display: number;
    max_display: number;
    step_display: number;
  } | null;
  caps: {
    cap_display: number;
    late_cap_display: number;
    effective_max_display: number;
    late_night: boolean;
  } | null;
  schedule: {
    enabled: boolean;
    start: string;
    end: string;
    mode: string | null;
    active_now: boolean;
    auto: boolean;
  };
  sleep_wake: {
    wake_on_resume: boolean;
    off_on_suspend: boolean;
    supports_power: boolean;
  };
  modes: {
    current: { code: string; label: string } | null;
    selectable: { code: string; label: string }[];
    complete: boolean;
  };
  game: {
    appid: number;
    name: string;
    remembers_volume: boolean;
    remembers_mode: boolean;
  } | null;
  profiles: {
    appid: number;
    name: string;
    driver: string | null;
    volume_display: number | null;
    mode_label: string | null;
    same_receiver: boolean;
  }[];
  capabilities: string[];
  revision: number;
  push: boolean;
  error?: string;
};

export type ControlIntent =
  | { type: "panel_opened" }
  | { type: "panel_closed" }
  | { type: "set_volume"; display: number }
  | { type: "set_mute"; muted: boolean }
  | { type: "set_power"; on: boolean }
  | { type: "set_mode"; code: string }
  | { type: "set_late_night"; on: boolean }
  | {
      type: "set_schedule";
      enabled: boolean;
      start: string;
      end: string;
      mode: string | null;
    }
  | {
      type: "set_sleep_wake";
      wake_on_resume: boolean;
      off_on_suspend: boolean;
    }
  | { type: "system_resumed" }
  | { type: "system_suspending" }
  | { type: "set_caps"; cap_display: number; late_cap_display: number }
  | { type: "configure_receiver"; driver_key: string; config: Record<string, string> }
  | { type: "select_receiver"; id: string }
  | { type: "game_started"; appid: number; name: string }
  | { type: "game_stopped"; appid: number }
  | { type: "set_current_game"; appid: number; name: string }
  | { type: "remember_for_game"; what: "volume" | "mode"; on: boolean }
  | { type: "forget_game"; appid: number };

export type Outcome = {
  ok: boolean;
  snapshot?: Snapshot;
  error?: string;
  confirmed?: boolean;
};
export type Receiver = {
  ip: string;
  model: string;
  id: string;
  label: string;
  driver: string;
};
export type ConfigField = {
  key: string;
  label: string;
  secret: boolean;
  required: boolean;
};
export type DriverInfo = {
  key: string;
  label: string;
  fields: ConfigField[];
};

export const getSnapshot = callable<[], Snapshot>("snapshot");
export const dispatchIntent = callable<[ControlIntent], Outcome>("dispatch");
export const discoverReceivers = callable<[], Receiver[]>("discover");
export const listDrivers = callable<[], DriverInfo[]>("list_drivers");
export const getSetupOptions = callable<
  [string, Record<string, string>],
  Record<string, { value: string; label: string }[]>
>("setup_options");
