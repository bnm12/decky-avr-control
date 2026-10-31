import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  TextField,
} from "@decky/ui";
import { useAvr } from "../hooks/useAvr";

type Props = { avr: ReturnType<typeof useAvr> };

export function ReceiverSection({ avr }: Props) {
  const { snapshot } = avr;
  const connection = snapshot?.connection;
  const fields =
    avr.selectedDriver === "auto"
      ? []
      : avr.drivers.find((driver) => driver.key === avr.selectedDriver)?.fields ??
        [];

  const save = () => {
    const config: Record<string, string> = {};
    for (const field of fields) {
      const value =
        field.key === "host" ? avr.host : avr.manualConfig[field.key] ?? "";
      const savedSecret =
        field.secret &&
        connection?.configured &&
        connection.driver === avr.selectedDriver;
      if (field.required && !value.trim() && !savedSecret) {
        avr.setError(`${field.label} is required.`);
        return;
      }
      if (value !== "" || (!field.secret && field.required)) {
        config[field.key] = value;
      }
    }
    if (avr.selectedDriver === "auto") config.host = avr.host.trim();
    if (config.host) config.host = config.host.trim();
    void avr.saveReceiver(config);
  };

  return (
    <PanelSection title="Receiver setup">
      {connection?.configured && (
        <PanelSectionRow>
          <div>
            {connection.label ?? connection.driver ?? "Receiver"}:{" "}
            {connection.summary}
          </div>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          disabled={avr.searching || avr.saving}
          onClick={() => void avr.search(false)}
        >
          {avr.searching ? "Searching…" : "Search network"}
        </ButtonItem>
      </PanelSectionRow>
      {avr.found.map((receiver) => (
        <PanelSectionRow key={`${receiver.driver}:${receiver.id}`}>
          <ButtonItem
            layout="below"
            onClick={() => void avr.choose(receiver)}
          >
            {receiver.model} ({receiver.label}, {receiver.ip})
          </ButtonItem>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <DropdownItem
          label="Connection type"
          rgOptions={[
            { data: "auto", label: "Automatic (detect by IP)" },
            ...avr.drivers.map((driver) => ({
              data: driver.key,
              label: driver.label,
            })),
          ]}
          selectedOption={avr.selectedDriver}
          onChange={(option: { data: string }) => {
            avr.setSelectedDriver(option.data);
            avr.setManualConfig({ host: avr.host });
          }}
        />
      </PanelSectionRow>
      {avr.selectedDriver === "auto" ? (
        <PanelSectionRow>
          <TextField
            label="Receiver IP address"
            value={avr.host}
            onChange={(event) => {
              avr.setHost(event.target.value);
              avr.setManualConfig((current) => ({
                ...current,
                host: event.target.value,
              }));
            }}
          />
        </PanelSectionRow>
      ) : (
        fields.map((field) => {
          const currentDriver =
            connection?.configured && connection.driver === avr.selectedDriver;
          const value =
            field.key === "host"
              ? avr.host
              : avr.manualConfig[field.key] ?? "";
          return (
            <PanelSectionRow key={field.key}>
              <TextField
                label={field.label}
                requiredLabel={field.required ? "*" : undefined}
                value={value}
                placeholder={field.secret && currentDriver ? "(saved)" : ""}
                bIsPassword={field.secret}
                onChange={(event) => {
                  if (field.key === "host") avr.setHost(event.target.value);
                  avr.setManualConfig((current) => ({
                    ...current,
                    [field.key]: event.target.value,
                  }));
                }}
              />
            </PanelSectionRow>
          );
        })
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={avr.saving} onClick={save}>
          {avr.saving ? "Connecting…" : "Save & connect"}
        </ButtonItem>
      </PanelSectionRow>
      {avr.error && (
        <PanelSectionRow>
          <div style={{ color: "#ff6b6b" }}>{avr.error}</div>
        </PanelSectionRow>
      )}
      {avr.note && (
        <PanelSectionRow>
          <div>{avr.note}</div>
        </PanelSectionRow>
      )}
    </PanelSection>
  );
}
