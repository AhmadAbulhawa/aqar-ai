"use client";

import { ArrowDown } from "lucide-react";
import { useEffect, useState } from "react";
import { Reveal } from "@/components/cinematic-motion";
import { TemplateCard } from "@/components/template-card";
import { apiErrorMessage, getDesignTemplates, mediaUrl } from "@/lib/api";
import type { DesignTemplate } from "@/types/api";

export default function TemplatesPage() {
  const [templates, setTemplates] = useState<DesignTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getDesignTemplates()
      .then(setTemplates)
      .catch((requestError) => setError(
        apiErrorMessage(requestError, "Templates could not be loaded."),
      ))
      .finally(() => setLoading(false));
  }, []);

  const feature = templates[0];
  const featureImage = feature?.preview_image_url
    || (feature?.preview_image_filename
      ? `/uploads/design_templates/${feature.preview_image_filename}`
      : null);

  return (
    <div className="templates-page">
      <section className="templates-hero">
        {featureImage && (
          <div className="templates-hero-image">
            {/* Real local template photography. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={mediaUrl(featureImage) || ""} alt="" />
          </div>
        )}
        <div className="templates-hero-shade" />
        <div className="page-shell templates-hero-content">
          <p className="cinematic-kicker">Curated product collections</p>
          <h1>Choose the room<br />you want to live in.</h1>
          <div className="templates-hero-foot">
            <p>Browse real design concepts, inspect their mapped catalog inventory, then manually apply one exact template to your room.</p>
            <a href="#template-collection">Explore the collection <ArrowDown size={14} /></a>
          </div>
        </div>
      </section>

      <section id="template-collection" className="page-shell templates-collection">
        <Reveal className="templates-intro">
          <p className="cinematic-kicker">The collection · {templates.length || "—"}</p>
          <h2>No algorithm chooses for you.</h2>
          <p>Every template remains a deliberate visual decision. Open one to study its design concept, mapped furniture, unresolved sources, and estimated total.</p>
        </Reveal>

        {error && <div className="connection-error" role="alert"><strong>Template connection failed</strong><p>{error}</p></div>}
        {loading ? (
          <div className="template-gallery" aria-label="Loading templates">
            {Array.from({ length: 6 }).map((_, index) => <div key={index} className="template-card-skeleton" />)}
          </div>
        ) : (
          <div className="template-gallery">
            {templates.map((template, index) => <TemplateCard key={template.id} template={template} index={index} />)}
          </div>
        )}
      </section>
    </div>
  );
}
