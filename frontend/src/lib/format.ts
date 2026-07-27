export function formatMoney(value: number, currency = "JOD") {
  return new Intl.NumberFormat("en-JO", {
    style: "currency",
    currency,
    minimumFractionDigits: value % 1 === 0 ? 0 : 2,
  }).format(value);
}

export function humanize(value: string | null | undefined) {
  if (!value) return "Unspecified";
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function dimensionsLabel(item: {
  width_cm?: number | null;
  depth_cm?: number | null;
  height_cm?: number | null;
  diameter_cm?: number | null;
}) {
  const dimensions = [
    item.width_cm == null ? null : `${item.width_cm} W`,
    item.depth_cm == null ? null : `${item.depth_cm} D`,
    item.height_cm == null ? null : `${item.height_cm} H`,
    item.diameter_cm == null ? null : `${item.diameter_cm} Dia`,
  ].filter((value): value is string => Boolean(value));
  return dimensions.length ? `${dimensions.join(" × ")} cm` : "Dimensions unavailable";
}
