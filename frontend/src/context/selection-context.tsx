"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { SelectionLine } from "@/types/api";

export const SELECTION_STORAGE_KEY = "aqar-ai-room-selection-v1";

type SelectionContextValue = {
  lines: SelectionLine[];
  ready: boolean;
  itemCount: number;
  add: (id: number) => void;
  setQuantity: (id: number, quantity: number) => void;
  remove: (id: number) => void;
  clear: () => void;
};

const SelectionContext = createContext<SelectionContextValue | null>(null);

export function normalizeSelection(value: unknown): SelectionLine[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (line): line is SelectionLine =>
        typeof line === "object" &&
        line !== null &&
        Number.isInteger((line as SelectionLine).id) &&
        (line as SelectionLine).id > 0 &&
        Number.isInteger((line as SelectionLine).quantity) &&
        (line as SelectionLine).quantity > 0,
    )
    .map((line) => ({ id: line.id, quantity: Math.min(line.quantity, 10) }));
}

export function SelectionProvider({ children }: { children: React.ReactNode }) {
  const [lines, setLines] = useState<SelectionLine[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        setLines(normalizeSelection(JSON.parse(localStorage.getItem(SELECTION_STORAGE_KEY) || "[]")));
      } catch {
        setLines([]);
      }
      setReady(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (ready) localStorage.setItem(SELECTION_STORAGE_KEY, JSON.stringify(lines));
  }, [lines, ready]);

  const add = useCallback((id: number) => {
    setLines((current) => {
      const existing = current.find((line) => line.id === id);
      if (existing) {
        return current.map((line) =>
          line.id === id ? { ...line, quantity: Math.min(line.quantity + 1, 10) } : line,
        );
      }
      return [...current, { id, quantity: 1 }];
    });
  }, []);

  const setQuantity = useCallback((id: number, quantity: number) => {
    if (quantity < 1) {
      setLines((current) => current.filter((line) => line.id !== id));
      return;
    }
    setLines((current) =>
      current.map((line) =>
        line.id === id ? { ...line, quantity: Math.min(quantity, 10) } : line,
      ),
    );
  }, []);

  const remove = useCallback((id: number) => {
    setLines((current) => current.filter((line) => line.id !== id));
  }, []);

  const clear = useCallback(() => setLines([]), []);

  const value = useMemo(
    () => ({
      lines,
      ready,
      itemCount: lines.reduce((sum, line) => sum + line.quantity, 0),
      add,
      setQuantity,
      remove,
      clear,
    }),
    [lines, ready, add, setQuantity, remove, clear],
  );

  return <SelectionContext.Provider value={value}>{children}</SelectionContext.Provider>;
}

export function useSelection() {
  const value = useContext(SelectionContext);
  if (!value) throw new Error("useSelection must be used inside SelectionProvider");
  return value;
}
