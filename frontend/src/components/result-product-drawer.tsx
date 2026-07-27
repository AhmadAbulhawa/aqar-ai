"use client";

import { ArrowUpRight, ChevronLeft, ChevronRight, X } from "lucide-react";
import { motion } from "motion/react";
import { useMemo } from "react";
import { ProductImage } from "@/components/product-image";
import { mediaUrl } from "@/lib/api";
import { displayStoreName } from "@/lib/brands";
import { dimensionsLabel, formatMoney, humanize } from "@/lib/format";
import {
  PRODUCT_ORIGINS,
  PRODUCT_ORIGIN_ORDER,
  productOrigin,
  productOriginKey,
} from "@/lib/product-origin";
import type { Furniture, SelectedFurniture } from "@/types/api";

export function ResultProductDrawer({
  open,
  onToggle,
  items,
  catalog,
  total,
  currency,
}: {
  open: boolean;
  onToggle: () => void;
  items: SelectedFurniture[];
  catalog: Furniture[];
  total: number;
  currency: string;
}) {
  const originGroups = useMemo(
    () => PRODUCT_ORIGIN_ORDER.map((key) => ({
      key,
      meta: PRODUCT_ORIGINS[key],
      items: items.filter((item) => productOriginKey(item) === key),
    })).filter((group) => group.items.length > 0),
    [items],
  );
  return (
    <>
      <button className="result-drawer-toggle" onClick={onToggle} aria-expanded={open}>
        {open ? <ChevronRight size={17} /> : <ChevronLeft size={17} />}
        <span>{open ? "Hide" : "View"} products</span>
      </button>
      <motion.aside
        className={`result-drawer ${open ? "is-open" : ""}`}
        aria-label="Immutable selected inventory"
        initial={false}
        animate={{ x: open ? "0%" : "100%" }}
        transition={{ type: "spring", stiffness: 260, damping: 30, mass: .88 }}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-paper px-5 py-5 md:px-7">
          <div><p className="eyebrow">Immutable inventory</p><h2 className="mt-1 font-serif text-2xl">Products in this design</h2></div>
          <button className="icon-button" onClick={onToggle} aria-label="Hide product drawer"><X size={18} /></button>
        </div>
        <div className="origin-groups px-5 py-5 md:px-7">
          {originGroups.map((group) => <section key={group.key} className={`origin-group origin-group-${group.key}`} aria-labelledby={`origin-${group.key}`}>
            <header className="origin-group-heading">
              <div><p className="eyebrow">Product source</p><h3 id={`origin-${group.key}`}>{group.meta.label}</h3></div>
              <span>{group.items.length}</span>
            </header>
            <p className="origin-group-explanation">{group.meta.explanation}</p>
            <div className="origin-product-list">
          {group.items.map((item) => {
            const current = catalog.find((product) => product.id === item.id);
            const origin = productOrigin(item);
            return (
              <article key={item.id} className={`origin-product-row ${origin.borderClass}`}>
                <div className="grid grid-cols-[82px_1fr] gap-4">
                  <ProductImage src={mediaUrl(item.image_url)} alt={item.name} className="aspect-square w-full bg-stone object-contain p-2" />
                  <div className="min-w-0">
                    <p className="text-[9px] font-bold uppercase tracking-[.13em] text-muted">{displayStoreName(item.store_name)} · {humanize(item.category)}</p>
                    <h3 className="mt-1 font-serif text-xl leading-tight">{item.name}</h3>
                    <span className={`origin-badge ${origin.badgeClass}`} title={origin.explanation}>{origin.label}</span>
                    <p className="mt-2 text-xs text-muted">{current ? dimensionsLabel(current) : "Dimensions unavailable"}</p>
                  </div>
                </div>
                <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-line pt-4 text-sm">
                  <div><dt className="detail-label">Quantity</dt><dd className="mt-1">{item.quantity}</dd></div>
                  <div><dt className="detail-label">Unit price</dt><dd className="mt-1">{formatMoney(item.price, item.currency)}</dd></div>
                  <div><dt className="detail-label">Subtotal</dt><dd className="mt-1 font-semibold">{formatMoney(item.line_total, item.currency)}</dd></div>
                </dl>
                <a href={item.product_url} target="_blank" rel="noreferrer" className="mt-4 inline-flex items-center gap-2 text-[10px] font-bold uppercase tracking-[.12em] underline underline-offset-4">Buy from original store <ArrowUpRight size={13} /></a>
              </article>
            );
          })}
            </div>
          </section>)}
          {!originGroups.length && <p className="py-12 text-center text-sm text-muted">No products are recorded for this design.</p>}
        </div>
        <div className="sticky bottom-0 border-t border-line bg-ink px-6 py-6 text-white">
          <div className="flex items-center justify-between"><span className="font-serif text-xl">Estimated total</span><strong>{formatMoney(total, currency)}</strong></div>
          <p className="mt-2 text-[10px] leading-5 text-white/45">Exact immutable quantities. Current catalog dimensions are matched by backend product ID.</p>
        </div>
      </motion.aside>
    </>
  );
}
