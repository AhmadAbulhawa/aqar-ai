"use client";

import { ArrowDown, ArrowRight, ArrowUpRight, Grid2X2, ScanLine, ShoppingBag } from "lucide-react";
import { motion, useScroll, useTransform } from "motion/react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { BeforeAfterSlider } from "@/components/before-after-slider";
import { BrandCard } from "@/components/brand-card";
import { Reveal } from "@/components/cinematic-motion";
import { TemplateCard } from "@/components/template-card";
import { BRANDS } from "@/lib/brands";
import { getDesignTemplates, mediaUrl } from "@/lib/api";
import type { DesignTemplate } from "@/types/api";

const HERO_IMAGE = "/home/aqar-ai-room-transformation.png";
const TEMPLATE_FALLBACK_IMAGE = "/uploads/design_templates/ikea_a-japandi-living-room-with-colours-and-materials-inspired-by-nature_https-www-ikea-com-jo-en-rooms.jpg";

function templateImage(template?: DesignTemplate) {
  if (!template) return TEMPLATE_FALLBACK_IMAGE;
  return template.preview_image_url
    || (template.preview_image_filename
      ? `/uploads/design_templates/${template.preview_image_filename}`
      : TEMPLATE_FALLBACK_IMAGE);
}

export default function HomePage() {
  const heroRef = useRef<HTMLElement>(null);
  const [templates, setTemplates] = useState<DesignTemplate[]>([]);
  const { scrollYProgress } = useScroll({ target: heroRef, offset: ["start start", "end start"] });
  const heroY = useTransform(scrollYProgress, [0, 1], ["0%", "13%"]);
  const titleY = useTransform(scrollYProgress, [0, 1], ["0%", "-12%"]);

  useEffect(() => {
    getDesignTemplates().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  const editorialTemplate = templates[1] || templates[0];

  return (
    <div className="aqar-landing">
      <section ref={heroRef} className="cinematic-home-hero">
        <motion.div
          className="cinematic-home-media"
          style={{ y: heroY }}
          initial={{ clipPath: "inset(0 0 100% 0)" }}
          animate={{ clipPath: "inset(0 0 0% 0)" }}
          transition={{ duration: 1.05, ease: [0.22, 1, 0.36, 1] }}
        >
          {/* Approved local homepage artwork supplied by the project owner. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={HERO_IMAGE} alt="An empty room transitioning into a warm, fully furnished living room" />
        </motion.div>
        <div className="cinematic-home-scrim" />
        <div className="cinematic-home-grain" aria-hidden="true" />
        <div className="cinematic-home-orbit" aria-hidden="true"><span /></div>
        <div className="cinematic-home-rail" aria-hidden="true">
          <span>Empty room</span><i /><span>Designed room</span>
        </div>
        <motion.div className="page-shell cinematic-home-content" style={{ y: titleY }}>
          <div className="cinematic-home-meta">
            <p className="cinematic-kicker">Spatial commerce for real rooms</p>
            <span>Interior intelligence / 2026</span>
          </div>
          <h1>
            <span>See your room</span>
            <span><em>furnished</em> before</span>
            <span>you buy.</span>
          </h1>
          <div className="cinematic-home-bottom">
            <div className="cinematic-hero-actions">
              <Link href="/studio">Design Your Room <ArrowRight size={15} /></Link>
              <Link href="/templates">Explore Templates <ArrowUpRight size={15} /></Link>
            </div>
            <p>From an empty room to a complete interior, grounded in real products you can buy.</p>
          </div>
        </motion.div>
        <div className="cinematic-home-index" aria-hidden="true"><span>01</span><small>Immersive design</small></div>
        <a className="cinematic-scroll" href="#home-templates" aria-label="Scroll to discover"><ArrowDown size={15} /></a>
      </section>

      <section id="home-templates" className="home-template-spotlight" aria-labelledby="home-template-title">
        <div className="page-shell">
          <Reveal className="home-template-spotlight-heading">
            <div>
              <p className="cinematic-kicker">Ready-made room directions</p>
              <h2 id="home-template-title">Start with a complete<br />design you already love.</h2>
            </div>
            <div>
              <p>Choose one exact template yourself, inspect its real catalog products, then adapt it to your measured room.</p>
              <Link href="/templates">Explore all templates <ArrowRight size={14} /></Link>
            </div>
          </Reveal>
          {templates.length ? (
            <div className="home-template-spotlight-grid">
              {templates.slice(0, 3).map((template, index) => (
                <TemplateCard key={template.id} template={template} index={index} />
              ))}
            </div>
          ) : (
            <div className="home-template-spotlight-grid" aria-label="Loading templates">
              {Array.from({ length: 3 }).map((_, index) => <div key={index} className="template-card-skeleton" />)}
            </div>
          )}
        </div>
      </section>

      <section id="brands" className="home-brands">
        <div className="page-shell">
          <Reveal className="home-section-heading">
            <div><p className="cinematic-kicker">Original retail partners</p><h2>Shop by brand,<br />not approximation.</h2></div>
            <p>Choose IKEA, LEADERS, SHAHWAN or MIDAS to open that retailer&apos;s real backend catalog. All furniture browsing starts here.</p>
          </Reveal>
          <div className="home-brand-grid">
            {BRANDS.map((brand, index) => <BrandCard key={brand.slug} brand={brand} index={index} />)}
          </div>
        </div>
      </section>

      <section id="home-story" className="home-manifesto">
        <div className="page-shell home-manifesto-grid">
          <Reveal className="home-manifesto-title">
            <p className="cinematic-kicker">A room is not a cart</p>
            <h2>Design the feeling.<br /><em>Know every piece.</em></h2>
          </Reveal>
          <Reveal className="home-manifesto-copy" delay={0.08}>
            <span>01 / The Aqar approach</span>
            <p>Start with the architecture you already have. Add furniture you can actually purchase. Review the exact inventory before a design is created.</p>
            <Link href="/shop">Enter the furniture collection <ArrowRight size={14} /></Link>
          </Reveal>
        </div>
        <span className="home-manifesto-signature" aria-hidden="true">space / story / certainty</span>
      </section>

      <section className="home-modes">
        <div className="page-shell">
          <div className="home-modes-heading">
            <Reveal><p className="cinematic-kicker">Three considered paths</p><h2>Choose how your room begins.</h2></Reveal>
            <p>Each workflow keeps preparation separate from generation. Nothing renders until you explicitly ask.</p>
          </div>
          <div className="home-mode-grid">
            {[
              { number: "01", icon: ShoppingBag, title: "Your selection", copy: "Build with the exact products and quantities already saved from the Shop.", href: "/shop", action: "Curate furniture" },
              { number: "02", icon: ScanLine, title: "Auto direction", copy: "Describe the atmosphere and review a database-backed recommendation first.", href: "/studio", action: "Open Auto Studio" },
              { number: "03", icon: Grid2X2, title: "A complete idea", copy: "Manually choose one editorial template and adapt that exact composition.", href: "/templates", action: "Browse templates" },
            ].map((item, index) => (
              <motion.article key={item.number} initial={{ opacity: 0, y: 32 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: "-8%" }} transition={{ duration: 0.65, delay: index * 0.08 }}>
                <div><span>{item.number}</span><item.icon size={21} strokeWidth={1.25} /></div>
                <h3>{item.title}</h3>
                <p>{item.copy}</p>
                <Link href={item.href}>{item.action} <ArrowRight size={13} /></Link>
              </motion.article>
            ))}
          </div>
        </div>
      </section>

      <section className="home-comparison">
        <div className="page-shell">
          <Reveal className="home-comparison-heading">
            <div><p className="cinematic-kicker">From existing to envisioned</p><h2>Keep the room.<br />Change the possibility.</h2></div>
            <p>Move the divider to compare a locally stored room photograph with its generated project preview. The architecture remains legible; the furnishing becomes tangible.</p>
          </Reveal>
          <Reveal className="home-comparison-frame" delay={0.08}><BeforeAfterSlider /></Reveal>
        </div>
      </section>

      <section className="home-template-story">
        <div className="home-template-visual">
          {/* Real backend template preview, never a stock placeholder. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={mediaUrl(templateImage(editorialTemplate)) || ""} alt={editorialTemplate?.name || "Curated room template"} />
        </div>
        <Reveal className="home-template-copy">
          <p className="cinematic-kicker">The template collection</p>
          <span>{editorialTemplate ? `Concept ${String(editorialTemplate.id).padStart(2, "0")}` : "Curated concepts"}</span>
          <h2>{editorialTemplate?.name || "A complete room, chosen by you."}</h2>
          <p>{editorialTemplate?.description || "Study the concept and its mapped products, then choose the exact room direction you want to adapt."}</p>
          <Link href={editorialTemplate ? `/templates/${editorialTemplate.id}` : "/templates"}>Explore this composition <ArrowUpRight size={15} /></Link>
        </Reveal>
        <span className="home-template-story-label" aria-hidden="true">Curated direction / exact products</span>
      </section>

      <section id="how-it-works" className="home-closing">
        <span className="home-closing-wordmark" aria-hidden="true">AQAR</span>
        <div className="page-shell home-closing-grid">
          <p className="cinematic-kicker">A measured path to a room</p>
          <h2>Choose.<br />Measure.<br /><em>Visualize.</em></h2>
          <div>
            <p>Real catalog data enters the Studio. Your room dimensions ground the request. The backend locks the final product inventory before any optional generation.</p>
            <Link href="/studio">Begin your room <ArrowRight size={15} /></Link>
          </div>
        </div>
      </section>
    </div>
  );
}
