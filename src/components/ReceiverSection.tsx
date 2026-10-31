import { useState } from "react";
import {
  ButtonItem,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  TextField,
} from "@decky/ui";
import { getSetupOptions } from "../api";
import { useAvr } from "../hooks/useAvr";

type Props = { avr: ReturnType<typeof useAvr> };

export function ReceiverSection({ avr }: Props) {
  const [haOptions, setHaOptions] = useState<
    { value: string; label: string }[]
  >([]);
  const [haSetupError, setHaSetupError] = useState<string | null>(null);
  const [haSetupLoading, setHaSetupLoading] = useState(false);
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
            setHaOptions([]);
            setHaSetupError(null);
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
                  if (
                    avr.selectedDriver === "homeassistant" &&
                    (field.key === "url" || field.key === "token")
                  ) {
                    setHaOptions([]);
                    setHaSetupError(null);
                  }
                }}
              />
            </PanelSectionRow>
          );
        })
      )}
      {avr.selectedDriver === "homeassistant" && (
        <>
          <PanelSectionRow>
            <div>
              Create a long-lived access token in Home Assistant under your
              profile → Security → Long-lived access tokens. The URL is often
              like http://homeassistant.local:8123.
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={avr.saving || haSetupLoading}
              onClick={() => {
                const url = avr.manualConfig.url?.trim() ?? "";
                const token = avr.manualConfig.token ?? "";
                setHaOptions([]);
                setHaSetupError(null);
                if (!url) {
                  setHaSetupError("Enter the Home Assistant URL first.");
                  return;
                }
                if (!token.trim()) {
                  const savedToken =
                    connection?.configured &&
                    connection.driver === "homeassistant";
                  setHaSetupError(
                    savedToken
                      ? "Re-enter the token to find media players; setup lookup can't use the saved token yet."
                      : "Enter a long-lived access token to find media players.",
                  );
                  return;
                }

                setHaSetupLoading(true);
                void getSetupOptions("homeassistant", { url, token })
                  .then((response) => {
                    const result = response as unknown as {
                      error?: unknown;
                      entity_id?: { value: string; label: string }[];
                    };
                    if (typeof result.error === "string") {
                      setHaSetupError(result.error);
                      return;
                    }
                    const options = result.entity_id ?? [];
                    setHaOptions(options);
                    if (options.length === 0) {
                      setHaSetupError("No Home Assistant media players found.");
                    }
                  })
                  .catch((error: unknown) => {
                    setHaSetupError(
                      error instanceof Error
                        ? error.message
                        : "Couldn't find Home Assistant media players.",
                    );
                  })
                  .finally(() => setHaSetupLoading(false));
              }}
            >
              {haSetupLoading ? "Finding media players…" : "Find media players"}
            </ButtonItem>
          </PanelSectionRow>
          {haOptions.length > 0 && (
            <PanelSectionRow>
              <DropdownItem
                label="Found media players"
                rgOptions={[
                  { data: "", label: "Select a media player" },
                  ...haOptions.map((option) => ({
                    data: option.value,
                    label: option.label,
                  })),
                ]}
                selectedOption={avr.manualConfig.entity_id ?? ""}
                onChange={(option: { data: string }) =>
                  avr.setManualConfig((current) => ({
                    ...current,
                    entity_id: option.data,
                  }))
                }
              />
            </PanelSectionRow>
          )}
          {haSetupError && (
            <PanelSectionRow>
              <div style={{ color: "#ff6b6b" }}>{haSetupError}</div>
            </PanelSectionRow>
          )}
        </>
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
