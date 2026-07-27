export const TEMPLATE_SELECTION_KEY = "aqar-ai-selected-template-v1";

export function normalizeTemplateId(value: unknown): number | null {
  const parsed = typeof value === "string" ? Number(value) : value;
  return typeof parsed === "number" && Number.isInteger(parsed) && parsed > 0
    ? parsed
    : null;
}

export function saveSelectedTemplate(templateId: number) {
  const normalized = normalizeTemplateId(templateId);
  if (!normalized) throw new Error("A valid template ID is required.");
  window.localStorage.setItem(TEMPLATE_SELECTION_KEY, String(normalized));
}

export function loadSelectedTemplate() {
  return normalizeTemplateId(window.localStorage.getItem(TEMPLATE_SELECTION_KEY));
}

export function clearSelectedTemplate() {
  window.localStorage.removeItem(TEMPLATE_SELECTION_KEY);
}
