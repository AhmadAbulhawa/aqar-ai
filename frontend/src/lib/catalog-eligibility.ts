import type { Furniture } from "@/types/api";

export function isBrowsableProduct(
  product: Pick<Furniture, "catalog_status">,
) {
  return product.catalog_status === "active" || product.catalog_status === "shop_only";
}

export function isDesignSelectableProduct(
  product: Pick<Furniture, "availability" | "catalog_status" | "design_eligible">,
) {
  return (
    product.availability &&
    product.catalog_status === "active" &&
    product.design_eligible
  );
}
