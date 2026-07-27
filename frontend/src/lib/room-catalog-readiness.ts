import type { Furniture, RoomType } from "@/types/api";
import { isDesignSelectableProduct } from "./catalog-eligibility";

export const ROOM_CATALOG_POLICY: Record<
  RoomType,
  { required: string[]; oneOf: string[][] }
> = {
  living_room: {
    required: ["sofa", "coffee_table"],
    oneOf: [["tv_stand", "sideboard"]],
  },
  bedroom: {
    required: ["bed", "wardrobe"],
    oneOf: [],
  },
  office: {
    required: ["desk", "office_chair"],
    oneOf: [],
  },
  dining_room: {
    required: ["dining_table", "dining_chair"],
    oneOf: [],
  },
  bathroom: {
    required: ["toilet", "vanity_sink", "mirror"],
    oneOf: [["shower_box", "bathtub"]],
  },
  kitchen: {
    required: [
      "kitchen_sink",
      "faucet",
      "refrigerator",
      "base_cabinet",
      "wall_cabinet",
    ],
    oneOf: [["cooktop", "freestanding_cooker"]],
  },
};

export function roomCatalogReadiness(
  products: Furniture[],
  roomType: RoomType,
) {
  const availableCategories = new Set(
    products
      .filter(
        (product) =>
          isDesignSelectableProduct(product) &&
          product.room_types.includes(roomType),
      )
      .map((product) => product.category),
  );
  const policy = ROOM_CATALOG_POLICY[roomType];
  const missingRequired = policy.required.filter(
    (category) => !availableCategories.has(category),
  );
  const missingOneOf = policy.oneOf.filter(
    (group) => !group.some((category) => availableCategories.has(category)),
  );
  return {
    ready: missingRequired.length === 0 && missingOneOf.length === 0,
    missingRequired,
    missingOneOf,
  };
}
