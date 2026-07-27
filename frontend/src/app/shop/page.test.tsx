import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ShopPage from "./page";

const mocks = vi.hoisted(() => ({
  getFurniture: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/api", () => ({
  getFurniture: mocks.getFurniture,
}));

vi.mock("@/components/product-card", () => ({
  ProductCard: ({ product }: { product: { name: string } }) => (
    <div>{product.name}</div>
  ),
}));

vi.mock("@/components/product-detail-dialog", () => ({
  ProductDetailDialog: () => null,
}));

vi.mock("@/components/brand-mark", () => ({
  BrandMark: ({ brand }: { brand: { displayName: string } }) => (
    <span>{brand.displayName}</span>
  ),
}));

const products = Array.from({ length: 60 }, (_, index) => ({
  id: index + 1,
  name: `Catalog product ${index + 1}`,
  category: "sofa",
  style: "modern",
  color: "blue",
  material: "fabric",
  price: 100 + index,
  availability: true,
  catalog_status: "active",
  store_name: "IKEA Jordan",
}));

describe("Shop catalog loading", () => {
  beforeEach(() => {
    mocks.getFurniture.mockReset();
    mocks.getFurniture.mockResolvedValue(products);
  });

  it("loads once, shows the first batch, and reveals more on demand", async () => {
    render(<ShopPage />);

    expect(await screen.findByText("Catalog product 1")).toBeInTheDocument();
    await waitFor(() => expect(mocks.getFurniture).toHaveBeenCalledTimes(1));
    expect(screen.getByText("Catalog product 48")).toBeInTheDocument();
    expect(screen.queryByText("Catalog product 49")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Load more/ }));

    expect(await screen.findByText("Catalog product 60")).toBeInTheDocument();
  });
});
