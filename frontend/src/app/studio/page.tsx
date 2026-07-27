"use client";

import {
  Armchair,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BedDouble,
  BriefcaseBusiness,
  Check,
  ChevronDown,
  CookingPot,
  LampFloor,
  Layers3,
  LockKeyhole,
  Package,
  Palette,
  PanelsTopLeft,
  PanelTop,
  Plus,
  SlidersHorizontal,
  Sofa,
  Sparkles,
  Table2,
  Tv,
  Upload,
  WalletCards,
  WandSparkles,
  X,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ProductImage } from "@/components/product-image";
import { StudioInventoryEditor } from "@/components/studio-inventory-editor";
import { useSelection } from "@/context/selection-context";
import {
  ApiError,
  apiErrorMessage,
  buildAutoDesignPayload,
  canRecoverCompletedRender,
  furnitureImageUrl,
  getFurniture,
  getDesignTemplate,
  getLatestGeneratedDesignAudit,
  isFailedCompliance,
  mediaUrl,
  prepareAutoDesign,
  prepareCustomDesign,
  prepareTemplateDesign,
  reviseDesignSelection,
  renderAutoDesign,
  renderCustomDesign,
  uploadRoom,
} from "@/lib/api";
import { displayStoreName } from "@/lib/brands";
import { dimensionsLabel, formatMoney, humanize } from "@/lib/format";
import {
  saveComplianceFailure,
  saveDesignResult,
  savePendingDesign,
} from "@/lib/result-storage";
import {
  activeStageIndex,
  completedStageCount,
  GENERATION_STAGES,
  generationBlocker,
  preparationBlocker,
  type StudioPhase,
} from "@/lib/studio-workflow";
import { roomCatalogReadiness } from "@/lib/room-catalog-readiness";
import { loadSelectedTemplate } from "@/lib/template-selection";
import type {
  DesignMode,
  DesignTemplateDetail,
  Furniture,
  FurnitureStyle,
  RecommendationResponse,
  RoomType,
  RoomUploadResponse,
  SelectedFurniture,
  TemplatePreparationResponse,
} from "@/types/api";

const roomTypes: RoomType[] = [
  "living_room",
  "bedroom",
  "dining_room",
  "office",
  "kitchen",
  "bathroom",
];
const styles: FurnitureStyle[] = ["modern", "minimal", "classic", "luxury"];
const budgetPresets = [400, 800, 1200, 2000];
const palettePresets = [
  {
    label: "Warm & grounded",
    value: "Warm white, walnut, olive",
    swatches: ["#eee6d8", "#8c5d3d", "#68704a"],
  },
  {
    label: "Soft & airy",
    value: "Soft white, light oak, sky blue",
    swatches: ["#f6f5ef", "#d7b98c", "#a9cde1"],
  },
  {
    label: "Earthy calm",
    value: "Sand, terracotta, sage",
    swatches: ["#d8c09e", "#b96f52", "#9ba987"],
  },
  {
    label: "Deep contrast",
    value: "Charcoal, cream, brass",
    swatches: ["#30343b", "#eee7d8", "#b38a4f"],
  },
];
const styleDescriptions: Record<FurnitureStyle, string> = {
  modern: "Clean lines",
  minimal: "Quiet essentials",
  classic: "Timeless detail",
  luxury: "Rich finishes",
};
const modes: Array<{
  id: DesignMode;
  number: string;
  name: string;
  description: string;
}> = [
  {
    id: "custom",
    number: "01",
    name: "Custom Furniture",
    description: "Use only the exact products already selected in the Shop.",
  },
  {
    id: "auto",
    number: "02",
    name: "Auto Design",
    description: "Set the direction, then review the backend-selected inventory.",
  },
  {
    id: "template",
    number: "03",
    name: "Template Design",
    description: "Start with a curated product collection, then complete functional gaps.",
  },
];

function AutoCategoryIcon({ category }: { category: string }) {
  const name = category.toLowerCase();
  if (name.includes("sofa")) return <Sofa size={21} strokeWidth={1.7} />;
  if (name.includes("armchair") || name === "chair") {
    return <Armchair size={21} strokeWidth={1.7} />;
  }
  if (name.includes("bed")) return <BedDouble size={21} strokeWidth={1.7} />;
  if (name.includes("lamp") || name.includes("light")) {
    return <LampFloor size={21} strokeWidth={1.7} />;
  }
  if (name.includes("television") || name === "tv") {
    return <Tv size={21} strokeWidth={1.7} />;
  }
  if (
    name.includes("table") ||
    name.includes("desk") ||
    name.includes("console")
  ) {
    return <Table2 size={21} strokeWidth={1.7} />;
  }
  if (
    name.includes("wardrobe") ||
    name.includes("cabinet") ||
    name.includes("stand") ||
    name.includes("storage")
  ) {
    return <PanelTop size={21} strokeWidth={1.7} />;
  }
  if (name.includes("rug") || name.includes("carpet")) {
    return <PanelsTopLeft size={21} strokeWidth={1.7} />;
  }
  if (name.includes("office")) {
    return <BriefcaseBusiness size={21} strokeWidth={1.7} />;
  }
  if (name.includes("kitchen")) {
    return <CookingPot size={21} strokeWidth={1.7} />;
  }
  if (name.includes("mirror") || name.includes("accessor")) {
    return <Sparkles size={21} strokeWidth={1.7} />;
  }
  return <Package size={21} strokeWidth={1.7} />;
}

