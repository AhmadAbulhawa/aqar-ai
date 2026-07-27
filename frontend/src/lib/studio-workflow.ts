import type { DesignMode } from "@/types/api";

export type StudioPreparationState = {
  hasRoomImage: boolean;
  hasLength: boolean;
  hasWidth: boolean;
  selectedProductCount: number;
  selectedTemplateId: number | null;
};

export type StudioPhase =
  | "idle"
  | "uploading"
  | "analyzing"
  | "preparing"
  | "ready"
  | "designing"
  | "checking";

export const GENERATION_STAGES = [
  "Uploading your room",
  "Analyzing the space",
  "Preparing products",
  "Designing your room",
  "Checking the result",
] as const;

export function preparationBlocker(
  mode: DesignMode,
  state: StudioPreparationState,
): string | null {
  if (mode === "custom" && state.selectedProductCount === 0) {
    return "Choose at least one product in the Shop for Custom Furniture mode.";
  }
  if (!state.hasRoomImage) return "Add a room image to continue.";
  if (!state.hasLength || !state.hasWidth) {
    return "Enter the measured room length and width.";
  }
  if (mode === "template" && !state.selectedTemplateId) {
    return "Choose one exact template from the Templates collection first.";
  }
  return null;
}

export function generationBlocker(
  prepared: boolean,
  busy: boolean,
): string | null {
  if (busy) return "A Studio request is already in progress.";
  if (!prepared) return "Review and lock the inventory before generation.";
  return null;
}

export function completedStageCount(phase: StudioPhase) {
  return {
    idle: 0,
    uploading: 0,
    analyzing: 1,
    preparing: 2,
    ready: 3,
    designing: 3,
    checking: 4,
  }[phase];
}

export function activeStageIndex(phase: StudioPhase): number | null {
  return {
    idle: null,
    uploading: 0,
    analyzing: 1,
    preparing: 2,
    ready: null,
    designing: 3,
    checking: 4,
  }[phase];
}
