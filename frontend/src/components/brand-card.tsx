import Link from "next/link";
import { ArrowUpRight } from "lucide-react";
import type { BrandDefinition } from "@/lib/brands";
import { BrandMark } from "./brand-mark";

export function BrandCard({ brand, index }: { brand: BrandDefinition; index: number }) {
  return (
    <Link
      href={`/shop?brand=${brand.slug}`}
      className="brand-card group"
      style={{ "--brand-accent": brand.accent } as React.CSSProperties}
    >
      <div className="flex items-start justify-between">
        <span className="font-serif text-sm italic text-muted">0{index + 1}</span>
        <span className="brand-arrow"><ArrowUpRight size={19} /></span>
      </div>
      <div className="brand-card-identity">
        <div className="brand-logo-frame">
          <BrandMark brand={brand} />
        </div>
        <h3 className="brand-card-name">{brand.displayName}</h3>
        <p className="mt-3 max-w-sm text-sm leading-6 text-muted">{brand.description}</p>
      </div>
      <span className="eyebrow">Explore the collection</span>
    </Link>
  );
}
