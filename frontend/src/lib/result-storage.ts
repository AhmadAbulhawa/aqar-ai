import type {
  FailedComplianceDetail,
  RecommendationResponse,
  RenderResponse,
  RoomUploadResponse,
  TemplatePreparationResponse,
} from "@/types/api";

export const RESULT_KEY = "aqar-ai-design-result-v1";
export const FAILURE_KEY = "aqar-ai-compliance-failure-v1";
export const PENDING_KEY = "aqar-ai-pending-design-v1";

export type PendingDesign = {
  room: RoomUploadResponse;
  recommendation: RecommendationResponse | TemplatePreparationResponse;
};

export function saveJson(key: string, value: unknown) {
  window.sessionStorage.setItem(key, JSON.stringify(value));
}

export function loadJson<T>(key: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function savePendingDesign(value: PendingDesign) {
  saveJson(PENDING_KEY, value);
}

export function saveDesignResult(value: RenderResponse) {
  saveJson(RESULT_KEY, value);
  window.sessionStorage.removeItem(FAILURE_KEY);
}

export function saveComplianceFailure(value: FailedComplianceDetail) {
  saveJson(FAILURE_KEY, value);
  window.sessionStorage.removeItem(RESULT_KEY);
}
