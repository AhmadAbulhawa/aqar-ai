import Link from "next/link";
import { ArrowUpRight, Sparkles } from "lucide-react";

export function SiteFooter() {
  return (
    <footer className="bg-ink text-[#f7f4ee]">
      <div className="page-shell grid gap-12 py-16 md:grid-cols-[1.5fr_1fr_1fr] md:py-20">
        <div>
          <div className="logo-lockup text-white">
            <span className="logo-glyph border-white/30"><Sparkles size={15} /></span>
            <span>AQAR</span><span className="logo-ai text-sand">AI</span>
          </div>
          <p className="mt-6 max-w-sm text-sm leading-7 text-white/58">
            See the room. Select every detail. Make a confident decision before you buy.
          </p>
        </div>
        <div>
          <p className="eyebrow text-white/40">Explore</p>
          <div className="mt-5 grid gap-3 text-sm text-white/75">
            <Link href="/shop">Furniture</Link>
            <Link href="/selection">Room selection</Link>
            <Link href="/studio">Design studio</Link>
          </div>
        </div>
        <div>
          <p className="eyebrow text-white/40">Create your space</p>
          <Link href="/studio" className="mt-5 inline-flex items-center gap-2 border-b border-white/30 pb-2 text-lg">
            Begin a design <ArrowUpRight size={17} />
          </Link>
        </div>
      </div>
      <div className="page-shell flex flex-col gap-2 border-t border-white/10 py-6 text-xs text-white/35 sm:flex-row sm:justify-between">
        <span>© 2026 Aqar AI</span>
        <span>Furniture visualization for considered homes.</span>
      </div>
    </footer>
  );
}
