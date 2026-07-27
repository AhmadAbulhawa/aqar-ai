import type {
  DesignTemplate,
  DesignTemplateDetail,
  FailedComplianceDetail,
  Furniture,
  InventoryRevisionResponse,
  ProductRequirementInput,
  RecommendationResponse,
  RenderResponse,
  RoomUploadResponse,
  SelectionLine,
  TemplateMatchResponse,
  TemplatePreparationResponse,
  ValidationDetail,
} from "@/types/api";

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || "/api/backend"
).replace(/\/$/, "");

export class ApiError extends Error {
  status: number;
  detail: ValidationDetail;
  endpoint: string;
  unreachable: boolean;

  constructor(
    status: number,
    detail: ValidationDetail,
    endpoint = "Unknown endpoint",
    unreachable = false,
  ) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.endpoint = endpoint;
    this.unreachable = unreachable;
  }
}

export function isFailedCompliance(
  detail: ValidationDetail,
): detail is FailedComplianceDetail {
  return (
    typeof detail === "object" &&
    detail !== null &&
    !Array.isArray(detail) &&
    detail.render_status === "failed_compliance"
  );
}

export function canRecoverCompletedRender(error: unknown) {
  if (!(error instanceof ApiError)) return false;
  const isRenderEndpoint =
    error.endpoint.includes("/designs/render/") ||
    error.endpoint.includes("/designs/templates/render/");
  if (!isRenderEndpoint || (error.status !== 0 && error.status < 500)) {
    return false;
  }
  const service =
    error.detail &&
    typeof error.detail === "object" &&
    !Array.isArray(error.detail) &&
    "service" in error.detail
      ? error.detail.service
      : null;
  // A structured image-service failure occurs before a render is saved. Only
  // ambiguous proxy, transport, or backend failures should offer audit recovery.
  return service !== "openai_images";
}

export function mediaUrl(path: string | null | undefined) {
  if (!path) return null;
  if (/^(https?:|blob:|data:)/.test(path)) return path;
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

export function furnitureImageUrl(item: Pick<Furniture, "image_filename">) {
  return item.image_filename
    ? mediaUrl(`/uploads/furniture/${item.image_filename}`)
    : null;
}

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const endpoint = `${API_BASE_URL}${path}`;
  let response: Response;
  try {
    response = await fetch(endpoint, init);
  } catch {
    throw new ApiError(
      0,
      {
        message: `The FastAPI backend is unreachable. Confirm it is running on ${API_BASE_URL}.`,
      },
      endpoint,
      true,
    );
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.detail ?? "Unexpected backend response",
      endpoint,
    );
  }
  return body as T;
}

export type FurnitureFilters = {
  category?: string;
  style?: string;
  color?: string;
  minPrice?: number;
  maxPrice?: number;
  availability?: boolean;
  storeName?: string;
  designEligible?: boolean;
  catalogStatus?: "active" | "shop_only" | "quarantined";
};

export function getFurniture(filters: FurnitureFilters = {}) {
  const params = new URLSearchParams();
  if (filters.category) params.set("category", filters.category);
  if (filters.style) params.set("style", filters.style);
  if (filters.color) params.set("color", filters.color);
  if (filters.minPrice !== undefined) params.set("min_price", String(filters.minPrice));
  if (filters.maxPrice !== undefined) params.set("max_price", String(filters.maxPrice));
  if (filters.availability !== undefined) {
    params.set("availability", String(filters.availability));
  }
  if (filters.storeName) params.set("store_name", filters.storeName);
  if (filters.designEligible !== undefined) {
    params.set("design_eligible", String(filters.designEligible));
  }
  if (filters.catalogStatus) params.set("catalog_status", filters.catalogStatus);
  const query = params.size ? `?${params.toString()}` : "";
  return apiRequest<Furniture[]>(`/furniture${query}`);
}

export function getFurnitureItem(id: number) {
  return apiRequest<Furniture>(`/furniture/${id}`);
}

export function getDesignTemplates(roomType?: string) {
  const query = roomType ? `?room_type=${encodeURIComponent(roomType)}` : "";
  return apiRequest<DesignTemplate[]>(`/designs/templates${query}`);
}

export function getDesignTemplate(templateId: number) {
  return apiRequest<DesignTemplateDetail>(`/designs/templates/${templateId}`);
}

export function uploadRoom(formData: FormData) {
  return apiRequest<RoomUploadResponse>("/rooms/upload", {
    method: "POST",
    body: formData,
  });
}

export type PrepareDesignPayload = {
  product_requirements: never[];
  required_products: Array<{ product_id: number; quantity: number }>;
  user_prompt: string | null;
  layout_preference: string | null;
};

export function buildPrepareDesignPayload(
  lines: SelectionLine[],
  userPrompt: string,
  layoutPreference: string,
): PrepareDesignPayload {
  return {
    product_requirements: [],
    required_products: lines.map(({ id, quantity }) => ({
      product_id: id,
      quantity,
    })),
    user_prompt: userPrompt.trim() || null,
    layout_preference: layoutPreference.trim() || null,
  };
}

export type AutoDesignPayload = {
  product_requirements: ProductRequirementInput[];
  required_products: never[];
  user_prompt: string | null;
  layout_preference: null;
};

