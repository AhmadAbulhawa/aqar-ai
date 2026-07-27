import type { SelectedFurniture } from "@/types/api";

export type ProductOriginKey = "template" | "completion" | "user" | "unknown";
export type ProductOriginFilter = "all" | "template" | "completion";

export const PRODUCT_ORIGINS: Record<
  ProductOriginKey,
  {
    label: string;
    borderClass: string;
    badgeClass: string;
    explanation: string;
  }
> = {
  template: {
    label: "From Template",
    borderClass: "origin-border-template",
    badgeClass: "origin-badge-template",
    explanation: "A real catalog product matched from the template you selected.",
  },
  completion: {
    label: "Added to Complete the Room",
    borderClass: "origin-border-completion",
    badgeClass: "origin-badge-completion",
    explanation: "A database product added to satisfy an unresolved functional slot.",
  },
  user: {
    label: "Selected by You",
    borderClass: "origin-border-user",
    badgeClass: "origin-badge-user",
    explanation: "An exact product and quantity selected manually in the Shop.",
  },
  unknown: {
    label: "Selected Product",
    borderClass: "origin-border-unknown",
    badgeClass: "origin-badge-unknown",
    explanation: "The backend did not provide a recognized product-source value.",
  },
};

const SOURCE_VALUE_MAP: Record<string, ProductOriginKey> = {
  from_template: "template",
  added_to_complete_room: "completion",
  user_selected: "user",
  template: "template",
  template_selected: "template",
  template_source: "template",
  system: "completion",
  system_completion: "completion",
  database_completion: "completion",
  user: "user",
  required_exact: "user",
};

export function productOriginKey(
  item: Pick<
    SelectedFurniture,
    "product_origin" | "selection_origin" | "selection_role" | "added_for_completion"
  >,
): ProductOriginKey {
  const publicOrigin = item.product_origin
    ? SOURCE_VALUE_MAP[item.product_origin]
    : undefined;
  if (publicOrigin) return publicOrigin;
  const origin = item.selection_origin
    ? SOURCE_VALUE_MAP[item.selection_origin]
    : undefined;
  if (origin) return origin;
  const role = item.selection_role ? SOURCE_VALUE_MAP[item.selection_role] : undefined;
  if (role) return role;
  if (item.added_for_completion) return "completion";
  return "unknown";
}

export const PRODUCT_ORIGIN_ORDER: ProductOriginKey[] = [
  "template",
  "completion",
  "user",
  "unknown",
];

export function productOrigin(item: SelectedFurniture) {
  const key = productOriginKey(item);
  return { key, ...PRODUCT_ORIGINS[key] };
}

export function matchesProductOriginFilter(
  item: SelectedFurniture,
  filter: ProductOriginFilter,
) {
  if (filter === "all") return true;
  return productOriginKey(item) === filter;
}
