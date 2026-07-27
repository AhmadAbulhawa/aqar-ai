"use client";

import { ArrowLeft, ArrowRight, ArrowUpRight, Check, CircleAlert } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ProductImage } from "@/components/product-image";
import { apiErrorMessage, getDesignTemplate, mediaUrl } from "@/lib/api";
import { displayStoreName } from "@/lib/brands";
import { dimensionsLabel, formatMoney, humanize } from "@/lib/format";
import { saveSelectedTemplate } from "@/lib/template-selection";
import type { DesignTemplateDetail } from "@/types/api";

export default function TemplateDetailPage() {
  const params = useParams<{ templateId: string }>();
  const router = useRouter();
  const templateId = Number(params.templateId);
  const invalidTemplateId = !Number.isInteger(templateId) || templateId < 1;
  const [template, setTemplate] = useState<DesignTemplateDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (invalidTemplateId) return;
    getDesignTemplate(templateId)
      .then(setTemplate)
      .catch((requestError) => setError(
        apiErrorMessage(requestError, "The template could not be loaded."),
      ));
  }, [invalidTemplateId, templateId]);

  function applyTemplate() {
    if (!template) return;
    saveSelectedTemplate(template.id);
    router.push("/studio?mode=template");
  }

  if (invalidTemplateId || error) return <section className="page-shell template-detail-error"><div className="connection-error" role="alert"><strong>Template unavailable</strong><p>{invalidTemplateId ? "The template ID is invalid." : error}</p></div><Link href="/templates"><ArrowLeft size={14} /> Back to templates</Link></section>;
  if (!template) return <div className="template-detail-loading"><span>Loading design concept</span></div>;

  return (
    <article className="template-detail-page">
      <section className="template-detail-stage">
        <motion.div className="template-detail-image" initial={{ clipPath: "inset(0 0 100% 0)" }} animate={{ clipPath: "inset(0 0 0% 0)" }} transition={{ duration: 1, ease: [0.22, 1, 0.36, 1] }}>
          {/* Real local backend template image. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={mediaUrl(template.preview_image_url) || ""} alt={template.name} />
        </motion.div>
        <div className="template-detail-overlay" />
        <div className="page-shell template-detail-title">
          <Link href="/templates"><ArrowLeft size={14} /> All templates</Link>
          <p className="cinematic-kicker">{humanize(template.room_type)} · {humanize(template.style || "curated")}</p>
          <h1>{template.name}</h1>
        </div>
      </section>

      <section className="page-shell template-concept-grid">
        <div>
          <p className="cinematic-kicker">Product inspiration</p>
          <h2>A collection you love,<br />ready for your room.</h2>
        </div>
        <div className="template-concept-copy">
          <p>{template.description}</p>
          <dl>
            <div><dt>Room</dt><dd>{humanize(template.room_type)}</dd></div>
            <div><dt>Style</dt><dd>{humanize(template.style || "curated")}</dd></div>
            <div><dt>Size</dt><dd>{humanize(template.size_category || "adaptive")}</dd></div>
            <div><dt>Source</dt><dd>{template.source_name || "Curated source"}</dd></div>
          </dl>
          {template.source_url && <a href={template.source_url} target="_blank" rel="noreferrer">View original design story <ArrowUpRight size={14} /></a>}
        </div>
      </section>

      <section className="template-products-section">
        <div className="page-shell">
          <div className="template-products-heading">
            <div><p className="cinematic-kicker">Mapped inventory</p><h2>Products represented<br />in this room.</h2></div>
            <div><strong>{template.matched_product_count}</strong><span>matched catalog products</span></div>
          </div>
          <div className="template-product-rows">
            {template.represented_products.map((slot) => {
              const product = slot.matched_product;
              return (
                <article key={slot.slot_id} className={product ? "is-matched" : "is-unresolved"}>
                  <span className="template-slot-number">{String(slot.slot_id).padStart(2, "0")}</span>
                  {product ? <ProductImage src={mediaUrl(product.image_url)} alt={product.name} className="template-slot-image" /> : <div className="template-slot-image image-fallback"><CircleAlert size={18} /></div>}
                  <div className="template-slot-main">
                    <p>{humanize(slot.category)} · Quantity {slot.quantity}</p>
                    <h3>{product?.name || `Unresolved ${humanize(slot.category)} slot`}</h3>
                    <span>{product ? `${displayStoreName(product.store_name)} · ${dimensionsLabel(product)}` : "No active exact catalog product is linked"}</span>
                  </div>
                  <div className="template-slot-price">
                    {product ? <><strong>{formatMoney(product.price * slot.quantity, product.currency)}</strong><a href={product.product_url} target="_blank" rel="noreferrer">Purchase <ArrowUpRight size={12} /></a></> : <span>Will be completed from the database where possible</span>}
                  </div>
                </article>
              );
            })}
          </div>

          {template.unresolved_slots.length > 0 && (
            <div className="template-unresolved-note"><CircleAlert size={17} /><p><strong>{template.unresolved_slots.length} source items remain unresolved.</strong> Only active matched products become part of the selection. The backend may add real catalog essentials when the room needs them.</p></div>
          )}
        </div>
      </section>

      <section className="template-apply-band">
        <div className="page-shell">
          <div><p className="cinematic-kicker">Your selected collection</p><h2>Design freely with these products.</h2><p>Estimated matched product total · {formatMoney(template.estimated_product_total, template.currency || "JOD")}</p></div>
          <button type="button" onClick={applyTemplate}>Use These Products in My Room <ArrowRight size={16} /></button>
          <span><Check size={13} /> The selected template ID is saved before Studio opens.</span>
        </div>
      </section>
    </article>
  );
}
