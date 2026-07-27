import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  API_BASE_URL,
  buildAutoDesignPayload,
  buildPrepareDesignPayload,
  getDesignTemplate,
  getDesignTemplates,
  isFailedCompliance,
  matchDesignTemplates,
  mediaUrl,
  prepareAutoDesign,
  prepareCustomDesign,
  prepareTemplateDesign,
  renderAutoDesign,
  renderCustomDesign,
  renderTemplateDesign,
  uploadRoom,
  validationMessage,
} from "./api";

const response = (body: unknown = {}) =>
  Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response);

describe("backend integration helpers", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn(() => response())));

  it("uses the same-origin backend proxy by default", () => {
    expect(API_BASE_URL).toBe("/api/backend");
  });

  it("resolves documented relative media paths against the backend", () => {
    expect(mediaUrl("/uploads/furniture/item.png")).toBe(`${API_BASE_URL}/uploads/furniture/item.png`);
    expect(mediaUrl("/uploads/rooms/room.jpg")).toBe(`${API_BASE_URL}/uploads/rooms/room.jpg`);
    expect(mediaUrl("/uploads/generated_designs/design.png")).toBe(`${API_BASE_URL}/uploads/generated_designs/design.png`);
  });

  it("builds exact product IDs and quantities for custom design", () => {
    expect(buildPrepareDesignPayload([{ id: 30, quantity: 2 }, { id: 7, quantity: 1 }], "Keep the windows open", "Conversation facing"))
      .toEqual({
        product_requirements: [],
        required_products: [
          { product_id: 30, quantity: 2 },
          { product_id: 7, quantity: 1 },
        ],
        user_prompt: "Keep the windows open",
        layout_preference: "Conversation facing",
      });
  });

  it("distinguishes failed compliance from ordinary validation 422", () => {
    expect(isFailedCompliance({ render_status: "failed_compliance" })).toBe(true);
    expect(isFailedCompliance([{ msg: "Field required" }])).toBe(false);
  });

  it("formats physical impossibility separately from dense warnings", () => {
    expect(validationMessage({
      message: "Inventory cannot fit.",
      composition_validation: {
        status: "invalid",
        confirmation_allowed: false,
        raw_product_footprint_m2: 18,
        overlap_adjusted_effective_footprint_m2: 16.5,
        violations: [{ reason: "Major products exceed the room floor area." }],
      },
    })).toBe(
      "Blocking error: physically impossible selection. Major products exceed the room floor area. Raw footprint 18.00 m²; overlap-adjusted footprint 16.50 m².",
    );

    expect(validationMessage({
      message: "Warning: dense selection, but Generate is allowed.",
      composition_validation: {
        status: "valid_with_warnings",
        confirmation_allowed: true,
      },
    })).toBe("Warning: dense selection, but Generate is allowed.");
  });

  it("preserves the backend image-service reason instead of masking every 502", () => {
    expect(validationMessage({
      message: "The configured API project is not permitted to run this image edit.",
      service: "openai_images",
      error_type: "PermissionDeniedError",
      selection_preserved: true,
      automatic_retry_attempted: false,
    })).toBe(
      "The configured API project is not permitted to run this image edit.",
    );
  });

  it("prepares Custom mode with exact IDs and quantities only", async () => {
    await prepareCustomDesign(41, [{ id: 30, quantity: 2 }], "Keep the door clear");
    expect(fetch).toHaveBeenCalledWith(
      `${API_BASE_URL}/designs/recommend/41`,
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          product_requirements: [],
          required_products: [{ product_id: 30, quantity: 2 }],
          user_prompt: "Keep the door clear",
          layout_preference: null,
        }),
      }),
    );
  });

  it("prepares Auto mode with category requirements and no exact products", async () => {
    const payload = buildAutoDesignPayload(["sofa", "rug"], "Warm and calm");
    await prepareAutoDesign(42, payload);
    expect(fetch).toHaveBeenCalledWith(
      `${API_BASE_URL}/designs/recommend/42`,
      expect.objectContaining({ method: "POST", body: JSON.stringify(payload) }),
    );
    expect(payload.required_products).toEqual([]);
  });

  it("uses the dedicated Template match and apply endpoints", async () => {
    await matchDesignTemplates(43, 9);
    await prepareTemplateDesign(43, 7);
    expect(fetch).toHaveBeenNthCalledWith(
      1,
      `${API_BASE_URL}/designs/templates/match/43?limit=9`,
      { method: "POST" },
    );
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      `${API_BASE_URL}/designs/templates/apply/43`,
      expect.objectContaining({ body: JSON.stringify({ template_id: 7 }) }),
    );
  });

  it("loads the real template gallery and exact template detail endpoints", async () => {
    await getDesignTemplates();
    await getDesignTemplate(7);
    expect(fetch).toHaveBeenNthCalledWith(1, `${API_BASE_URL}/designs/templates`, undefined);
    expect(fetch).toHaveBeenNthCalledWith(2, `${API_BASE_URL}/designs/templates/7`, undefined);
  });

  it("lets the browser set the multipart boundary for room uploads", async () => {
    const form = new FormData();
    form.append("image", new File(["room"], "room.jpg", { type: "image/jpeg" }));
    await uploadRoom(form);
    expect(fetch).toHaveBeenCalledWith(
      `${API_BASE_URL}/rooms/upload`,
      { method: "POST", body: form },
    );
    const init = vi.mocked(fetch).mock.calls[0]?.[1];
    expect(init?.headers).toBeUndefined();
  });

  it("reports a transport failure with endpoint and no fabricated HTTP status", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(getDesignTemplates()).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
      endpoint: `${API_BASE_URL}/designs/templates`,
      unreachable: true,
    });
  });

  it("renders Custom and Auto through the immutable selection endpoint with distinct payloads", async () => {
    await renderCustomDesign(51, "custom-selection");
    await renderAutoDesign(52, "auto-selection", "a".repeat(64));
    expect(fetch).toHaveBeenNthCalledWith(
      1,
      `${API_BASE_URL}/designs/render/51`,
      expect.objectContaining({ body: JSON.stringify({ selection_id: "custom-selection" }) }),
    );
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      `${API_BASE_URL}/designs/render/52`,
      expect.objectContaining({
        body: JSON.stringify({
          selection_id: "auto-selection",
          design_strategy_signature: "a".repeat(64),
        }),
      }),
    );
  });

  it("renders Template mode through its dedicated endpoint", async () => {
    await renderTemplateDesign(53, 8);
    expect(fetch).toHaveBeenCalledWith(
      `${API_BASE_URL}/designs/templates/render/53`,
      expect.objectContaining({ body: JSON.stringify({ template_id: 8 }) }),
    );
  });
});
