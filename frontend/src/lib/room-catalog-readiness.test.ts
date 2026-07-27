import { describe, expect, it } from "vitest";
import { roomCatalogReadiness } from "./room-catalog-readiness";
import type { Furniture, RoomType } from "@/types/api";

function product(id: number, category: string, roomType: RoomType): Furniture {
  return {
    id,
    name: category,
    category,
    style: "modern",
    color: "neutral",
    material: null,
    width_cm: 100,
    depth_cm: 60,
    height_cm: 70,
    price: 100,
    image_filename: null,
    product_url: "https://example.com",
    store_name: "Catalog Store",
    currency: "JOD",
    room_types: [roomType],
    tags: [],
    availability: true,
    catalog_status: "active",
    quarantine_reason: null,
    source_store_slug: "catalog_store",
    source_product_id: String(id),
    canonical_product_url: "https://example.com",
    name_ar: null,
    name_en: category,
    subcategory: null,
    original_price: null,
    source_image_url: null,
    additional_source_image_urls: [],
    diameter_cm: null,
    description: null,
    brand: null,
    model_number: null,
    source_metadata: {},
    imported_at: null,
    source_updated_at: null,
    design_eligible: true,
    mounting_type: null,
    is_product_set: false,
    included_pieces: [],
    normalized_identity: null,
    image_sha256: null,
    last_seen_import_run: null,
  };
}

describe("room catalog readiness", () => {
  it("accepts every room type when its required policy can be supplied", () => {
    const cases: Array<[RoomType, string[]]> = [
      ["living_room", ["sofa", "coffee_table", "tv_stand"]],
      ["bedroom", ["bed", "wardrobe"]],
      ["office", ["desk", "office_chair"]],
      ["dining_room", ["dining_table", "dining_chair"]],
      ["bathroom", ["toilet", "vanity_sink", "mirror", "bathtub"]],
      [
        "kitchen",
        [
          "kitchen_sink",
          "faucet",
          "refrigerator",
          "base_cabinet",
          "wall_cabinet",
          "cooktop",
        ],
      ],
    ];
    for (const [roomType, categories] of cases) {
      const products = categories.map((category, index) =>
        product(index + 1, category, roomType),
      );
      expect(roomCatalogReadiness(products, roomType)).toEqual({
        ready: true,
        missingRequired: [],
        missingOneOf: [],
      });
    }
  });

  it("reports exact missing essentials instead of allowing a doomed request", () => {
    expect(roomCatalogReadiness([], "bedroom")).toEqual({
      ready: false,
      missingRequired: ["bed", "wardrobe"],
      missingOneOf: [],
    });
  });

  it("does not treat a Shop-only product as room-ready inventory", () => {
    const products = [
      {
        ...product(1, "bed", "bedroom"),
        catalog_status: "shop_only",
        design_eligible: false,
      },
      product(2, "wardrobe", "bedroom"),
    ];
    expect(roomCatalogReadiness(products, "bedroom").missingRequired).toEqual([
      "bed",
    ]);
  });
});
