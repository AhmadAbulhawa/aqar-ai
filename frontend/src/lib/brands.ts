export type BrandDefinition = {
  storeName: string;
  displayName: string;
  slug: string;
  logoPath: string;
  logoAvailable: boolean;
  accent: string;
  description: string;
};

export const BRANDS: readonly BrandDefinition[] = [
  {
    storeName: "IKEA Jordan",
    displayName: "IKEA",
    slug: "ikea",
    logoPath: "/brands/ikea.svg",
    logoAvailable: true,
    accent: "#fbd914",
    description: "Scandinavian essentials, warm woods and considered comfort.",
  },
  {
    storeName: "Leaders Center",
    displayName: "LEADERS",
    slug: "leaders",
    logoPath: "/brands/leaders.svg",
    logoAvailable: true,
    accent: "#d8a857",
    description: "Premium home technology selected for modern living.",
  },
  {
    storeName: "Shahwan Furniture",
    displayName: "SHAHWAN",
    slug: "shahwan",
    logoPath: "/brands/shahwan-official.webp",
    logoAvailable: true,
    accent: "#b59a72",
    description: "Jordanian furniture collections for every part of the home.",
  },
  {
    storeName: "Midas Jordan",
    displayName: "MIDAS",
    slug: "midas",
    logoPath: "/brands/midas-official.png",
    logoAvailable: true,
    accent: "#bda06b",
    description: "Furniture, lighting and décor from Midas Jordan.",
  },
] as const;

export const BRAND_BY_STORE = Object.fromEntries(
  BRANDS.map((brand) => [brand.storeName, brand]),
) as Record<string, BrandDefinition>;

export const BRAND_BY_SLUG = Object.fromEntries(
  BRANDS.map((brand) => [brand.slug, brand]),
) as Record<string, BrandDefinition>;

export function displayStoreName(storeName: string | null): string {
  if (!storeName) return "Independent retailer";
  return BRAND_BY_STORE[storeName]?.displayName ?? storeName;
}
