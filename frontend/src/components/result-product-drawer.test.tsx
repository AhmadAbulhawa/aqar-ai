import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResultProductDrawer } from "./result-product-drawer";

describe("result product drawer", () => {
  it("uses immutable result quantities with catalog dimensions and store links", () => {
    render(<ResultProductDrawer
      open
      onToggle={vi.fn()}
      total={458}
      currency="JOD"
      items={[{
        id: 30,
        name: "TCL 43-inch Google TV",
        category: "television",
        style: "modern",
        color: "black",
        price: 229,
        currency: "JOD",
        image_filename: "tv.jpg",
        image_url: "/uploads/furniture/tv.jpg",
        product_url: "https://leaders.jo/en/product/tcl-43-inch-google-tv/",
        store_name: "Leaders Center",
        selection_role: "required_exact",
        selection_origin: "user",
        quantity: 2,
        line_total: 458,
      }]}
      catalog={[{
        id: 30,
        name: "TCL 43-inch Google TV",
        category: "television",
        style: "modern",
        color: "black",
        material: null,
        width_cm: 96,
        depth_cm: 7.3,
        height_cm: 56,
        price: 229,
        image_filename: "tv.jpg",
        product_url: "https://leaders.jo/en/product/tcl-43-inch-google-tv/",
        store_name: "Leaders Center",
        currency: "JOD",
        room_types: ["living_room"],
        tags: [],
        availability: true,
        catalog_status: "active",
        quarantine_reason: null,
        source_store_slug: "leaders_jo",
        source_product_id: "30",
        canonical_product_url: "https://leaders.jo/en/product/tcl-43-inch-google-tv/",
        name_ar: null,
        name_en: "TCL 43-inch Google TV",
        subcategory: null,
        original_price: null,
        source_image_url: null,
        additional_source_image_urls: [],
        diameter_cm: null,
        description: null,
        brand: "TCL",
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
      }]}
    />);

    expect(screen.getByText("TCL 43-inch Google TV")).toBeInTheDocument();
    expect(screen.getByText("96 W × 7.3 D × 56 H cm")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    const originBadge = screen.getAllByText("Selected by You").find((node) => node.classList.contains("origin-badge"))!;
    expect(originBadge).toHaveClass("origin-badge-user");
    expect(originBadge.closest("article")).toHaveClass("origin-border-user");
    expect(screen.getByRole("heading", { name: "Selected by You" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "All Products" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Buy from original store/i })).toHaveAttribute("href", "https://leaders.jo/en/product/tcl-43-inch-google-tv/");
  });

  it("groups and styles all public product origins in a stable order", () => {
    const base = {
      category: "chair",
      style: "modern" as const,
      color: "oak",
      price: 100,
      currency: "JOD",
      image_filename: null,
      image_url: null,
      product_url: "https://example.com/product",
      store_name: "IKEA Jordan",
      selection_role: "selected",
      selection_origin: null,
      quantity: 1,
      line_total: 100,
    };
    render(<ResultProductDrawer
      open
      onToggle={vi.fn()}
      total={300}
      currency="JOD"
      items={[
        { ...base, id: 1, name: "Template chair", product_origin: "from_template" },
        { ...base, id: 2, name: "Completion lamp", product_origin: "added_to_complete_room" },
        { ...base, id: 3, name: "User table", product_origin: "user_selected" },
      ]}
      catalog={[]}
    />);

    const drawers = screen.getAllByLabelText("Immutable selected inventory");
    const drawer = within(drawers[drawers.length - 1]);
    const templateHeading = drawer.getByRole("heading", { name: "From Template" });
    const completionHeading = drawer.getByRole("heading", { name: "Added to Complete the Room" });
    const userHeading = drawer.getByRole("heading", { name: "Selected by You" });
    expect(templateHeading.closest("section")).toHaveClass("origin-group-template");
    expect(completionHeading.closest("section")).toHaveClass("origin-group-completion");
    expect(userHeading.closest("section")).toHaveClass("origin-group-user");
    expect(drawer.getByText("Template chair").closest("article")).toHaveClass("origin-border-template");
    expect(drawer.getByText("Completion lamp").closest("article")).toHaveClass("origin-border-completion");
    expect(drawer.getByText("User table").closest("article")).toHaveClass("origin-border-user");
    expect(templateHeading.compareDocumentPosition(completionHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(completionHeading.compareDocumentPosition(userHeading) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
