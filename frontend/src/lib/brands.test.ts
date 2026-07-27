import { describe, expect, it } from "vitest";
import { BRAND_BY_SLUG, BRAND_BY_STORE, displayStoreName } from "./brands";

describe("brand mapping", () => {
  it("maps exact live database store values to approved brand slugs", () => {
    expect(BRAND_BY_STORE["IKEA Jordan"].slug).toBe("ikea");
    expect(BRAND_BY_STORE["Leaders Center"].slug).toBe("leaders");
    expect(BRAND_BY_SLUG.ikea.storeName).toBe("IKEA Jordan");
    expect(BRAND_BY_SLUG.leaders.storeName).toBe("Leaders Center");
    expect(BRAND_BY_SLUG.ikea).toMatchObject({ logoPath: "/brands/ikea.svg", logoAvailable: true });
    expect(BRAND_BY_SLUG.leaders).toMatchObject({ logoPath: "/brands/leaders.svg", logoAvailable: true });
  });

  it("does not mislabel unmapped stores", () => {
    expect(displayStoreName("Orange Jordan")).toBe("Orange Jordan");
  });
});
