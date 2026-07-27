"use client";

import { ArrowUpRight } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { mediaUrl } from "@/lib/api";
import { humanize } from "@/lib/format";
import type { DesignTemplate } from "@/types/api";

export function TemplateCard({ template, index }: { template: DesignTemplate; index: number }) {
  const preview = template.preview_image_url
    || (template.preview_image_filename
      ? `/uploads/design_templates/${template.preview_image_filename}`
      : null);
  return (
    <motion.article
      className="template-card"
      initial={{ opacity: 0, y: 30 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-6%" }}
      transition={{ duration: 0.65, delay: Math.min(index * 0.055, 0.28) }}
    >
      <Link href={`/templates/${template.id}`} aria-label={`View ${template.name}`}>
        <div className="template-card-media">
          {preview ? (
            // Real backend template preview.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={mediaUrl(preview) || ""} alt={template.name} />
          ) : <span className="image-fallback">Preview unavailable</span>}
          <span className="template-index">{String(index + 1).padStart(2, "0")}</span>
          <span className="template-open"><ArrowUpRight size={17} /></span>
        </div>
        <div className="template-card-copy">
          <p className="template-card-meta">{humanize(template.room_type)} · {template.style ? humanize(template.style) : "Curated"} · {humanize(template.size_category || "adaptive")}</p>
          <h2>{template.name}</h2>
          <span className="template-card-count">{template.matched_product_count} matched products</span>
          <p className="template-card-description">{template.description || "A curated collection of real products ready to match to your room."}</p>
          <em className="template-card-source">Source · {template.source_name || "Curated collection"}</em>
        </div>
      </Link>
    </motion.article>
  );
}
