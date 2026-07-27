import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import StudioPage from "./page";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  getFurniture: vi.fn(),
  uploadRoom: vi.fn(),
  prepareCustomDesign: vi.fn(),
  prepareAutoDesign: vi.fn(),
  getDesignTemplate: vi.fn(),
  prepareTemplateDesign: vi.fn(),
  reviseDesignSelection: vi.fn(),
  renderCustomDesign: vi.fn(),
  renderAutoDesign: vi.fn(),
  renderTemplateDesign: vi.fn(),
  saveDesignResult: vi.fn(),
  saveComplianceFailure: vi.fn(),
  savePendingDesign: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/context/selection-context", () => ({
  useSelection: () => ({
    lines: [{ id: 30, quantity: 2 }],
    ready: true,
  }),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getFurniture: mocks.getFurniture,
  uploadRoom: mocks.uploadRoom,
  prepareCustomDesign: mocks.prepareCustomDesign,
  prepareAutoDesign: mocks.prepareAutoDesign,
  getDesignTemplate: mocks.getDesignTemplate,
  prepareTemplateDesign: mocks.prepareTemplateDesign,
  reviseDesignSelection: mocks.reviseDesignSelection,
  renderCustomDesign: mocks.renderCustomDesign,
  renderAutoDesign: mocks.renderAutoDesign,
  renderTemplateDesign: mocks.renderTemplateDesign,
}));
vi.mock("@/lib/result-storage", () => ({
  saveDesignResult: mocks.saveDesignResult,
  saveComplianceFailure: mocks.saveComplianceFailure,
  savePendingDesign: mocks.savePendingDesign,
}));

const product = {
  id: 30,
  name: "Walnut Lounge Chair",
  category: "armchair",
  style: "modern",
  color: "walnut",
  material: "wood",
  width_cm: 80,
  depth_cm: 82,
  height_cm: 74,
  price: 240,
  image_filename: "chair.jpg",
  product_url: "https://example.com/chair",
  store_name: "IKEA Jordan",
  currency: "JOD",
  room_types: ["living_room"],
  tags: [],
  availability: true,
  catalog_status: "active",
  quarantine_reason: null,
  design_eligible: true,
};

const room = {
  message: "uploaded",
  room_request_id: 71,
  filename: "room.jpg",
  original_filename: "room.jpg",
  content_type: "image/jpeg",
  room_type: "living_room",
  budget: null,
  style: null,
  preferred_colors: [],
  room_dimensions: { length_cm: 500, width_cm: 400, height_cm: null },
};

const selectedFurniture = {
  id: 30,
  name: "Walnut Lounge Chair",
  category: "armchair",
  style: "modern",
  color: "walnut",
  price: 240,
  currency: "JOD",
  image_filename: "chair.jpg",
  image_url: "/uploads/furniture/chair.jpg",
  product_url: "https://example.com/chair",
  store_name: "IKEA Jordan",
  selection_role: "user_selected",
  selection_origin: "user",
  quantity: 2,
  line_total: 480,
};

const comfortableCapacity = {
  status: "valid" as const,
  density_classification: "comfortable" as const,
  confirmation_allowed: true,
  room_floor_area_m2: 20,
  usable_floor_area_m2: 16.4,
  raw_product_footprint_m2: 1.31,
  overlap_adjusted_effective_footprint_m2: 1.31,
  effective_occupancy_ratio: 0.08,
  largest_space_consuming_products: [
    {
      product_id: 30,
      name: "Walnut Lounge Chair",
      category: "armchair",
      quantity: 2,
      raw_footprint_m2: 1.31,
      effective_footprint_m2: 1.31,
    },
  ],
  excluded_from_independent_floor_area: [],
  overlap_adjustments: [],
  violations: [],
  warnings: [],
  assumptions: [],
};

const recommendation = {
  room_request: { id: 71, image_filename: "room.jpg", original_filename: "room.jpg", content_type: "image/jpeg", room_type: "living_room", budget: null, style: null, preferred_colors: [] },
  selected_furniture_ids: [30],
  selected_products: [{ id: 30, quantity: 2 }],
  selected_furniture: [selectedFurniture],
  selection_id: "11111111-1111-4111-8111-111111111111",
  selection_reused: false,
  selection_status: "immutable",
  design_strategy_signature: null,
  composition_validation: comfortableCapacity,
  total_price: 480,
  currency: "JOD",
};

