import { beforeEach, describe, expect, it } from "vitest";
import {
  clearSelectedTemplate,
  loadSelectedTemplate,
  normalizeTemplateId,
  saveSelectedTemplate,
  TEMPLATE_SELECTION_KEY,
} from "./template-selection";

describe("manual template persistence", () => {
  beforeEach(() => window.localStorage.clear());

  it("persists and reloads only the exact manually selected ID", () => {
    saveSelectedTemplate(7);
    expect(window.localStorage.getItem(TEMPLATE_SELECTION_KEY)).toBe("7");
    expect(loadSelectedTemplate()).toBe(7);
  });

  it("never normalizes missing or invalid storage to a first template", () => {
    expect(normalizeTemplateId(null)).toBeNull();
    expect(normalizeTemplateId("0")).toBeNull();
    window.localStorage.setItem(TEMPLATE_SELECTION_KEY, "not-a-template");
    expect(loadSelectedTemplate()).toBeNull();
  });

  it("clears the previous manual selection explicitly", () => {
    saveSelectedTemplate(9);
    clearSelectedTemplate();
    expect(loadSelectedTemplate()).toBeNull();
  });
});
