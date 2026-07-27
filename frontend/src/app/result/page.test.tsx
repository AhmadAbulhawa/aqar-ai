import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FAILURE_KEY, PENDING_KEY } from "@/lib/result-storage";
import ResultPage from "./page";

const mocks = vi.hoisted(() => ({
  getFurniture: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(window.location.search),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getFurniture: mocks.getFurniture,
}));

const furniture = {
  id: 82,
  name: "STOCKHOLM 2025 2-seat sofa",
  category: "sofa",
  style: "modern",
  color: "beige",
  material: "fabric",
  width_cm: 178,
  depth_cm: 99,
  height_cm: 70,
  price: 480,
  image_filename: "sofa.png",
  product_url: "https://example.com/sofa",
  store_name: "IKEA Jordan",
  currency: "JOD",
  room_types: ["living_room"],
  tags: [],
  availability: true,
  catalog_status: "active",
  quarantine_reason: null,
};

const selectedFurniture = {
  id: 82,
  name: "STOCKHOLM 2025 2-seat sofa",
  category: "sofa",
  style: "modern",
  color: "beige",
  price: 480,
  currency: "JOD",
  image_filename: "sofa.png",
  image_url: "/uploads/furniture/sofa.png",
  product_url: "https://example.com/sofa",
  store_name: "IKEA Jordan",
  selection_role: "template_selected",
  selection_origin: "template",
  quantity: 2,
  line_total: 960,
};

describe("failed-compliance result inventory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/result?status=failed-compliance");
    mocks.getFurniture.mockResolvedValue([furniture]);
    window.sessionStorage.setItem(FAILURE_KEY, JSON.stringify({
      message: "Generated image failed post-render compliance.",
      render_status: "failed_compliance",
      compliance_status: "failed",
      hard_compliance_failure: true,
      hard_failure_codes: ["permanent_finish_preservation"],
      failure_codes: [],
      failure_reasons: ["The original floor finish changed."],
      violations: [],
      warnings: [],
      generated_design_id: "rejected-21",
      generated_image: {
        filename: "rejected.png",
        local_path: "uploads/generated_designs/rejected.png",
        image_url: "/uploads/generated_designs/rejected.png",
        preserved: true,
      },
      automatic_paid_retry_enabled: false,
      automatic_retry_attempted: false,
      retry_policy: "manual_only",
    }));
    window.sessionStorage.setItem(PENDING_KEY, JSON.stringify({
      room: {
        message: "uploaded",
        room_request_id: 21,
        filename: "room.jpg",
        original_filename: "room.jpg",
        content_type: "image/jpeg",
        room_type: "living_room",
        budget: null,
        style: null,
        preferred_colors: [],
        room_dimensions: { length_cm: 400, width_cm: 400, height_cm: 380 },
      },
      recommendation: {
        message: "prepared",
        design_mode: "auto_template_blueprint",
        selection_id: "locked-selection-21",
        selection_reused: false,
        selection_status: "immutable",
        room_request_id: 21,
        selected_template: {},
        selected_furniture_ids: [82],
        selected_products: [{ id: 82, quantity: 2 }],
        selected_furniture: [selectedFurniture],
        total_price: 960,
        currency: "JOD",
      },
    }));
  });

  afterEach(() => cleanup());

  it("shows the full rejected image before comments and keeps inventory closed by default", async () => {
    render(<ResultPage />);

    expect(await screen.findByRole("heading", { name: "Design Not Approved" })).toBeInTheDocument();
    const image = screen.getByRole("img", { name: "Complete rejected generated design" });
    const comments = screen.getByRole("heading", { name: "Compliance comments" });
    expect(image.compareDocumentPosition(comments) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByText("Design generated")).not.toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: /view products/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    const drawer = await screen.findByLabelText("Immutable selected inventory");
    expect(within(drawer).getByRole("heading", { name: "Products in this design" })).toBeInTheDocument();
    expect(within(drawer).getByText("STOCKHOLM 2025 2-seat sofa")).toBeInTheDocument();
    expect(drawer).toHaveTextContent("Quantity2");
    await waitFor(() => expect(mocks.getFurniture).toHaveBeenCalledTimes(1));
  });
});
