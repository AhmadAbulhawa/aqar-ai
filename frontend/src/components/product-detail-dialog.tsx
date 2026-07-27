"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { ArrowUpRight, Minus, Plus, X } from "lucide-react";
import { useState } from "react";
import { useSelection } from "@/context/selection-context";
import { furnitureImageUrl } from "@/lib/api";
import { displayStoreName } from "@/lib/brands";
import { isDesignSelectableProduct } from "@/lib/catalog-eligibility";
import { dimensionsLabel, formatMoney, humanize } from "@/lib/format";
import type { Furniture } from "@/types/api";
import { ProductImage } from "./product-image";

export function ProductDetailDialog({
  product,
  onClose,
}: {
  product: Furniture | null;
  onClose: () => void;
}) {
  const [quantity, setQuantity] = useState(1);
  const { add, setQuantity: setSelectedQuantity, lines } = useSelection();
  const selectable = product ? isDesignSelectableProduct(product) : false;

  function addQuantity() {
    const existing = lines.find((line) => line.id === product?.id);
    if (!product || !selectable) return;
    if (!existing) add(product.id);
    setSelectedQuantity(product.id, (existing?.quantity ?? 0) + quantity);
    onClose();
  }

  return (
    <Dialog.Root open={Boolean(product)} onOpenChange={(value) => !value && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="product-dialog">
          {product && (
            <div className="grid min-h-full md:grid-cols-[1.05fr_.95fr]">
              <div className="bg-stone p-5 md:p-8">
                <ProductImage src={furnitureImageUrl(product)} alt={product.name} className="h-full min-h-[300px] w-full object-contain" />
              </div>
              <div className="flex flex-col p-7 md:p-10">
                <div className="flex items-center justify-between">
                  <span className="eyebrow">{displayStoreName(product.store_name)}</span>
                  <Dialog.Close className="icon-button"><X size={19} /></Dialog.Close>
                </div>
                <Dialog.Title className="mt-8 font-serif text-4xl leading-[1.04]">{product.name}</Dialog.Title>
                <Dialog.Description className="mt-4 text-sm leading-7 text-muted">
                  {product.style ? `${humanize(product.style)} ` : ""}{humanize(product.category)}
                  {product.color ? ` in ${product.color}` : ""}
                  {product.material ? `, crafted from ${product.material}` : ""}.
                </Dialog.Description>
                <dl className="mt-8 grid grid-cols-2 gap-x-5 gap-y-6 border-y border-line py-6 text-sm">
                  <div><dt className="detail-label">Dimensions</dt><dd className="mt-1">{dimensionsLabel(product)}</dd></div>
                  <div><dt className="detail-label">Availability</dt><dd className="mt-1">{product.availability ? "Available" : "Unavailable"}</dd></div>
                  <div><dt className="detail-label">Category</dt><dd className="mt-1">{humanize(product.category)}</dd></div>
                  <div><dt className="detail-label">Price</dt><dd className="mt-1 font-semibold">{formatMoney(product.price, product.currency)}</dd></div>
                </dl>
                <div className="mt-auto pt-8">
                  <div className="mb-4 flex items-center justify-between">
                    <span className="text-sm text-muted">Quantity</span>
                    <div className="quantity-control">
                      <button onClick={() => setQuantity((value) => Math.max(1, value - 1))} aria-label="Decrease quantity"><Minus size={14} /></button>
                      <span>{quantity}</span>
                      <button onClick={() => setQuantity((value) => Math.min(10, value + 1))} aria-label="Increase quantity"><Plus size={14} /></button>
                    </div>
                  </div>
                  <button className="primary-button w-full" onClick={addQuantity} disabled={!selectable}>
                    {selectable
                      ? `Add to room selection · ${formatMoney(product.price * quantity, product.currency)}`
                      : "Available in Shop only"}
                  </button>
                  <a href={product.product_url} target="_blank" rel="noreferrer" className="mt-4 flex items-center justify-center gap-2 text-xs font-semibold uppercase tracking-[0.12em] text-muted hover:text-ink">
                    View at original store <ArrowUpRight size={14} />
                  </a>
                </div>
              </div>
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
