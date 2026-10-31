import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
} from "@decky/ui";
import { useEffect, useState } from "react";
import { useAvr } from "../hooks/useAvr";

type Props = { avr: ReturnType<typeof useAvr> };

const isTime = (value: string) =>
  /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value);

export function ScheduleSection({ avr }: Props) {
  const schedule = avr.snapshot?.schedule;
  const [start, setStart] = useState(schedule?.start ?? "22:00");
  const [end, setEnd] = useState(schedule?.end ?? "07:00");
  const [mode, setMode] = useState(schedule?.mode ?? "");

  useEffect(() => {
    if (!schedule) return;
    setStart(schedule.start);
    setEnd(schedule.end);
    setMode(schedule.mode ?? "");
  }, [schedule?.start, schedule?.end, schedule?.mode]);

  const save = (enabled = schedule?.enabled ?? false) => {
    if (!isTime(start) || !isTime(end)) {
      avr.setError("Times must use HH:MM (24-hour) format");
      return;
    }
    if (start === end) {
      avr.setError("Start and end times must be different");
      return;
    }
    void avr.send({
      type: "set_schedule",
      enabled,
      start,
      end,
      mode: mode || null,
    });
  };

  return (
    <PanelSection title="Time-of-day schedule">
      <PanelSectionRow>
        <ToggleField
          label="Enable daily quiet-hours schedule"
          description={
            schedule?.active_now
              ? schedule.auto
                ? "Active now; late night mode was turned on automatically"
                : "Active now"
              : "Uses the Steam device's local time"
          }
          checked={schedule?.enabled ?? false}
          onChange={(enabled: boolean) => save(enabled)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="Start (HH:MM)"
          value={start}
          onChange={(event) => setStart(event.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <TextField
          label="End (HH:MM)"
          value={end}
          onChange={(event) => setEnd(event.target.value)}
        />
      </PanelSectionRow>
      {avr.snapshot?.capabilities.includes("mode") &&
        avr.snapshot.modes.selectable.length > 0 && (
          <PanelSectionRow>
            <DropdownItem
              label="Listening mode at start"
              rgOptions={[
                { data: "", label: "No change" },
                ...avr.snapshot.modes.selectable.map((option) => ({
                  data: option.code,
                  label: option.label,
                })),
              ]}
              selectedOption={mode}
              onChange={(option: { data: string }) => setMode(option.data)}
            />
          </PanelSectionRow>
        )}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => save()}>
          Save schedule
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
