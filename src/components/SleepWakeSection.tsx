import { PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { useAvr } from "../hooks/useAvr";

type Props = { avr: ReturnType<typeof useAvr> };

export function SleepWakeSection({ avr }: Props) {
  const settings = avr.snapshot?.sleep_wake;
  if (!settings) return null;
  const persist = (wakeOnResume: boolean, offOnSuspend: boolean) =>
    avr.send({
      type: "set_sleep_wake",
      wake_on_resume: wakeOnResume,
      off_on_suspend: offOnSuspend,
    });

  return (
    <PanelSection title="Steam sleep and wake">
      {!settings.supports_power && (
        <PanelSectionRow>
          <div>This receiver has no power control.</div>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ToggleField
          label="Turn receiver on when Steam wakes"
          description="Only wakes it when its power state is known to be off"
          checked={settings.wake_on_resume}
          disabled={!settings.supports_power}
          onChange={(enabled: boolean) =>
            void persist(enabled, settings.off_on_suspend)
          }
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ToggleField
          label="Turn receiver off when Steam sleeps"
          checked={settings.off_on_suspend}
          disabled={!settings.supports_power}
          onChange={(enabled: boolean) =>
            void persist(settings.wake_on_resume, enabled)
          }
        />
      </PanelSectionRow>
    </PanelSection>
  );
}
