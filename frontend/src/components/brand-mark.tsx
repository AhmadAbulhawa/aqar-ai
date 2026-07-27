import type { BrandDefinition } from "@/lib/brands";

export function BrandMark({
  brand,
  className = "",
}: {
  brand: BrandDefinition;
  className?: string;
}) {
  if (brand.logoAvailable) {
    // This path activates only after the approved local SVG is supplied.
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={brand.logoPath} alt={`${brand.displayName} logo`} className={className} />;
  }

  return (
    <span className={`brand-wordmark ${className}`} aria-label={brand.displayName}>
      {brand.displayName}
    </span>
  );
}