export function buildAutoDesignPayload(
  categories: string[],
  userPrompt: string,
): AutoDesignPayload {
  return {
    product_requirements: categories.map((category) => ({
      category,
      mode: "required" as const,
      quantity: 1,
    })),
    required_products: [],
    user_prompt: userPrompt.trim() || null,
    layout_preference: null,
  };
}

export function prepareCustomDesign(
  roomRequestId: number,
  lines: SelectionLine[],
  userPrompt: string,
) {
  return apiRequest<RecommendationResponse>(
    `/designs/recommend/${roomRequestId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        product_requirements: [],
        required_products: lines.map(({ id, quantity }) => ({
          product_id: id,
          quantity,
        })),
        user_prompt: userPrompt.trim() || null,
        layout_preference: null,
      }),
    },
  );
}

export function prepareAutoDesign(
  roomRequestId: number,
  payload: AutoDesignPayload,
) {
  return apiRequest<RecommendationResponse>(
    `/designs/recommend/${roomRequestId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function matchDesignTemplates(roomRequestId: number, limit = 9) {
  return apiRequest<TemplateMatchResponse>(
    `/designs/templates/match/${roomRequestId}?limit=${limit}`,
    { method: "POST" },
  );
}

export function prepareTemplateDesign(
  roomRequestId: number,
  templateId: number,
) {
  return apiRequest<TemplatePreparationResponse>(
    `/designs/templates/apply/${roomRequestId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: templateId }),
    },
  );
}

export function reviseDesignSelection(
  selectionId: string,
  items: SelectionLine[],
) {
  return apiRequest<InventoryRevisionResponse>(
    `/designs/selections/${selectionId}/revise`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: items.map(({ id, quantity }) => ({
          product_id: id,
          quantity,
        })),
      }),
    },
  );
}

export function prepareDesign(
  roomRequestId: number,
  payload: PrepareDesignPayload,
) {
  return apiRequest<RecommendationResponse>(
    `/designs/recommend/${roomRequestId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function renderDesign(
  roomRequestId: number,
  selectionId: string,
  designStrategySignature: string | null,
) {
  return apiRequest<RenderResponse>(`/designs/render/${roomRequestId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      selection_id: selectionId,
      ...(designStrategySignature
        ? { design_strategy_signature: designStrategySignature }
        : {}),
    }),
  });
}

export function renderCustomDesign(
  roomRequestId: number,
  selectionId: string,
) {
  return apiRequest<RenderResponse>(`/designs/render/${roomRequestId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selection_id: selectionId }),
  });
}

export function renderAutoDesign(
  roomRequestId: number,
  selectionId: string,
  designStrategySignature: string | null,
) {
  return apiRequest<RenderResponse>(`/designs/render/${roomRequestId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      selection_id: selectionId,
      ...(designStrategySignature
        ? { design_strategy_signature: designStrategySignature }
        : {}),
    }),
  });
}

export function renderTemplateDesign(
  roomRequestId: number,
  templateId: number,
) {
  return apiRequest<RenderResponse>(
    `/designs/templates/render/${roomRequestId}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: templateId }),
    },
  );
}

export function getLatestGeneratedDesignAudit(
  roomRequestId: number,
  selectionId: string,
) {
  const query = new URLSearchParams({ selection_id: selectionId });
  return apiRequest<FailedComplianceDetail | Record<string, unknown>>(
    `/designs/generated/${roomRequestId}/latest?${query.toString()}`,
  );
}

export function validationMessage(detail: ValidationDetail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || "Invalid value").join(" · ");
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const composition = detail.composition_validation;
    if (
      composition &&
      typeof composition === "object" &&
      !Array.isArray(composition)
    ) {
      const compositionRecord = composition as Record<string, unknown>;
      if (compositionRecord.status !== "invalid") {
        if ("message" in detail) return String(detail.message);
        return "The backend could not process this request.";
      }
      const violations = Array.isArray(compositionRecord.violations)
        ? compositionRecord.violations
        : [];
      const firstViolation = violations.find(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === "object" && !Array.isArray(item)),
      );
      const reason = typeof firstViolation?.reason === "string"
        ? firstViolation.reason
        : typeof detail.message === "string"
          ? detail.message
          : "The selected products cannot physically fit the measured room.";
      const raw = typeof compositionRecord.raw_product_footprint_m2 === "number"
        ? compositionRecord.raw_product_footprint_m2.toFixed(2)
        : null;
      const effective =
        typeof compositionRecord.overlap_adjusted_effective_footprint_m2 === "number"
          ? compositionRecord.overlap_adjusted_effective_footprint_m2.toFixed(2)
          : null;
      const capacity = raw && effective
        ? ` Raw footprint ${raw} m²; overlap-adjusted footprint ${effective} m².`
        : "";
      return `Blocking error: physically impossible selection. ${reason}${capacity}`;
    }
  }
  if (detail && typeof detail === "object" && "message" in detail) {
    return String(detail.message);
  }
  return "The backend could not process this request.";
}

export function apiErrorMessage(error: unknown, fallback: string) {
  if (!(error instanceof ApiError)) return fallback;
  const status = error.status ? `HTTP ${error.status}` : "No HTTP status";
  const reachability = error.unreachable ? "Backend unreachable" : "Backend responded";
  return `${reachability} · ${error.endpoint} · ${status} · ${validationMessage(error.detail)}`;
}
