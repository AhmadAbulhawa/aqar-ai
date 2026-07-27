"use client";

import Link from "next/link";
import { ArrowRight, Minus, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { ProductImage } from "@/components/product-image";
import { useSelection } from "@/context/selection-context";
import { displayStoreName } from "@/lib/brands";
import { furnitureImageUrl, getFurniture } from "@/lib/api";
import { formatMoney } from "@/lib/format";
import type { Furniture } from "@/types/api";

export default function SelectionPage() {
  const { lines, ready, setQuantity, remove } = useSelection();
  const [products, setProducts] = useState<Furniture[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!ready || !lines.length) return;
    getFurniture()
      .then(setProducts)
      .catch(() => setError("Product details could not be loaded from the backend."));
  }, [ready, lines.length]);

  const detailedLines = useMemo(
    () => lines
      .map((line) => ({ line, product: products.find((product) => product.id === line.id) }))
      .filter((value): value is { line: typeof value.line; product: Furniture } => Boolean(value.product)),
    [lines, products],
  );
  const total = detailedLines.reduce((sum, { line, product }) => sum + product.price * line.quantity, 0);
  const currency = detailedLines[0]?.product.currency || "JOD";

  return (
    <>
      <section className="border-b border-line bg-[#ebe7df] py-14 md:py-20">
        <div className="page-shell grid gap-7 md:grid-cols-[1fr_.65fr] md:items-end">
          <div><p className="eyebrow">Room selection</p><h1 className="section-title mt-5">The exact pieces for your room.</h1></div>
          <p className="section-copy md:ml-auto">This is not a checkout cart. Quantities here become locked product requirements when you prepare a room design.</p>
        </div>
      </section>

      <section className="page-shell py-10 md:py-16">
        {!ready ? <p className="text-sm text-muted">Loading your selection…</p> : !lines.length ? (
          <div className="grid min-h-[440px] place-items-center border border-line bg-white/25 p-8 text-center">
            <div><p className="eyebrow">Nothing selected yet</p><h2 className="mt-4 font-serif text-4xl md:text-5xl">Begin with one piece you love.</h2><p className="mx-auto mt-4 max-w-md text-sm leading-7 text-muted">Your selection is kept on this device so you can return and refine it.</p><Link href="/shop" className="primary-button mt-8 inline-flex items-center gap-4">Explore furniture <ArrowRight size={16} /></Link></div>
          </div>
        ) : (
          <div className="grid gap-10 lg:grid-cols-[1fr_360px] xl:gap-16">
            <div>
              {error && <div className="form-error mb-6">{error}</div>}
              <div className="hidden grid-cols-[92px_1fr_130px_110px_42px] gap-5 border-b border-line pb-3 text-[10px] font-bold uppercase tracking-[.14em] text-muted sm:grid">
                <span>Piece</span><span>Product</span><span>Quantity</span><span>Subtotal</span><span />
              </div>
              <div className="divide-y divide-line">
                {detailedLines.map(({ line, product }) => (
                  <article key={product.id} className="grid grid-cols-[88px_1fr] gap-4 py-6 sm:grid-cols-[92px_1fr_130px_110px_42px] sm:items-center sm:gap-5">
                    <ProductImage src={furnitureImageUrl(product)} alt={product.name} className="aspect-square w-full bg-stone object-contain p-2" />
                    <div className="min-w-0"><p className="text-[10px] font-bold uppercase tracking-[.14em] text-muted">{displayStoreName(product.store_name)}</p><h2 className="mt-1 font-serif text-xl leading-tight">{product.name}</h2><p className="mt-2 text-xs text-muted">{formatMoney(product.price, product.currency)} each</p></div>
                    <div className="col-start-2 sm:col-start-auto">
                      <div className="quantity-control w-fit"><button aria-label={`Decrease ${product.name} quantity`} onClick={() => setQuantity(product.id, line.quantity - 1)}><Minus size={13} /></button><span>{line.quantity}</span><button aria-label={`Increase ${product.name} quantity`} onClick={() => setQuantity(product.id, line.quantity + 1)}><Plus size={13} /></button></div>
                    </div>
                    <p className="col-start-2 text-sm font-semibold sm:col-start-auto">{formatMoney(product.price * line.quantity, product.currency)}</p>
                    <button className="col-start-2 text-left text-muted hover:text-[#983f32] sm:col-start-auto" onClick={() => remove(product.id)} aria-label={`Remove ${product.name}`}><Trash2 size={17} /></button>
                  </article>
                ))}
              </div>
              <Link href="/shop" className="mt-8 inline-flex border-b border-ink pb-2 text-[11px] font-bold uppercase tracking-[.13em]">Continue selecting furniture</Link>
            </div>

            <aside className="h-fit bg-ink p-7 text-white lg:sticky lg:top-28 md:p-9">
              <p className="eyebrow text-white/45">Selection summary</p>
              <h2 className="mt-5 font-serif text-3xl">Ready to see it together?</h2>
              <dl className="mt-8 space-y-4 border-y border-white/15 py-6 text-sm">
                <div className="flex justify-between text-white/60"><dt>Unique products</dt><dd>{lines.length}</dd></div>
                <div className="flex justify-between text-white/60"><dt>Total pieces</dt><dd>{lines.reduce((sum, line) => sum + line.quantity, 0)}</dd></div>
                <div className="flex justify-between text-lg"><dt className="font-serif">Estimated total</dt><dd>{formatMoney(total, currency)}</dd></div>
              </dl>
              <p className="mt-5 text-xs leading-6 text-white/45">Estimated from current backend catalog prices. Purchases are completed with each original retailer.</p>
              <Link href="/studio" className="mt-7 flex min-h-13 items-center justify-center gap-4 rounded-full bg-white px-5 text-[11px] font-bold uppercase tracking-[.13em] text-ink">Open Design Studio <ArrowRight size={15} /></Link>
            </aside>
          </div>
        )}
      </section>
    </>
  );
}
