"use client";

import { Minus, PackagePlus, Plus, Search, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { ProductImage } from "@/components/product-image";
import { furnitureImageUrl, mediaUrl } from "@/lib/api";
import { displayStoreName } from "@/lib/brands";
import { isDesignSelectableProduct } from "@/lib/catalog-eligibility";
import { formatMoney, humanize } from "@/lib/format";
import {
  PRODUCT_ORIGINS,
  PRODUCT_ORIGIN_ORDER,
  productOrigin,
  productOriginKey,
} from "@/lib/product-origin";
import type { Furniture, RoomType, SelectedFurniture } from "@/types/api";

type Props = {
  items: SelectedFurniture[];
  catalog: Furniture[];
  roomType: RoomType;
  disabled: boolean;
  dirty: boolean;
  saving: boolean;
  onChange: (items: SelectedFurniture[]) => void;
  onSave: () => void;
  onCancel: () => void;
};

const MAX_QUANTITY = 10;
const MAX_PRODUCTS = 15;

function catalogProductToSelection(product: Furniture): SelectedFurniture {
  return {
    id: product.id,
    name: product.name,
    category: product.category,
    style: product.style,
    color: product.color,
    material: product.material,
    width_cm: product.width_cm,
    depth_cm: product.depth_cm,
    height_cm: product.height_cm,
    price: product.price,
    currency: product.currency,
    image_filename: product.image_filename,
    image_url: product.image_filename
      ? `/uploads/furniture/${product.image_filename}`
      : null,
    product_url: product.product_url,
    store_name: product.store_name,
    selection_role: "user_selected",
    selection_origin: "user",
    product_origin: "user_selected",
    added_for_completion: false,
    was_included: true,
    inclusion_reason: "Added by you during Studio inventory review.",
    included_quantity: 1,
    completion_quantity: 0,
    quantity_edited_by_user: true,
    quantity: 1,
    line_total: product.price,
  };
}

export function StudioInventoryEditor({
  items,
  catalog,
  roomType,
  disabled,
  dirty,
  saving,
  onChange,
  onSave,
  onCancel,
}: Props) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState("");
  const selectedIds = useMemo(() => new Set(items.map((item) => item.id)), [items]);
  const availableProducts = useMemo(() => {
    const query = search.trim().toLowerCase();
    return catalog.filter((product) => {
      if (
        !isDesignSelectableProduct(product) ||
        !product.room_types.includes(roomType) ||
        selectedIds.has(product.id)
      ) return false;
      if (!query) return true;
      return [
        product.name,
        product.category,
        product.style,
        product.color,
        product.material,
        displayStoreName(product.store_name),
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }, [catalog, roomType, search, selectedIds]);
  const groups = PRODUCT_ORIGIN_ORDER.map((key) => ({
    key,
    meta: PRODUCT_ORIGINS[key],
    items: items.filter((item) => productOriginKey(item) === key),
  })).filter((group) => group.items.length > 0);

  function setQuantity(item: SelectedFurniture, quantity: number) {
    if (quantity < 1 || quantity > MAX_QUANTITY) return;
    onChange(items.map((candidate) => candidate.id === item.id
      ? {
          ...candidate,
          quantity,
          line_total: Number((candidate.price * quantity).toFixed(2)),
          quantity_edited_by_user: true,
        }
      : candidate));
  }

  function addProduct(product: Furniture) {
    if (items.length >= MAX_PRODUCTS) return;
    onChange([...items, catalogProductToSelection(product)]);
    setPickerOpen(false);
    setSearch("");
  }

  return (
    <>
      <div className="studio-inventory-toolbar">
        <div>
          <strong>Edit before generation</strong>
          <span>Quantities and products are saved as a new locked inventory.</span>
        </div>
        <button
          type="button"
          className="studio-add-product"
          disabled={disabled || items.length >= MAX_PRODUCTS}
          onClick={() => setPickerOpen(true)}
        >
          <PackagePlus size={15} /> Add product
        </button>
      </div>

      <div className="studio-inventory-list">
        {groups.map((group) => (
          <section key={group.key} className={`studio-origin-group origin-group-${group.key}`}>
            <header className="studio-origin-heading">
              <div><span>Product source</span><h3>{group.meta.label}</h3></div>
              <strong>{group.items.length}</strong>
            </header>
            <p className="studio-origin-explanation">{group.meta.explanation}</p>
            {group.items.map((item) => {
              const origin = productOrigin(item);
              return (
                <article key={item.id} className={origin.borderClass}>
                  <ProductImage
                    src={item.image_url
                      ? mediaUrl(item.image_url)
                      : item.image_filename
                        ? mediaUrl(`/uploads/furniture/${item.image_filename}`)
                        : null}
                    alt={item.name}
                    className="h-16 w-16 bg-stone object-contain p-2"
                  />
                  <div className="min-w-0">
                    <p>{displayStoreName(item.store_name)}</p>
                    <h4>{item.name}</h4>
                    <span>{humanize(item.category)}</span>
                    <em className={origin.badgeClass}>{origin.label}</em>
                  </div>
                  <div className="studio-inventory-item-actions">
                    <strong>{formatMoney(item.line_total, item.currency)}</strong>
                    <div className="studio-quantity-control" aria-label={`Quantity for ${item.name}`}>
                      <button
                        type="button"
                        aria-label={`Decrease quantity for ${item.name}`}
                        disabled={disabled || item.quantity <= 1}
                        onClick={() => setQuantity(item, item.quantity - 1)}
                      ><Minus size={12} /></button>
                      <span aria-live="polite">{item.quantity}</span>
                      <button
                        type="button"
                        aria-label={`Increase quantity for ${item.name}`}
                        disabled={disabled || item.quantity >= MAX_QUANTITY}
                        onClick={() => setQuantity(item, item.quantity + 1)}
                      ><Plus size={12} /></button>
                    </div>
                    <button
                      type="button"
                      className="studio-remove-product"
                      aria-label={`Remove ${item.name}`}
                      disabled={disabled}
                      onClick={() => onChange(items.filter((candidate) => candidate.id !== item.id))}
                    ><Trash2 size={13} /> Remove</button>
                  </div>
                </article>
              );
            })}
          </section>
        ))}
      </div>

      {dirty && (
        <div className="studio-inventory-savebar" role="status">
          <p>{items.length
            ? "Save these changes before generating the room."
            : "Keep at least one product in the inventory."}</p>
          <div>
            <button type="button" disabled={saving} onClick={onCancel}>Discard</button>
            <button
              type="button"
              className="primary-button"
              disabled={saving || items.length === 0}
              onClick={onSave}
            >{saving ? "Saving…" : "Save inventory changes"}</button>
          </div>
        </div>
      )}

      {pickerOpen && (
        <div className="studio-product-picker-backdrop" role="presentation" onMouseDown={() => setPickerOpen(false)}>
          <section
            className="studio-product-picker"
            role="dialog"
            aria-modal="true"
            aria-labelledby="studio-product-picker-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div><span>Live catalog</span><h3 id="studio-product-picker-title">Add a product</h3></div>
              <button type="button" aria-label="Close product catalog" onClick={() => setPickerOpen(false)}><X size={17} /></button>
            </header>
            <label className="studio-product-picker-search">
              <Search size={16} />
              <input
                autoFocus
                aria-label="Search products to add"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search name, category, color, or store"
              />
            </label>
            <p className="studio-picker-count">{availableProducts.length} products available for {humanize(roomType)}</p>
            <div className="studio-picker-grid">
              {availableProducts.map((product) => (
                <article key={product.id}>
                  <ProductImage
                    src={furnitureImageUrl(product)}
                    alt={product.name}
                    className="studio-picker-image"
                  />
                  <div>
                    <p>{displayStoreName(product.store_name)} · {humanize(product.category)}</p>
                    <h4>{product.name}</h4>
                    <strong>{formatMoney(product.price, product.currency)}</strong>
                  </div>
                  <button type="button" aria-label={`Add ${product.name}`} onClick={() => addProduct(product)}>
                    <Plus size={14} /> Add
                  </button>
                </article>
              ))}
              {availableProducts.length === 0 && (
                <p className="studio-picker-empty">No matching unselected products are available for this room.</p>
              )}
            </div>
          </section>
        </div>
      )}
    </>
  );
}
