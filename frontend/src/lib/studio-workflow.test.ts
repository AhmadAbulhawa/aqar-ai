import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  activeStageIndex,
  completedStageCount,
  generationBlocker,
  preparationBlocker,
} from "./studio-workflow";

const completeState = {
  hasRoomImage: true,
  hasLength: true,
  hasWidth: true,
  selectedProductCount: 2,
  selectedTemplateId: null,
};

describe("Studio workflow guards", () => {
  it("requires exact Shop products only in Custom mode", () => {
    expect(preparationBlocker("custom", { ...completeState, selectedProductCount: 0 }))
      .toMatch(/Choose at least one product/);
    expect(preparationBlocker("auto", { ...completeState, selectedProductCount: 0 }))
      .toBeNull();
  });

  it("explains missing room media and dimensions", () => {
    expect(preparationBlocker("auto", { ...completeState, hasRoomImage: false }))
      .toMatch(/room image/);
    expect(preparationBlocker("auto", { ...completeState, hasWidth: false }))
      .toMatch(/length and width/);
  });

  it("requires one exact template selected from the gallery", () => {
    expect(preparationBlocker("template", completeState))
      .toMatch(/Choose one exact template/);
    expect(preparationBlocker("template", { ...completeState, selectedTemplateId: 7 }))
      .toBeNull();
  });

  it("enables generation only after inventory preparation", () => {
    expect(generationBlocker(false, false)).toMatch(/lock the inventory/);
    expect(generationBlocker(true, false)).toBeNull();
    expect(generationBlocker(true, true)).toMatch(/already in progress/);
  });

  it("advances stages only at request-backed milestones", () => {
    expect(activeStageIndex("uploading")).toBe(0);
    expect(completedStageCount("analyzing")).toBe(1);
    expect(completedStageCount("ready")).toBe(3);
    expect(activeStageIndex("designing")).toBe(3);
    expect(activeStageIndex("checking")).toBe(4);
  });

  it("defines a reduced-motion override for all motion", () => {
    const css = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("animation-duration: .001ms !important");
  });
});
