"use client";

import { ArrowUpRight, Check, Plus } from "lucide-react";
import { useState } from "react";
import { useSelection } from "@/context/selection-context";
import { furnitureImageUrl } from "@/lib/api";
import { displayStoreName } from "@/lib/brands";
import { isDesignSelectableProduct } from "@/lib/catalog-eligibility";
import { dimensionsLabel, formatMoney, humanize } from "@/lib/format";
import type { Furniture } from "@/types/api";
import { ProductImage } from "./product-image";

export function ProductCard({
  product,
  onOpen,
  priority = false,
}: {
  product: Furniture;
  onOpen?: (product: Furniture) => void;
  priority?: boolean;
}) {
  const { add } = useSelection();
  const [added, setAdded] = useState(false);
  const selectable = isDesignSelectableProduct(product);

  function handleAdd(event: React.MouseEvent) {
    event.stopPropagation();
    if (!selectable) return;
    add(product.id);
    setAdded(true);
    window.setTimeout(() => setAdded(false), 1200);
  }

  return (
    <article className="product-card group" data-priority={priority || undefined}>
      <button className="product-image-wrap" onClick={() => onOpen?.(product)} aria-label={`View ${product.name}`}>
        <span className="product-card-number">#{String(product.id).padStart(3, "0")}</span>
        <ProductImage src={furnitureImageUrl(product)} alt={product.name} className="product-image" />
        <span className="product-category">{humanize(product.category)}</span>
        <span className="product-view-action">View details <ArrowUpRight size={12} /></span>
      </button>
      <div className="product-card-body">
        <div className="product-store-row">
          <p>{displayStoreName(product.store_name)}</p>
          <span className={selectable ? "is-available" : ""}>
            <i /> {product.catalog_status === "shop_only"
              ? "Shop only"
              : product.availability ? "Available" : "Unavailable"}
          </span>
        </div>
        <button onClick={() => onOpen?.(product)} className="product-card-title">
          {product.name}
        </button>
        <div className="product-card-footer">
          <div>
            <p>{dimensionsLabel(product)}</p>
            <strong>{formatMoney(product.price, product.currency)}</strong>
          </div>
          <button onClick={handleAdd} disabled={!selectable} className={`add-product ${added ? "is-added" : ""}`} aria-label={`Add ${product.name} to room selection`}>
            {added ? <Check size={17} /> : <Plus size={18} />}
          </button>
        </div>
      </div>
    </article>
  );
}