const template = {
  id: 7,
  name: "Quiet Forest Living Room",
  room_type: "living_room",
  style: "modern",
  size_category: "medium",
  layout_type: "conversation",
  description: "A restrained layered living room.",
  preview_image_filename: "template.jpg",
  source_name: "IKEA Jordan",
  source_url: "https://example.com/template",
  tags: ["calm"],
  preview_image_url: "/uploads/design_templates/template.jpg",
  source_product_count: 1,
  matched_product_count: 1,
  skipped_product_count: 0,
  failed_product_count: 0,
  missing_product_count: 0,
  represented_products: [],
  available_matched_catalog_products: [],
  unresolved_slots: [],
  estimated_product_total: 480,
  currency: "JOD",
};

function completeRequiredFields(container: HTMLElement) {
  const fileInput = container.querySelector<HTMLInputElement>('input[type="file"]');
  if (!fileInput) throw new Error("Room file input not found");
  fireEvent.change(fileInput, {
    target: { files: [new File(["room"], "room.jpg", { type: "image/jpeg" })] },
  });
  fireEvent.change(screen.getByLabelText("Length (cm)"), { target: { value: "500" } });
  fireEvent.change(screen.getByLabelText("Width (cm)"), { target: { value: "400" } });
}

async function prepareCustom(container: HTMLElement) {
  completeRequiredFields(container);
  fireEvent.click(screen.getByRole("button", { name: /Review exact inventory/i }));
  await screen.findByRole("heading", { name: "Inventory ready to render" });
}

