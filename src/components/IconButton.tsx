import { DialogButton } from "@decky/ui";
import type { ReactNode } from "react";

type Props = {
  icon: ReactNode;
  label: string;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  text?: string;
};

const hiddenLabel = {
  position: "absolute" as const,
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden" as const,
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap" as const,
  border: 0,
};

export function IconButton({
  icon,
  label,
  onClick,
  active = false,
  disabled = false,
  text,
}: Props) {
  return (
    <DialogButton
      focusable
      disabled={disabled}
      onClick={onClick}
      style={{
        alignItems: "center",
        background: active ? "#1a9fff" : "#202b36",
        border: `1px solid ${active ? "#65bfff" : "#43505d"}`,
        boxSizing: "border-box",
        color: disabled ? "#697580" : active ? "#fff" : "#c7d0d8",
        display: "inline-flex",
        gap: text ? 6 : 0,
        height: 36,
        justifyContent: "center",
        minWidth: text ? 88 : 36,
        opacity: disabled ? 0.65 : 1,
        padding: text ? "0 8px" : 0,
        width: text ? undefined : 36,
      }}
    >
      {icon}
      {text ? <span>{text}</span> : <span style={hiddenLabel}>{label}</span>}
    </DialogButton>
  );
}
