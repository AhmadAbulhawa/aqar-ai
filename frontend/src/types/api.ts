export type FurnitureStyle = "modern" | "classic" | "minimal" | "luxury";

export type RoomType =
  | "living_room"
  | "bedroom"
  | "office"
  | "dining_room"
  | "bathroom"
  | "kitchen";

export type Furniture = {
  id: number;
  name: string;
  category: string;
  style: FurnitureStyle | null;
  color: string | null;
  material: string | null;
  width_cm: number | null;
  depth_cm: number | null;
  height_cm: number | null;
  price: number;
  image_filename: string | null;
  product_url: string;
  store_name: string | null;
  currency: string;
  room_types: RoomType[];
  tags: string[];
  availability: boolean;
  catalog_status: string;
  quarantine_reason: string | null;
  source_store_slug: string | null;
  source_product_id: string | null;
  canonical_product_url: string | null;
  name_ar: string | null;
  name_en: string | null;
  subcategory: string | null;
  original_price: number | null;
  source_image_url: string | null;
  additional_source_image_urls: string[];
  diameter_cm: number | null;
  description: string | null;
  brand: string | null;
  model_number: string | null;
  source_metadata: Record<string, unknown>;
  imported_at: string | null;
  source_updated_at: string | null;
  design_eligible: boolean;
  mounting_type: string | null;
  is_product_set: boolean;
  included_pieces: Array<string | Record<string, unknown>>;
  normalized_identity: string | null;
  image_sha256: string | null;
  last_seen_import_run: string | null;
};

export type SelectionLine = {
  id: number;
  quantity: number;
};

export type DesignMode = "custom" | "auto" | "template";

export type ProductRequirementInput = {
  category: string;
  mode: "auto" | "required" | "excluded";
  quantity: number | null;
};

export type SelectedFurniture = {
  id: number;
  name: string;
  category: string;
  style: FurnitureStyle | null;
  color: string | null;
  material?: string | null;
  width_cm?: number | null;
  depth_cm?: number | null;
  height_cm?: number | null;
  price: number;
  currency: string;
  image_filename: string | null;
  image_url: string | null;
  product_url: string;
  store_name: string | null;
  selection_role: string;
  selection_origin: string | null;
  product_origin?: "from_template" | "added_to_complete_room" | "user_selected" | "unknown";
  added_for_completion?: boolean;
  was_included?: boolean;
  inclusion_reason?: string | null;
  included_quantity?: number;
  completion_quantity?: number;
  quantity_edited_by_user?: boolean;
  quantity: number;
  line_total: number;
};

export type CapacityProduct = {
  product_id: number | null;
  name?: string | null;
  category: string;
  floor_area_classification?: string;
  quantity: number;
  raw_footprint_m2: number;
  effective_footprint_m2: number;
  reason?: string;
};

export type CompositionValidation = {
  status: "valid" | "valid_with_warnings" | "invalid" | "uncertain_geometry";
  density_classification?:
    | "comfortable"
    | "moderately_dense"
    | "high_density"
    | "physically_implausible"
    | null;
  confirmation_allowed?: boolean;
  room_type?: string | null;
  room_dimensions_cm?: {
    length_cm: number;
    width_cm: number;
    height_cm?: number | null;
  } | null;
  room_floor_area_m2?: number | null;
  usable_floor_area_m2?: number | null;
  circulation_reserve_area_m2?: number | null;
  raw_product_footprint_m2?: number | null;
  major_furniture_footprint_m2?: number | null;
  overlap_adjusted_effective_footprint_m2?: number | null;
  effective_occupancy_ratio?: number | null;
  estimated_required_area_m2?: number | null;
  estimated_density_ratio?: number | null;
  excluded_from_independent_floor_area?: CapacityProduct[];
  overlap_adjustments?: CapacityProduct[];
  largest_space_consuming_products?: CapacityProduct[];
  violations?: Array<Record<string, unknown>>;
  warnings?: string[];
  assumptions?: string[];
};

export type InventoryRevisionResponse = {
  message: string;
  room_request_id: number;
  template_id: number | null;
  mode: string;
  base_selection_id: string;
  selection_id: string;
  selection_reused: boolean;
  selection_status: string;
  design_strategy_signature: string | null;
  selected_furniture_ids: number[];
  selected_products: Array<{
    id: number;
    quantity: number;
    product_origin?: SelectedFurniture["product_origin"];
    selection_role?: string;
  }>;
  selected_furniture: SelectedFurniture[];
  composition_validation: CompositionValidation;
  total_price: number;
  currency: string;
};

export type RoomUploadResponse = {
  message: string;
  room_request_id: number;
  filename: string;
  original_filename: string;
  content_type: string;
  room_type: RoomType;
  budget: number | null;
  style: FurnitureStyle | null;
  preferred_colors: string[];
  room_dimensions: {
    length_cm: number;
    width_cm: number;
    height_cm: number | null;
  } | null;
};

