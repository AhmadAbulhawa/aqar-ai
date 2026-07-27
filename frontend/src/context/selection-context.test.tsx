import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import {
  normalizeSelection,
  SelectionProvider,
  SELECTION_STORAGE_KEY,
  useSelection,
} from "./selection-context";

function SelectionProbe() {
  const { lines, ready, add } = useSelection();
  return <button onClick={() => add(30)}>{ready ? JSON.stringify(lines) : "loading"}</button>;
}

describe("selection persistence", () => {
  beforeEach(() => localStorage.clear());

  it("keeps only valid IDs and quantities and enforces the backend quantity cap", () => {
    expect(normalizeSelection([
      { id: 1, quantity: 2 },
      { id: 7, quantity: 99 },
      { id: -2, quantity: 1 },
      { id: 3, quantity: 0 },
      { id: "4", quantity: 1 },
    ])).toEqual([{ id: 1, quantity: 2 }, { id: 7, quantity: 10 }]);
  });

  it("hydrates IDs and quantities from localStorage and writes quantity changes back", async () => {
    localStorage.setItem(SELECTION_STORAGE_KEY, JSON.stringify([{ id: 30, quantity: 2 }]));
    render(<SelectionProvider><SelectionProbe /></SelectionProvider>);
    expect(await screen.findByRole("button", { name: '[{"id":30,"quantity":2}]' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem(SELECTION_STORAGE_KEY) || "[]")).toEqual([{ id: 30, quantity: 3 }]);
    });
  });
});
