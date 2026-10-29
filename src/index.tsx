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

type State = {
  ok: boolean;
  volume?: number;
  muted?: boolean;
  max?: number;
  zero?: number;
  error?: string;
};
type Receiver = { ip: string; model: string; id: string };

const getState = callable<[], State>("get_state");
const setVolume = callable<[number], State>("set_volume");
const setMute = callable<[boolean], State>("set_mute");
const getHost = callable<[], string>("get_host");
const setHost = callable<[string], void>("set_host");
const setMax = callable<[number], void>("set_max");
const discover = callable<[], Receiver[]>("discover");
const selectReceiver = callable<[string, string], void>("select_receiver");

// 0.5 dB per step; `zero` is the raw value that equals 0 dB.
const toDb = (v: number, zero: number) => {
  const d = (v - zero) / 2;
  return `${d > 0 ? "+" : ""}${d.toFixed(1)} dB`;
};

// The cap is entered in dB; the backend stores raw receiver units.
const fmtDb = (raw: number, zero: number) => String((raw - zero) / 2);
const dbToRaw = (text: string, zero: number): number | null => {
  const d = parseFloat(text.replace(",", ".")); // accept 1,5 as well as 1.5
  return Number.isNaN(d) ? null : Math.round(zero + d * 2);
};

function Content() {
  const [vol, setVol] = useState(0);
  const [max, setMaxState] = useState(100);
  const [maxText, setMaxText] = useState("-32");
  const [zero, setZero] = useState(164);
  const [muted, setMuted] = useState(false);
  const [host, setHostState] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [found, setFound] = useState<Receiver[]>([]);
  const [searching, setSearching] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const refresh = async () => {
    const r = await getState();
    if (r.ok) {
      setVol(r.volume ?? 0);
      setMuted(!!r.muted);
      setMaxState(r.max ?? 100);
      setMaxText(fmtDb(r.max ?? 100, r.zero ?? 164));
      setZero(r.zero ?? 164);
      setErr(null);
    } else {
      setErr(r.error ?? "Unknown error");
    }
  };

  const choose = async (r: Receiver) => {
    await selectReceiver(r.ip, r.id);
    setHostState(r.ip);
    setNote(`Using ${r.model} at ${r.ip}`);
    await refresh();
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
    getHost().then((h) => {
      setHostState(h);
      if (h) refresh();
      else search(true); // first run: look for the receiver automatically
    });
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
              setMuted(c);
              const r = await setMute(c);
              if (!r.ok) setErr(r.error ?? "Unknown error");
            }}
          />
        </PanelSectionRow>
        {err && (
          <PanelSectionRow>
            <div style={{ color: "#ff6b6b" }}>{err}</div>
          </PanelSectionRow>
        )}
      </PanelSection>
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
          <ButtonItem
            layout="below"
            onClick={async () => {
              await setHost(host);
              const n = dbToRaw(maxText, zero);
              if (n !== null) await setMax(n);
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
