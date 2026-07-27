import { describe, expect, it } from "vitest";
import {
  isBrowsableProduct,
  isDesignSelectableProduct,
} from "./catalog-eligibility";
import { BRANDS } from "./brands";
import { dimensionsLabel } from "./format";

describe("catalog compatibility", () => {
  it("shows Shop-only products but never permits room selection", () => {
    const shopOnly = {
      availability: true,
      catalog_status: "shop_only",
      design_eligible: false,
    };
    expect(isBrowsableProduct(shopOnly)).toBe(true);
    expect(isDesignSelectableProduct(shopOnly)).toBe(false);
    expect(isBrowsableProduct({ catalog_status: "quarantined" })).toBe(false);
    expect(isDesignSelectableProduct({
      availability: true,
      catalog_status: "active",
      design_eligible: true,
    })).toBe(true);
  });

  it("registers the exact database store names for all four retailers", () => {
    expect(BRANDS.map((brand) => brand.storeName)).toEqual([
      "IKEA Jordan",
      "Leaders Center",
      "Shahwan Furniture",
      "Midas Jordan",
    ]);
  });

  it("formats partial and ceiling-light dimensions without invented values", () => {
    expect(dimensionsLabel({
      width_cm: null,
      depth_cm: null,
      height_cm: 24,
      diameter_cm: 46,
    })).toBe("24 H × 46 Dia cm");
    expect(dimensionsLabel({})).toBe("Dimensions unavailable");
  });
});
