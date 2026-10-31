export function formatVolume(
  value: number | null,
  unit: "dB" | "%" | "steps",
  label?: string,
): string {
  const suffix = unit === "steps" ? "" : ` ${unit}`;
  if (value === null) return label ? `${label}${suffix}` : "";
  const number = Number(value.toFixed(4));
  const rendered = unit === "dB" && number > 0 ? `+${number}` : String(number);
  return `${label ? `${label} ` : ""}${rendered}${suffix}`;
}
