import { describe, expect, it } from "vitest";
import { matchesProductOriginFilter, productOrigin, productOriginKey } from "./product-origin";
import type { SelectedFurniture } from "@/types/api";

function selected(overrides: Partial<SelectedFurniture>): SelectedFurniture {
  return {
    id: 1,
    name: "Catalog object",
    category: "chair",
    style: "modern",
    color: "oak",
    price: 100,
    currency: "JOD",
    image_filename: null,
    image_url: null,
    product_url: "https://example.com/product",
    store_name: "IKEA Jordan",
    selection_role: "user_selected",
    selection_origin: "user",
    quantity: 1,
    line_total: 100,
    ...overrides,
  };
}

describe("product origin presentation", () => {
  it("maps persisted template source values to the template label and border", () => {
    const origin = productOrigin(selected({ selection_origin: "template", selection_role: "template_selected" }));
    expect(origin.label).toBe("From Template");
    expect(origin.borderClass).toBe("origin-border-template");
    expect(origin.badgeClass).toBe("origin-badge-template");
  });

  it("maps database completion values without confusing them with template products", () => {
    const item = selected({ selection_origin: "system", selection_role: "system_completion" });
    expect(productOriginKey(item)).toBe("completion");
    expect(productOrigin(item).label).toBe("Added to Complete the Room");
    expect(matchesProductOriginFilter(item, "completion")).toBe(true);
    expect(matchesProductOriginFilter(item, "template")).toBe(false);
  });

  it("preserves the user-selected origin for exact Custom inventory", () => {
    expect(productOrigin(selected({})).label).toBe("Selected by You");
  });

  it("prefers the backend public product_origin contract", () => {
    expect(productOriginKey(selected({
      product_origin: "added_to_complete_room",
      selection_origin: "template",
      selection_role: "template_selected",
    }))).toBe("completion");
  });
});