describe("Design Studio modes and actions", () => {
  beforeAll(() => {
    Object.defineProperty(URL, "createObjectURL", { value: vi.fn(() => "blob:room"), configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: vi.fn(), configurable: true });
  });

  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/studio");
    window.localStorage.clear();
    mocks.getFurniture.mockResolvedValue([
      product,
      { ...product, id: 31, name: "Sofa", category: "sofa" },
      { ...product, id: 32, name: "Coffee table", category: "coffee_table" },
      { ...product, id: 33, name: "TV stand", category: "tv_stand" },
    ]);
    mocks.getDesignTemplate.mockResolvedValue(template);
    mocks.uploadRoom.mockResolvedValue(room);
    mocks.prepareCustomDesign.mockResolvedValue(recommendation);
    mocks.prepareAutoDesign.mockResolvedValue({ ...recommendation, design_strategy_signature: "a".repeat(64) });
    mocks.prepareTemplateDesign.mockResolvedValue({
      message: "prepared",
      design_mode: "auto_template_blueprint",
      selection_id: "22222222-2222-4222-8222-222222222222",
      selection_reused: false,
      selection_status: "immutable",
      room_request_id: 71,
      selected_template: { ...template, preview_image_available: true },
      selected_furniture_ids: [30],
      selected_products: [{ id: 30, quantity: 2 }],
      selected_furniture: [selectedFurniture],
      total_price: 480,
      currency: "JOD",
    });
    mocks.reviseDesignSelection.mockImplementation((selectionId, items) => ({
      message: "saved",
      room_request_id: 71,
      template_id: null,
      mode: "custom",
      base_selection_id: selectionId,
      selection_id: "33333333-3333-4333-8333-333333333333",
      selection_reused: false,
      selection_status: "locked",
      design_strategy_signature: null,
      selected_furniture_ids: items.map((item: { id: number }) => item.id),
      selected_products: items,
      selected_furniture: items.map((item: { id: number; quantity: number }) => {
        const source = item.id === 30
          ? selectedFurniture
          : {
              ...selectedFurniture,
              id: item.id,
              name: item.id === 31 ? "Sofa" : `Product ${item.id}`,
              category: item.id === 31 ? "sofa" : "armchair",
              product_origin: "user_selected",
            };
        return {
          ...source,
          quantity: item.quantity,
          line_total: source.price * item.quantity,
        };
      }),
      composition_validation: comfortableCapacity,
      total_price: items.reduce(
        (sum: number, item: { quantity: number }) => sum + 240 * item.quantity,
        0,
      ),
      currency: "JOD",
    }));
    mocks.renderCustomDesign.mockResolvedValue({ generated_design_id: "design-1" });
    mocks.renderAutoDesign.mockResolvedValue({ generated_design_id: "design-2" });
    mocks.renderTemplateDesign.mockResolvedValue({ generated_design_id: "design-3" });
  });

  afterEach(() => cleanup());

  it("keeps Auto-only filters out of Custom Furniture mode", async () => {
    render(<StudioPage />);
    expect(await screen.findByTestId("custom-fields")).toBeInTheDocument();
    expect(screen.queryByLabelText("Budget")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Preferred style")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Preferred colors")).not.toBeInTheDocument();
    expect(screen.queryByText(/Required categories/)).not.toBeInTheDocument();
  });

  it("shows recommendation filters only in Auto mode", async () => {
    render(<StudioPage />);
    fireEvent.click(screen.getByRole("button", { name: /Auto Design/i }));
    expect(await screen.findByTestId("auto-fields")).toBeInTheDocument();
    expect(screen.getByLabelText("Budget")).toBeInTheDocument();
    expect(screen.getByLabelText("Preferred style")).toBeInTheDocument();
    expect(screen.getByLabelText("Preferred colors")).toBeInTheDocument();
    expect(screen.getByText(/Required categories/)).toBeInTheDocument();
  });

  it("builds an interactive Auto Design brief from visual presets", async () => {
    render(<StudioPage />);
    fireEvent.click(screen.getByRole("button", { name: /Auto Design/i }));
    await screen.findByTestId("auto-fields");

    fireEvent.click(screen.getByRole("button", { name: "800 JOD" }));
    expect(screen.getByLabelText("Budget")).toHaveValue(800);

    fireEvent.click(screen.getByRole("button", { name: /Minimal Quiet essentials/i }));
    expect(screen.getByRole("button", { name: /Minimal Quiet essentials/i }))
      .toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "Use Warm & grounded palette" }));
    expect(screen.getByLabelText("Preferred colors"))
      .toHaveValue("Warm white, walnut, olive");

    fireEvent.click(screen.getByRole("button", { name: "Require Sofa" }));
    expect(screen.getByRole("button", { name: "Remove Sofa" }))
      .toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("4 signals active")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(screen.getByRole("button", { name: "Require Sofa" }))
      .toHaveAttribute("aria-pressed", "false");
  });

  it("blocks an incomplete room catalog before a doomed Auto request", async () => {
    render(<StudioPage />);
    fireEvent.click(screen.getByRole("button", { name: /Auto Design/i }));
    fireEvent.change(screen.getByLabelText("Room type"), {
      target: { value: "bedroom" },
    });
    expect(
      await screen.findByText(/Auto Design is paused.*bed, wardrobe/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Prepare AI inventory/i })).toBeDisabled();
    expect(mocks.prepareAutoDesign).not.toHaveBeenCalled();
  });

  it("routes Template mode to the manual gallery instead of matching automatically", async () => {
    render(<StudioPage />);
    fireEvent.click(screen.getByRole("button", { name: /Template Design/i }));
    expect(mocks.push).toHaveBeenCalledWith("/templates");
    expect(mocks.getDesignTemplate).not.toHaveBeenCalled();
  });

  it("keeps Generate disabled with a visible explanation until review completes", async () => {
    render(<StudioPage />);
    const generate = screen.getByRole("button", { name: /Generate design/i });
    expect(generate).toBeDisabled();
    expect(screen.getByText("Add a room image to continue.")).toBeInTheDocument();
  });

  it("enables Generate after required fields and exact inventory preparation", async () => {
    const { container } = render(<StudioPage />);
    await prepareCustom(container);
    expect(screen.getByRole("button", { name: /Generate design/i })).toBeEnabled();
    expect(mocks.prepareCustomDesign).toHaveBeenCalledWith(
      71,
      [{ id: 30, quantity: 2 }],
      "",
    );
    expect(screen.getByRole("button", { name: "Add product" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Increase quantity for Walnut Lounge Chair" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Remove Walnut Lounge Chair" })).toBeEnabled();
  });

  it("shows a dense-selection warning with largest products while keeping Generate enabled", async () => {
    mocks.prepareCustomDesign.mockResolvedValueOnce({
      ...recommendation,
      composition_validation: {
        ...comfortableCapacity,
        status: "valid_with_warnings",
        density_classification: "high_density",
        usable_floor_area_m2: 13.12,
        raw_product_footprint_m2: 12.4,
        overlap_adjusted_effective_footprint_m2: 10.6,
        effective_occupancy_ratio: 0.808,
        warnings: ["Warning: dense selection, but Generate is allowed."],
      },
    });
    const { container } = render(<StudioPage />);

    await prepareCustom(container);

    expect(screen.getByText("Warning: dense selection, but Generate is allowed.")).toBeInTheDocument();
    expect(screen.getByText(/Largest space users: Walnut Lounge Chair/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate design/i })).toBeEnabled();
    expect(mocks.renderCustomDesign).not.toHaveBeenCalled();
  });

  it("shows a blocking physical-impossibility error and does not render", async () => {
    mocks.prepareCustomDesign.mockRejectedValueOnce(new ApiError(
      422,
      {
        message: "Blocking error: the exact inventory is physically impossible.",
        composition_validation: {
          status: "invalid",
          density_classification: "physically_implausible",
          confirmation_allowed: false,
          raw_product_footprint_m2: 18,
          overlap_adjusted_effective_footprint_m2: 16.5,
          violations: [{
            code: "required_major_products_exceed_room_area",
            reason: "Required major products have more floor area than the room.",
          }],
        },
      },
      "http://127.0.0.1:8000/designs/recommend/71",
    ));
    const { container } = render(<StudioPage />);
    completeRequiredFields(container);

    fireEvent.click(screen.getByRole("button", { name: /Review exact inventory/i }));

    expect(await screen.findByText(/Blocking error: physically impossible selection/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate design/i })).toBeDisabled();
    expect(mocks.renderCustomDesign).not.toHaveBeenCalled();
  });

  it("edits quantity, adds a live product, removes a product, and saves a new lock", async () => {
    const { container } = render(<StudioPage />);
    await prepareCustom(container);

    fireEvent.click(screen.getByRole("button", { name: "Increase quantity for Walnut Lounge Chair" }));
    expect(screen.getByText("Save inventory changes before generating the design.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate design/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Add product" }));
    expect(screen.getByRole("dialog", { name: "Add a product" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add Sofa" }));
    expect(screen.getAllByText("Sofa").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Remove Walnut Lounge Chair" }));
    expect(screen.queryByRole("button", { name: "Remove Walnut Lounge Chair" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Save inventory changes" }));
    await waitFor(() => expect(mocks.reviseDesignSelection).toHaveBeenCalledWith(
      "11111111-1111-4111-8111-111111111111",
      [{ id: 31, quantity: 1 }],
    ));
    expect(await screen.findByText(/Selection ID.*33333333/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate design/i })).toBeEnabled();
  });

  it("invokes one render for duplicate Generate clicks", async () => {
    let release: (value: unknown) => void = () => undefined;
    mocks.renderCustomDesign.mockImplementation(() => new Promise((resolve) => { release = resolve; }));
    const { container } = render(<StudioPage />);
    await prepareCustom(container);
    const generate = screen.getByRole("button", { name: /Generate design/i });
    fireEvent.click(generate);
    fireEvent.click(generate);
    expect(mocks.renderCustomDesign).toHaveBeenCalledTimes(1);
    release({ generated_design_id: "design-1" });
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/result?status=success"));
  });

  it("shows network errors and resets the loading state", async () => {
    mocks.renderCustomDesign.mockRejectedValueOnce(new Error("Network connection lost"));
    const { container } = render(<StudioPage />);
    await prepareCustom(container);
    fireEvent.click(screen.getByRole("button", { name: /Generate design/i }));
    expect(await screen.findByText("Network connection lost")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate design/i })).toBeEnabled();
  });

  it("shows one useful backend-unreachable message with endpoint and transport status", async () => {
    mocks.prepareCustomDesign.mockRejectedValueOnce(new ApiError(
      0,
      { message: "The FastAPI backend is unreachable." },
      "http://127.0.0.1:8000/designs/recommend/71",
      true,
    ));
    const { container } = render(<StudioPage />);
    completeRequiredFields(container);
    fireEvent.click(screen.getByRole("button", { name: /Review exact inventory/i }));
    const messages = await screen.findAllByText(/Backend unreachable.*designs\/recommend\/71.*No HTTP status/i);
    expect(messages).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Review exact inventory/i })).toBeEnabled();
  });

  it("routes failed-compliance 422 to the failure result without retrying", async () => {
    const detail = { render_status: "failed_compliance", generated_design_id: "rejected-1" };
    mocks.renderCustomDesign.mockRejectedValueOnce(new ApiError(422, detail));
    const { container } = render(<StudioPage />);
    await prepareCustom(container);
    fireEvent.click(screen.getByRole("button", { name: /Generate design/i }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/result?status=failed-compliance"));
    expect(mocks.saveComplianceFailure).toHaveBeenCalledWith(detail);
    expect(mocks.renderCustomDesign).toHaveBeenCalledTimes(1);
  });

  it("does not offer audit recovery for a structured image-service rejection", async () => {
    mocks.renderCustomDesign.mockRejectedValueOnce(new ApiError(
      422,
      {
        message: "The selected image model rejected an unsupported request option.",
        service: "openai_images",
        error_code: "invalid_input_fidelity_model",
      },
      "/api/backend/designs/render/71",
    ));
    const { container } = render(<StudioPage />);
    await prepareCustom(container);
    fireEvent.click(screen.getByRole("button", { name: /Generate design/i }));

    expect(await screen.findByText(/rejected an unsupported request option/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Recover completed render/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Generate design/i })).toBeEnabled();
  });

  it("offers audit recovery only for an ambiguous render server failure", async () => {
    mocks.renderCustomDesign.mockRejectedValueOnce(new ApiError(
      500,
      "Unexpected backend response",
      "/api/backend/designs/render/71",
    ));
    const { container } = render(<StudioPage />);
    await prepareCustom(container);
    fireEvent.click(screen.getByRole("button", { name: /Generate design/i }));

    expect(await screen.findByRole("button", { name: /Recover completed render/i })).toBeInTheDocument();
  });

  it("prepares Auto inventory before allowing its render", async () => {
    const { container } = render(<StudioPage />);
    fireEvent.click(screen.getByRole("button", { name: /Auto Design/i }));
    completeRequiredFields(container);
    fireEvent.click(screen.getByRole("button", { name: /Prepare AI inventory/i }));
    await screen.findByRole("heading", { name: "Inventory ready to render" });
    expect(mocks.prepareAutoDesign).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Add product" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Increase quantity for Walnut Lounge Chair" })).toBeEnabled();
    expect(mocks.renderAutoDesign).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Generate design/i }));
    await waitFor(() => expect(mocks.renderAutoDesign).toHaveBeenCalledTimes(1));
  });

  it("loads, applies, and renders only the exact manually persisted Template ID", async () => {
    window.history.replaceState({}, "", "/studio?mode=template");
    window.localStorage.setItem("aqar-ai-selected-template-v1", "7");
    const { container } = render(<StudioPage />);
    expect(await screen.findByText("Quiet Forest Living Room")).toBeInTheDocument();
    expect(mocks.getDesignTemplate).toHaveBeenCalledWith(7);
    expect(screen.getByTestId("template-fields")).toBeInTheDocument();
    expect(screen.queryByLabelText("Budget")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Preferred style")).not.toBeInTheDocument();
    completeRequiredFields(container);
    fireEvent.click(screen.getByRole("button", { name: /Apply selected template/i }));
    await screen.findByRole("heading", { name: "Inventory ready to render" });
    expect(mocks.prepareTemplateDesign).toHaveBeenCalledWith(71, 7);
    expect(screen.getByRole("button", { name: "Add product" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Remove Walnut Lounge Chair" })).toBeEnabled();
    expect(mocks.savePendingDesign).toHaveBeenCalledWith({
      room,
      recommendation: expect.objectContaining({
        selection_id: "22222222-2222-4222-8222-222222222222",
        selected_furniture: [selectedFurniture],
      }),
    });
    expect(mocks.renderTemplateDesign).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Generate design/i }));
    await waitFor(() => expect(mocks.renderCustomDesign).toHaveBeenCalledWith(
      71,
      "22222222-2222-4222-8222-222222222222",
    ));
    expect(mocks.renderTemplateDesign).not.toHaveBeenCalled();
  });

  it("opens and closes the mobile control sheet state", () => {
    const { container } = render(<StudioPage />);
    const controls = container.querySelector(".studio-controls");
    fireEvent.click(screen.getByRole("button", { name: "Room controls" }));
    expect(controls).toHaveClass("is-open");
    fireEvent.click(screen.getByRole("button", { name: "Close controls" }));
    expect(controls).not.toHaveClass("is-open");
  });
});