function ProgressRail({ phase }: { phase: StudioPhase }) {
  const active = activeStageIndex(phase);
  const complete = completedStageCount(phase);
  return (
    <ol className="studio-progress" aria-label="Generation progress">
      {GENERATION_STAGES.map((label, index) => (
        <li
          key={label}
          className={index < complete ? "is-complete" : index === active ? "is-active" : ""}
        >
          <span>{index < complete ? <Check size={11} /> : index + 1}</span>
          <p>{label}</p>
        </li>
      ))}
    </ol>
  );
}

export default function StudioPage() {
  const router = useRouter();
  const { lines, ready } = useSelection();
  const [mode, setMode] = useState<DesignMode>("custom");
  const [products, setProducts] = useState<Furniture[]>([]);
  const [catalogLoaded, setCatalogLoaded] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [roomType, setRoomType] = useState<RoomType>("living_room");
  const [length, setLength] = useState("");
  const [width, setWidth] = useState("");
  const [height, setHeight] = useState("");
  const [note, setNote] = useState("");
  const [style, setStyle] = useState<FurnitureStyle | "">("");
  const [colors, setColors] = useState("");
  const [budget, setBudget] = useState("");
  const [requiredCategories, setRequiredCategories] = useState<string[]>([]);
  const [uploadedRoom, setUploadedRoom] = useState<RoomUploadResponse | null>(null);
  const [recommendation, setRecommendation] = useState<RecommendationResponse | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState<DesignTemplateDetail | null>(null);
  const [templatePreparation, setTemplatePreparation] = useState<TemplatePreparationResponse | null>(null);
  const [inventoryDraft, setInventoryDraft] = useState<SelectedFurniture[]>([]);
  const [draftSelectionId, setDraftSelectionId] = useState<string | null>(null);
  const [inventoryDirty, setInventoryDirty] = useState(false);
  const [inventorySaving, setInventorySaving] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [phase, setPhase] = useState<StudioPhase>("idle");
  const [ordinaryError, setOrdinaryError] = useState("");
  const [recoveryAvailable, setRecoveryAvailable] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [controlsOpen, setControlsOpen] = useState(false);
  const renderGuard = useRef(false);
  const prepareGuard = useRef(false);
  const lockedSelectionId = recommendation?.selection_id || templatePreparation?.selection_id || null;
  const lockedInventory = recommendation?.selected_furniture || templatePreparation?.selected_furniture || [];

  useEffect(() => {
    if (!ready) return;
    getFurniture()
      .then((items) => {
        setProducts(items);
        setCatalogLoaded(true);
      })
      .catch(() => {
        setCatalogLoaded(true);
        setOrdinaryError("The live furniture catalog could not be loaded.");
      });
  }, [ready]);

  useEffect(() => {
    let cancelled = false;
    async function restoreManualTemplate() {
      await Promise.resolve();
      const requestedMode = new URLSearchParams(window.location.search).get("mode");
      if (requestedMode !== "template" || cancelled) return;
      const persistedTemplateId = loadSelectedTemplate();
      setMode("template");
      if (!persistedTemplateId) {
        setOrdinaryError(
          "Choose one exact design from the Templates collection before opening Template Studio.",
        );
        return;
      }
      setSelectedTemplateId(persistedTemplateId);
      try {
        const restoredTemplate = await getDesignTemplate(persistedTemplateId);
        if (!cancelled) {
          setSelectedTemplate(restoredTemplate);
          setRoomType(restoredTemplate.room_type);
        }
      } catch (error) {
        if (!cancelled) {
          setOrdinaryError(
            apiErrorMessage(error, "The selected template could not be loaded."),
          );
        }
      }
    }
    void restoreManualTemplate();
    return () => { cancelled = true; };
  }, []);

  const preview = useMemo(() => (file ? URL.createObjectURL(file) : null), [file]);
  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );

  const selectedProducts = useMemo(
    () =>
      lines
        .map((line) => ({
          line,
          product: products.find((item) => item.id === line.id),
        }))
        .filter(
          (entry): entry is { line: (typeof lines)[number]; product: Furniture } =>
            Boolean(entry.product),
        ),
    [lines, products],
  );
  const availableCategories = useMemo(
    () =>
      Array.from(
        new Set(
          products
            .filter(
              (item) =>
                item.availability &&
                item.catalog_status === "active" &&
                item.design_eligible &&
                item.room_types.includes(roomType),
            )
            .map((item) => item.category),
        ),
      ).sort(),
    [products, roomType],
  );
  const activeAutoSignals =
    Number(Boolean(budget)) +
    Number(Boolean(style)) +
    Number(Boolean(colors.trim())) +
    requiredCategories.length;
  const selectedTotal = selectedProducts.reduce(
    (sum, { line, product }) => sum + product.price * line.quantity,
    0,
  );
  const preparedInventory =
    draftSelectionId === lockedSelectionId ? inventoryDraft : lockedInventory;
  const preparedTotal = inventoryDirty
    ? preparedInventory.reduce((sum, item) => sum + item.line_total, 0)
    : recommendation?.total_price ?? templatePreparation?.total_price ?? selectedTotal;
  const preparedCurrency =
    recommendation?.currency ||
    templatePreparation?.currency ||
    selectedProducts[0]?.product.currency ||
    "JOD";
  const isPrepared = Boolean(recommendation || templatePreparation);
  const activeComposition =
    recommendation?.composition_validation || templatePreparation?.composition_validation;
  const densityWarning = activeComposition?.confirmation_allowed !== false && (
    activeComposition?.density_classification === "moderately_dense" ||
    activeComposition?.density_classification === "high_density"
  );
  const largestCapacityProducts =
    activeComposition?.largest_space_consuming_products?.filter(
      (item) => item.effective_footprint_m2 > 0,
    ).slice(0, 3) || [];
  const busy = preparing || rendering || inventorySaving;
  const baseBlocker = preparationBlocker(mode, {
    hasRoomImage: Boolean(file || uploadedRoom),
    hasLength: Boolean(length),
    hasWidth: Boolean(width),
    selectedProductCount: lines.length,
    selectedTemplateId,
  });
  const catalogReadiness = useMemo(
    () => roomCatalogReadiness(products, roomType),
    [products, roomType],
  );
  const missingCatalogLabels = [
    ...catalogReadiness.missingRequired.map(humanize),
    ...catalogReadiness.missingOneOf.map((group) =>
      group.map(humanize).join(" or "),
    ),
  ];
  const catalogBlocker =
    mode === "auto" && catalogLoaded && !catalogReadiness.ready
      ? `The live catalog cannot prepare a complete ${humanize(roomType)} yet. Missing: ${missingCatalogLabels.join(", ")}.`
      : null;
  const blocker = baseBlocker || catalogBlocker;
  const generateReason = busy
    ? generationBlocker(isPrepared, true)
    : isPrepared
      ? inventoryDirty
        ? "Save inventory changes before generating the design."
        : null
      : blocker || generationBlocker(false, false);
  const modeInputReady =
    mode === "custom" ? lines.length > 0 : mode === "template" ? Boolean(selectedTemplateId) : true;
  const readinessChecks = [
    Boolean(file || uploadedRoom),
    Boolean(length),
    Boolean(width),
    modeInputReady,
  ];
  const readinessCount = readinessChecks.filter(Boolean).length;

  function clearPreparedState() {
    setRecommendation(null);
    setTemplatePreparation(null);
    setInventoryDraft([]);
    setDraftSelectionId(null);
    setInventoryDirty(false);
    setOrdinaryError("");
    setRecoveryAvailable(false);
    setPhase("idle");
    renderGuard.current = false;
  }

  async function handleSaveInventory() {
    if (!lockedSelectionId || inventorySaving || !inventoryDraft.length) return;
    setInventorySaving(true);
    setOrdinaryError("");
    try {
      const revision = await reviseDesignSelection(
        lockedSelectionId,
        inventoryDraft.map((item) => ({ id: item.id, quantity: item.quantity })),
      );
      if (recommendation) {
        const nextRecommendation: RecommendationResponse = {
          ...recommendation,
          selection_id: revision.selection_id,
          selection_reused: revision.selection_reused,
          selection_status: revision.selection_status,
          design_strategy_signature: revision.design_strategy_signature,
          selected_furniture_ids: revision.selected_furniture_ids,
          selected_products: revision.selected_products,
          selected_furniture: revision.selected_furniture,
          total_price: revision.total_price,
          currency: revision.currency,
          composition_validation: revision.composition_validation,
        };
        setRecommendation(nextRecommendation);
        if (uploadedRoom) {
          savePendingDesign({ room: uploadedRoom, recommendation: nextRecommendation });
        }
      } else if (templatePreparation) {
        const nextTemplatePreparation: TemplatePreparationResponse = {
          ...templatePreparation,
          selection_id: revision.selection_id,
          selection_reused: revision.selection_reused,
          selection_status: revision.selection_status,
          selected_furniture_ids: revision.selected_furniture_ids,
          selected_products: revision.selected_products,
          selected_furniture: revision.selected_furniture,
          total_price: revision.total_price,
          currency: revision.currency,
          composition_validation: revision.composition_validation,
        };
        setTemplatePreparation(nextTemplatePreparation);
        if (uploadedRoom) {
          savePendingDesign({ room: uploadedRoom, recommendation: nextTemplatePreparation });
        }
      }
      setInventoryDraft(revision.selected_furniture);
      setDraftSelectionId(revision.selection_id);
      setInventoryDirty(false);
      setPhase("ready");
    } catch (error) {
      setOrdinaryError(
        apiErrorMessage(error, "The edited inventory could not be saved."),
      );
    } finally {
      setInventorySaving(false);
    }
  }

  function invalidateRoomRequest() {
    clearPreparedState();
    setUploadedRoom(null);
  }

  function changeMode(nextMode: DesignMode) {
    if (nextMode === mode || busy) return;
    if (nextMode === "template") {
      router.push("/templates");
      return;
    }
    setMode(nextMode);
    setAttempted(false);
    setControlsOpen(false);
    invalidateRoomRequest();
  }

  function buildRoomUpload() {
    if (!file) throw new Error("A room image is required.");
    const form = new FormData();
    form.append("image", file);
    form.append("room_type", roomType);
    form.append("room_length_cm", length);
    form.append("room_width_cm", width);
    if (height) form.append("room_height_cm", height);
    if (mode === "auto") {
      if (budget) form.append("budget", budget);
      if (style) form.append("style", style);
      if (colors.trim()) form.append("preferred_colors", colors.trim());
    }
    return form;
  }

  async function ensureRoomUploaded() {
    if (uploadedRoom) return uploadedRoom;
    setPhase("uploading");
    const room = await uploadRoom(buildRoomUpload());
    setUploadedRoom(room);
    return room;
  }

  async function handlePrepare(event?: React.FormEvent) {
    event?.preventDefault();
    setAttempted(true);
    if (prepareGuard.current || busy || isPrepared) return;
    if (blocker) {
      setOrdinaryError(blocker);
      return;
    }
    prepareGuard.current = true;
    setPreparing(true);
    setOrdinaryError("");
    try {
      const room = await ensureRoomUploaded();
      if (mode === "custom") {
        setPhase("analyzing");
        const prepared = await prepareCustomDesign(room.room_request_id, lines, note);
        setRecommendation(prepared);
        setInventoryDraft(prepared.selected_furniture);
        setDraftSelectionId(prepared.selection_id);
        setInventoryDirty(false);
        savePendingDesign({ room, recommendation: prepared });
      } else if (mode === "auto") {
        setPhase("analyzing");
        const prepared = await prepareAutoDesign(
          room.room_request_id,
          buildAutoDesignPayload(requiredCategories, note),
        );
        setRecommendation(prepared);
        setInventoryDraft(prepared.selected_furniture);
        setDraftSelectionId(prepared.selection_id);
        setInventoryDirty(false);
        savePendingDesign({ room, recommendation: prepared });
      } else {
        if (!selectedTemplateId) {
          setOrdinaryError("Select one matched template before preparation.");
          return;
        }
        setPhase("preparing");
        const prepared = await prepareTemplateDesign(
          room.room_request_id,
          selectedTemplateId,
        );
        setTemplatePreparation(prepared);
        setInventoryDraft(prepared.selected_furniture);
        setDraftSelectionId(prepared.selection_id);
        setInventoryDirty(false);
        savePendingDesign({ room, recommendation: prepared });
      }
      setPhase("ready");
    } catch (error) {
      setPhase("idle");
      setOrdinaryError(
        apiErrorMessage(
          error,
          error instanceof Error
            ? error.message
            : "The Studio request could not be prepared.",
        ),
      );
    } finally {
      prepareGuard.current = false;
      setPreparing(false);
    }
  }

  async function handleRender() {
    if (renderGuard.current || rendering) return;
    if (!uploadedRoom || !isPrepared) {
      setOrdinaryError("Review and lock the inventory before generation.");
      return;
    }
    renderGuard.current = true;
    setRendering(true);
    setPhase("designing");
    setOrdinaryError("");
    setRecoveryAvailable(false);
    try {
      let result;
      if (mode === "custom" && recommendation) {
        result = await renderCustomDesign(
          uploadedRoom.room_request_id,
          recommendation.selection_id,
        );
      } else if (mode === "auto" && recommendation) {
        result = await renderAutoDesign(
          uploadedRoom.room_request_id,
          recommendation.selection_id,
          recommendation.design_strategy_signature,
        );
      } else if (mode === "template" && templatePreparation) {
        result = await renderCustomDesign(
          uploadedRoom.room_request_id,
          templatePreparation.selection_id,
        );
      } else {
        throw new Error("The active mode has no prepared inventory.");
      }
      setPhase("checking");
      saveDesignResult(result);
      router.push("/result?status=success");
    } catch (error) {
      if (error instanceof ApiError && error.status === 422 && isFailedCompliance(error.detail)) {
        setRecoveryAvailable(false);
        setPhase("checking");
        saveComplianceFailure(error.detail);
        router.push("/result?status=failed-compliance");
        return;
      }
      setRecoveryAvailable(canRecoverCompletedRender(error));
      setOrdinaryError(
        apiErrorMessage(
          error,
          error instanceof Error
            ? error.message
            : "The render request could not be completed.",
        ),
      );
      setPhase("ready");
      renderGuard.current = false;
      setRendering(false);
    }
  }

  async function handleRecoverRender() {
    if (!uploadedRoom || !lockedSelectionId || rendering) return;
    const originalRenderError = ordinaryError;
    setRendering(true);
    try {
      const audit = await getLatestGeneratedDesignAudit(
        uploadedRoom.room_request_id,
        lockedSelectionId,
      );
      if (isFailedCompliance(audit)) {
        saveComplianceFailure(audit);
        router.push("/result?status=failed-compliance");
        return;
      }
      setOrdinaryError(
        "A completed render audit exists, but this Studio version cannot reconstruct its full success payload. The preserved image remains available in the backend audit.",
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setRecoveryAvailable(false);
        setOrdinaryError(
          originalRenderError ||
            "No completed render was saved. The locked inventory is still ready, so you can generate again explicitly.",
        );
      } else {
        setOrdinaryError(
          apiErrorMessage(error, "The completed render audit could not be checked."),
        );
      }
    } finally {
      setRendering(false);
    }
  }

  const preparationLabel =
    mode === "template"
        ? "Apply selected template"
        : mode === "auto"
          ? "Prepare AI inventory"
          : "Review exact inventory";

  return (
    <div className="studio-page page-enter">
      <header className="studio-masthead">
        <div className="page-shell">
          <div>
            <p className="eyebrow text-white/45">Aqar AI · Design Studio</p>
            <h1>Your room,<br /><em>fully considered.</em></h1>
            <p className="studio-masthead-copy">Upload the room you already have, ground the design in exact dimensions, and approve every product before generation.</p>
          </div>
          <div className="studio-masthead-notes" aria-label="Studio workflow summary">
            <div><span>01</span><p>Upload your room</p></div>
            <div><span>02</span><p>Review real inventory</p></div>
            <div><span>03</span><p>Generate once</p></div>
          </div>
        </div>
      </header>

      <nav className="page-shell studio-mode-selector" aria-label="Design mode">
        {modes.map((item) => (
          <button
            key={item.id}
            type="button"
            className={mode === item.id ? "is-active" : ""}
            aria-pressed={mode === item.id}
            onClick={() => changeMode(item.id)}
          >
            <span>{item.number}</span>
            <strong>{item.name}</strong>
            <p>{item.description}</p>
            <i>{mode === item.id ? <Check size={13} /> : <ArrowRight size={13} />}</i>
          </button>
        ))}
      </nav>

      <form onSubmit={handlePrepare} className="page-shell studio-workspace">
        <main className="studio-canvas-column">
          <section className="studio-canvas" aria-label="Room image workspace">
            <div className="studio-canvas-toolbar">
              <span><i /> Room canvas</span>
              <span>{file?.name || "Awaiting room image"}</span>
            </div>
            {preview ? (
              // This preview remains browser-local until the explicit preparation action.
              // eslint-disable-next-line @next/next/no-img-element
              <img src={preview} alt="Selected room preview" className="studio-room-preview" />
            ) : (
              <label className="studio-empty-canvas">
                <span><Upload size={21} /></span>
                <p>Show us the room<br />before the design.</p>
                <small>Drop or choose a JPEG, PNG, or WEBP image</small>
                <strong>Choose room image <ArrowUpRight size={13} /></strong>
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  className="sr-only"
                  onChange={(event) => {
                    setFile(event.target.files?.[0] || null);
                    invalidateRoomRequest();
                  }}
                />
              </label>
            )}

            {preview && !busy && (
              <label className="studio-replace-image">
                Replace image
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  className="sr-only"
                  onChange={(event) => {
                    setFile(event.target.files?.[0] || null);
                    invalidateRoomRequest();
                  }}
                />
              </label>
            )}

            {busy && (
              <div className="studio-generation-overlay" aria-live="polite">
                <div className="studio-generation-copy">
                  <span className="studio-pulse" />
                  <p>{GENERATION_STAGES[activeStageIndex(phase) ?? 0]}</p>
                  <small>The room stays in view while the backend completes this stage.</small>
                </div>
              </div>
            )}
          </section>

          <ProgressRail phase={phase} />

          {mode === "custom" && (
            <section className="studio-section step-enter" data-testid="custom-fields">
              <div className="studio-section-heading">
                <div><span>Exact inventory</span><h2>Your chosen furniture</h2></div>
                <Link href="/selection">Edit selection <ArrowRight size={13} /></Link>
              </div>
              {!ready ? (
                <div className="studio-skeleton" aria-label="Loading selection" />
              ) : lines.length === 0 ? (
                <div className="studio-empty-note">
                  <p>No products are selected yet.</p>
                  <Link href="/shop">Choose furniture in the Shop</Link>
                </div>
              ) : (
                <div className="studio-product-strip">
                  {selectedProducts.map(({ product, line }) => (
                    <article key={product.id}>
                      <div className="studio-product-visual">
                        <ProductImage
                          src={furnitureImageUrl(product)}
                          alt={product.name}
                          className="studio-product-image"
                        />
                        <span>Qty {line.quantity}</span>
                      </div>
                      <div className="studio-product-copy">
                        <p>{displayStoreName(product.store_name)} · {humanize(product.category)}</p>
                        <h3>{product.name}</h3>
                        <div>
                          <span>{dimensionsLabel(product)}</span>
                          <strong>{formatMoney(product.price * line.quantity, product.currency)}</strong>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          )}

          {mode === "auto" && (
            <section className="studio-section studio-auto-direction step-enter" data-testid="auto-fields">
              <div className="studio-section-heading">
                <div><span>Creative direction</span><h2>Make it feel like you</h2></div>
                <WandSparkles size={21} />
              </div>
              <div className="auto-direction-status">
                <span className="auto-direction-orb" aria-hidden="true">
                  <Sparkles size={21} />
                </span>
                <div>
                  <strong>Shape the mood, not the floor plan.</strong>
                  <p>Give Auto Design a few signals. You will still review every product before generation.</p>
                </div>
                <span className={`auto-signal-count ${activeAutoSignals ? "has-signals" : ""}`}>
                  <i aria-hidden="true" />
                  {activeAutoSignals
                    ? `${activeAutoSignals} signal${activeAutoSignals === 1 ? "" : "s"} active`
                    : "Open brief"}
                </span>
              </div>

              <div className="auto-filter-grid">
                <article className="auto-filter-card">
                  <header>
                    <span aria-hidden="true"><WalletCards size={20} /></span>
                    <div>
                      <p>01 · Budget</p>
                      <h3>Set the spend rhythm</h3>
                    </div>
                  </header>
                  <label className="auto-money-field">
                    <span>JOD</span>
                    <input
                      aria-label="Budget"
                      type="number"
                      min="1"
                      placeholder="Open budget"
                      value={budget}
                      onChange={(event) => {
                        setBudget(event.target.value);
                        invalidateRoomRequest();
                      }}
                    />
                  </label>
                  <div className="auto-budget-presets" aria-label="Budget presets">
                    {budgetPresets.map((value) => (
                      <button
                        key={value}
                        type="button"
                        aria-label={`${value} JOD`}
                        aria-pressed={budget === String(value)}
                        onClick={() => {
                          setBudget(String(value));
                          invalidateRoomRequest();
                        }}
                      >
                        {value.toLocaleString()}
                      </button>
                    ))}
                  </div>
                </article>

                <article className="auto-filter-card">
                  <header>
                    <span aria-hidden="true"><Sparkles size={20} /></span>
                    <div>
                      <p>02 · Style</p>
                      <h3>Choose a design language</h3>
                    </div>
                  </header>
                  <div className="auto-style-options" role="group" aria-label="Preferred style">
                    <button
                      type="button"
                      aria-pressed={!style}
                      onClick={() => {
                        setStyle("");
                        invalidateRoomRequest();
                      }}
                    >
                      <strong>Surprise me</strong>
                      <small>Open direction</small>
                    </button>
                    {styles.map((value) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={style === value}
                        onClick={() => {
                          setStyle(value);
                          invalidateRoomRequest();
                        }}
                      >
                        <strong>{humanize(value)}</strong>
                        <small>{styleDescriptions[value]}</small>
                      </button>
                    ))}
                  </div>
                </article>

                <article className="auto-filter-card auto-palette-card">
                  <header>
                    <span aria-hidden="true"><Palette size={20} /></span>
                    <div>
                      <p>03 · Palette</p>
                      <h3>Paint the atmosphere</h3>
                    </div>
                  </header>
                  <label className="auto-palette-input">
                    <Palette size={16} aria-hidden="true" />
                    <input
                      aria-label="Preferred colors"
                      placeholder="Warm white, walnut, olive"
                      value={colors}
                      onChange={(event) => {
                        setColors(event.target.value);
                        invalidateRoomRequest();
                      }}
                    />
                  </label>
                  <div className="auto-palette-presets">
                    {palettePresets.map((preset) => (
                      <button
                        key={preset.label}
                        type="button"
                        aria-label={`Use ${preset.label} palette`}
                        aria-pressed={colors === preset.value}
                        onClick={() => {
                          setColors(preset.value);
                          invalidateRoomRequest();
                        }}
                      >
                        <span aria-hidden="true">
                          {preset.swatches.map((swatch) => (
                            <i key={swatch} style={{ backgroundColor: swatch }} />
                          ))}
                        </span>
                        <strong>{preset.label}</strong>
                      </button>
                    ))}
                  </div>
                </article>
              </div>
              <fieldset className="auto-category-fieldset">
                <legend>
                  Required categories
                  <span>Optional · lock the essentials into the brief</span>
                </legend>
                <div className="auto-category-toolbar">
                  <p>Tap any piece Auto Design must include.</p>
                  <div>
                    <strong>{requiredCategories.length}</strong>
                    <span>selected</span>
                    {requiredCategories.length > 0 && (
                      <button
                        type="button"
                        onClick={() => {
                          setRequiredCategories([]);
                          clearPreparedState();
                        }}
                      >
                        Clear all
                      </button>
                    )}
                  </div>
                </div>
                <div className="auto-category-grid">
                  {availableCategories.map((category) => {
                    const selected = requiredCategories.includes(category);
                    return (
                      <button
                        key={category}
                        type="button"
                        aria-label={`${selected ? "Remove" : "Require"} ${humanize(category)}`}
                        aria-pressed={selected}
                        className={selected ? "is-selected" : ""}
                        onClick={() => {
                          setRequiredCategories((current) =>
                            selected
                              ? current.filter((value) => value !== category)
                              : [...current, category],
                          );
                          clearPreparedState();
                        }}
                      >
                        <span className="auto-category-icon" aria-hidden="true">
                          <AutoCategoryIcon category={category} />
                        </span>
                        <span className="auto-category-copy">
                          <strong>{humanize(category)}</strong>
                          <small>{selected ? "Pinned to brief" : "Tap to require"}</small>
                        </span>
                        <span className="auto-category-state" aria-hidden="true">
                          {selected ? <Check size={13} /> : <Plus size={13} />}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </fieldset>
              {catalogLoaded && !catalogReadiness.ready && (
                <div className="studio-analysis-note mt-7">
                  <strong>Catalog coverage required</strong>
                  <p>
                    Auto Design is paused for this room type until the live catalog contains: {missingCatalogLabels.join(", ")}.
                    Custom mode remains available for exact products that already belong to this room type.
                  </p>
                </div>
              )}
            </section>
          )}

          {mode === "template" && (
            <section className="studio-section step-enter" data-testid="template-fields">
              <div className="studio-section-heading">
                <div><span>Curated product source</span><h2>Products you liked</h2></div>
                <Layers3 size={19} />
              </div>
              {!selectedTemplate ? (
                <div className="studio-empty-note">
                  <p>No template has been selected for this Studio session.</p>
                  <Link href="/templates">Browse templates and choose a product collection</Link>
                </div>
              ) : (
                <div className="studio-selected-template">
                  <div>
                    {/* Persisted manual selection from the template detail page. */}
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={mediaUrl(selectedTemplate.preview_image_url) || ""} alt={selectedTemplate.name} />
                    <span><Check size={12} /> Product source ID {selectedTemplate.id}</span>
                  </div>
                  <div>
                    <p>{humanize(selectedTemplate.style || "curated")} · {humanize(selectedTemplate.size_category || "adaptive")}</p>
                    <h3>{selectedTemplate.name}</h3>
                    <span>{selectedTemplate.matched_product_count} products are matched to the live catalog. Their source image does not prescribe the final layout.</span>
                    <Link href="/templates">Choose a different template <ArrowRight size={13} /></Link>
                  </div>
                </div>
              )}
            </section>
          )}

          {isPrepared && (
            <section className="studio-locked-panel step-enter">
              <div className="studio-section-heading">
                <div><span>Immutable review</span><h2>Inventory ready to render</h2></div>
                <LockKeyhole size={18} />
              </div>
              <StudioInventoryEditor
                items={preparedInventory}
                catalog={products}
                roomType={roomType}
                disabled={rendering || inventorySaving}
                dirty={inventoryDirty}
                saving={inventorySaving}
                onChange={(items) => {
                  setInventoryDraft(items);
                  setDraftSelectionId(lockedSelectionId);
                  setInventoryDirty(true);
                  setOrdinaryError("");
                }}
                onSave={() => { void handleSaveInventory(); }}
                onCancel={() => {
                  setInventoryDraft(lockedInventory);
                  setDraftSelectionId(lockedSelectionId);
                  setInventoryDirty(false);
                  setOrdinaryError("");
                }}
              />
              {densityWarning && activeComposition && (
                <div className="studio-capacity-warning" role="status">
                  <strong>
                    {activeComposition.density_classification === "high_density"
                      ? "Warning: dense selection, but Generate is allowed."
                      : "Warning: moderately dense selection; Generate is allowed."}
                  </strong>
                  <p>
                    Effective footprint {activeComposition.overlap_adjusted_effective_footprint_m2?.toFixed(2)} m²
                    {activeComposition.usable_floor_area_m2 != null
                      ? ` of ${activeComposition.usable_floor_area_m2.toFixed(2)} m² usable area.`
                      : "."}
                    {" "}Change quantities if you prefer more space, or continue and let GPT Image 2 attempt the best realistic arrangement.
                  </p>
                  {largestCapacityProducts.length > 0 && (
                    <p>
                      Largest space users: {largestCapacityProducts.map((item) =>
                        `${item.name || humanize(item.category)} (qty ${item.quantity}, ${item.effective_footprint_m2.toFixed(2)} m²)`,
                      ).join(" · ")}.
                    </p>
                  )}
                </div>
              )}
              <div className="studio-total"><span>Estimated total</span><strong>{formatMoney(preparedTotal, preparedCurrency)}</strong></div>
              <p className="studio-selection-id">Selection ID · {recommendation?.selection_id || templatePreparation?.selection_id}</p>
              {recommendation?.room_analysis?.analysis_warning && <div className="studio-analysis-note"><strong>Measured-room fallback</strong><p>{recommendation.room_analysis.analysis_warning}</p></div>}
            </section>
          )}
        </main>

        <aside className={`studio-controls ${controlsOpen ? "is-open" : ""}`}>
          <button type="button" className="studio-sheet-handle" aria-expanded={controlsOpen} onClick={() => setControlsOpen((value) => !value)}>
            <span>{controlsOpen ? "Close controls" : "Room controls"}</span>
            {controlsOpen ? <X size={16} /> : <ChevronDown size={16} />}
          </button>
          <div className="studio-controls-content">
            <div className="studio-controls-heading"><p className="eyebrow">Room context</p><span>{modes.find((item) => item.id === mode)?.number}</span></div>
            <div className="studio-readiness">
              <div><span>Setup readiness</span><strong>{readinessCount}/4</strong></div>
              <div aria-hidden="true"><i style={{ width: `${readinessCount * 25}%` }} /></div>
              <p>{isPrepared ? "Inventory locked and ready for generation." : "Complete the room details, then review the inventory."}</p>
            </div>
            <div className="studio-fields-stack">
              <label><span className="field-label">Room type *</span><select aria-label="Room type" className="select-field" value={roomType} disabled={busy || isPrepared} onChange={(event) => { setRoomType(event.target.value as RoomType); setRequiredCategories([]); invalidateRoomRequest(); }}>{roomTypes.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}</select></label>
              <div className="grid grid-cols-2 gap-3">
                <label><span className="field-label">Length (cm) *</span><input aria-label="Length (cm)" type="number" min="1" max="10000" className="text-field" value={length} disabled={busy || isPrepared} onChange={(event) => { setLength(event.target.value); invalidateRoomRequest(); }} /></label>
                <label><span className="field-label">Width (cm) *</span><input aria-label="Width (cm)" type="number" min="1" max="10000" className="text-field" value={width} disabled={busy || isPrepared} onChange={(event) => { setWidth(event.target.value); invalidateRoomRequest(); }} /></label>
              </div>
              <label><span className="field-label">Height (cm)</span><input aria-label="Height (cm)" type="number" min="1" max="10000" className="text-field" value={height} disabled={busy || isPrepared} onChange={(event) => { setHeight(event.target.value); invalidateRoomRequest(); }} /></label>
              {mode !== "template" && (
                <label><span className="field-label">Optional design note</span><textarea aria-label="Optional design note" maxLength={1000} className="textarea-field" placeholder={mode === "custom" ? "Placement, circulation, or atmosphere" : "What should the recommendation prioritize?"} value={note} disabled={busy || isPrepared} onChange={(event) => { setNote(event.target.value); clearPreparedState(); }} /></label>
              )}
            </div>

            <dl className="studio-summary">
              <div><dt>Mode</dt><dd>{modes.find((item) => item.id === mode)?.name}</dd></div>
              <div><dt>Room</dt><dd>{humanize(roomType)}</dd></div>
              <div><dt>Dimensions</dt><dd>{length && width ? `${length} × ${width}${height ? ` × ${height}` : ""} cm` : "Required"}</dd></div>
              {mode === "custom" && <div><dt>Exact pieces</dt><dd>{lines.reduce((sum, line) => sum + line.quantity, 0)}</dd></div>}
              {isPrepared && <div><dt>Locked pieces</dt><dd>{preparedInventory.reduce((sum, item) => sum + item.quantity, 0)}</dd></div>}
            </dl>

            {ordinaryError && (
              <div className="studio-error" role="alert">
                <strong>Studio request needs attention</strong>
                <p>{ordinaryError}</p>
                {recoveryAvailable && lockedSelectionId && (
                  <button type="button" className="secondary-button mt-4" disabled={rendering} onClick={() => { void handleRecoverRender(); }}>
                    {rendering ? "Checking audit…" : "Recover completed render"}
                  </button>
                )}
              </div>
            )}
            {!ordinaryError && attempted && blocker && <p className="studio-field-error">{blocker}</p>}

            {!isPrepared && (
              <button type="submit" className="secondary-button studio-prepare-button" disabled={Boolean(blocker) || busy}>
                {preparing ? "Working…" : preparationLabel}
                {!preparing && <ArrowRight size={14} />}
              </button>
            )}
            <button
              type="button"
              className="primary-button studio-generate-button"
              disabled={Boolean(generateReason)}
              aria-describedby="generate-availability"
              onClick={handleRender}
            >
              {rendering ? <><span className="button-loader" /> Designing your room</> : <>Generate design <ArrowUpRight size={15} /></>}
            </button>
            <p id="generate-availability" className="studio-availability">{generateReason || "Inventory reviewed. Generation is ready when you are."}</p>
            <div className="studio-safety"><LockKeyhole size={13} /><p>One explicit submission. Duplicate requests and automatic paid retries are disabled.</p></div>
            <Link href={mode === "custom" ? "/selection" : "/shop"} className="studio-back-link"><ArrowLeft size={13} /> {mode === "custom" ? "Back to selection" : "Explore furniture"}</Link>
          </div>
        </aside>
      </form>

      <div className="studio-mobile-action">
        <button type="button" className="studio-mobile-controls" aria-expanded={controlsOpen} onClick={() => setControlsOpen((value) => !value)}><SlidersHorizontal size={15} /> Controls</button>
        <button type="button" className="primary-button" disabled={Boolean(generateReason)} onClick={handleRender}>{rendering ? "Designing…" : "Generate"}</button>
      </div>
    </div>
  );
}
