import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  SliderField,
  TextField,
  ToggleField,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useRef, useState } from "react";
import { FaVolumeUp } from "react-icons/fa";

type State = { ok: boolean; volume?: number; muted?: boolean; max?: number; error?: string };

const getState = callable<[], State>("get_state");
const setVolume = callable<[number], State>("set_volume");
const setMute = callable<[boolean], State>("set_mute");
const getHost = callable<[], string>("get_host");
const setHost = callable<[string], void>("set_host");

const toDb = (v: number) => {
  const d = (v - 161) / 2;
  return `${d > 0 ? "+" : ""}${d.toFixed(1)} dB`;
};

function Content() {
  const [vol, setVol] = useState(0);
  const [max, setMax] = useState(161);
  const [muted, setMuted] = useState(false);
  const [host, setHostState] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const refresh = async () => {
    const r = await getState();
    if (r.ok) {
      setVol(r.volume ?? 0);
      setMuted(!!r.muted);
      setMax(r.max ?? 161);
      setErr(null);
    } else {
      setErr(r.error ?? "Unknown error");
    }
  };

  useEffect(() => {
    getHost().then(setHostState);
    refresh();
  }, []);

  const onSlide = (v: number) => {
    setVol(v);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      const r = await setVolume(v);
      if (!r.ok) setErr(r.error ?? "Unknown error");
      else setErr(null);
    }, 150);
  };

  return (
    <>
      <PanelSection title="AVR Volume">
        <PanelSectionRow>
          <SliderField
            label={`Volume (${toDb(vol)})`}
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
              setMuted(c);
              const r = await setMute(c);
              if (!r.ok) setErr(r.error ?? "Unknown error");
            }}
          />
        </PanelSectionRow>
        {err && <PanelSectionRow><div style={{ color: "#ff6b6b" }}>{err}</div></PanelSectionRow>}
      </PanelSection>
      <PanelSection title="Settings">
        <PanelSectionRow>
          <TextField
            label="AVR IP address"
            value={host}
            onChange={(e) => setHostState(e.target.value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              await setHost(host);
              await refresh();
            }}
          >
            Save &amp; connect
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: "Pioneer Volume",
  titleView: <div>Pioneer Volume</div>,
  content: <Content />,
  icon: <FaVolumeUp />,
  onDismount() {},
}));