export type RecommendationResponse = {
  room_request: {
    id: number;
    image_filename: string;
    original_filename: string;
    content_type: string;
    room_type: RoomType;
    budget: number | null;
    style: FurnitureStyle | null;
    preferred_colors: string[];
  };
  selected_furniture_ids: number[];
  selected_products: Array<{ id: number; quantity: number }>;
  selected_furniture: SelectedFurniture[];
  selection_id: string;
  selection_reused: boolean;
  selection_status: string;
  design_strategy_signature: string | null;
  design_strategy_fallback?: boolean;
  room_analysis?: {
    analysis_source?: "vision_model" | "measured_fallback";
    analysis_warning?: string | null;
  };
  composition_validation?: CompositionValidation;
  total_price: number;
  currency: string;
};

export type DesignTemplate = {
  id: number;
  name: string;
  room_type: RoomType;
  style: FurnitureStyle | null;
  size_category: string | null;
  layout_type: string | null;
  description: string | null;
  preview_image_filename: string | null;
  source_name: string | null;
  source_url: string | null;
  tags: string[];
  preview_image_url?: string | null;
  source_product_count: number;
  matched_product_count: number;
  skipped_product_count: number;
  failed_product_count: number;
  missing_product_count: number;
};

export type TemplateCatalogProduct = {
  id: number;
  name: string;
  category: string;
  style: FurnitureStyle | null;
  color: string | null;
  material: string | null;
  width_cm: number | null;
  depth_cm: number | null;
  height_cm: number | null;
  price: number;
  currency: string;
  image_filename: string | null;
  image_url: string | null;
  product_url: string;
  store_name: string | null;
  availability: boolean;
  catalog_status: string;
};

export type TemplateRepresentedProduct = {
  slot_id: number;
  category: string;
  quantity: number;
  role: string | null;
  priority: string | null;
  preferred_style: string | null;
  preferred_colors: string[];
  matched_product: TemplateCatalogProduct | null;
};

export type DesignTemplateDetail = DesignTemplate & {
  preview_image_url: string | null;
  represented_products: TemplateRepresentedProduct[];
  available_matched_catalog_products: TemplateCatalogProduct[];
  unresolved_slots: Array<Record<string, unknown>>;
  estimated_product_total: number;
  currency: string | null;
};

export type MatchedTemplate = {
  template: DesignTemplate;
  score: number;
  reasons: string[];
};

export type TemplateMatchResponse = {
  room_request: RecommendationResponse["room_request"];
  room_analysis: Record<string, unknown>;
  room_analysis_cached: boolean;
  matched_templates: MatchedTemplate[];
};

export type TemplatePreparationResponse = {
  message: string;
  design_mode: "template_product_source" | "auto_template_blueprint";
  template_semantics?: "product_source_only";
  template_layout_constraints_applied?: false;
  template_product_source_signature?: string;
  template_product_source?: {
    semantics: "product_source_only";
    template_id: number;
    matched_products: Array<{ id: number; quantity: number; category: string }>;
    layout_constraints_applied: false;
  };
  selection_id: string;
  selection_reused: boolean;
  selection_status: string;
  room_request_id: number;
  selected_template: DesignTemplate & {
    preview_image_url: string | null;
    preview_image_available: boolean;
  };
  selected_furniture_ids: number[];
  selected_products: Array<{
    id: number;
    quantity: number;
    product_origin?: SelectedFurniture["product_origin"];
    selection_role?: string;
  }>;
  selected_furniture: SelectedFurniture[];
  composition_validation?: CompositionValidation;
  total_price: number;
  currency: string;
};

export type ComplianceResult = {
  status: string;
  hard_compliance_failure?: boolean;
  hard_failure_codes?: string[];
  failure_codes?: string[];
  failure_reasons?: string[];
  violations?: string[];
  warnings?: string[];
};

export type RenderResponse = {
  message: string;
  selection_source: string;
  selection_id: string;
  selection_status: string;
  generated_design_id: string;
  room_request_id: number;
  room_type: RoomType;
  style: FurnitureStyle | null;
  design_strategy_signature: string | null;
  compliance: ComplianceResult;
  generated_image: {
    filename: string;
    local_path: string;
    image_url: string;
    model: string;
    input_image_count: number;
    output_size: string;
  };
  room_image: {
    filename: string;
    local_path: string;
    image_url: string;
  };
  selected_furniture_ids: number[];
  selected_products: Array<{ id: number; quantity: number }>;
  selected_furniture: SelectedFurniture[];
  total_price: number;
  currency: string;
};

export type FailedComplianceDetail = {
  message: string;
  render_status: "failed_compliance";
  compliance_status: string;
  hard_compliance_failure: boolean;
  hard_failure_codes: string[];
  failure_codes: string[];
  failure_reasons: string[];
  violations: string[];
  warnings: string[];
  generated_design_id: string;
  generated_image?: {
    filename: string;
    local_path: string;
    image_url: string;
    preserved: boolean;
  };
  automatic_paid_retry_enabled: boolean;
  automatic_retry_attempted: boolean;
  retry_policy: string;
};

export type ValidationDetail =
  | string
  | Array<{ loc?: Array<string | number>; msg?: string; type?: string }>
  | Record<string, unknown>;
