"use client";

import { ImageIcon } from "lucide-react";
import { useState } from "react";

export function ProductImage({
  src,
  alt,
  className = "",
}: {
  src: string | null;
  alt: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return (
      <div className={`image-fallback ${className}`} role="img" aria-label={`${alt} image unavailable`}>
        <ImageIcon size={24} strokeWidth={1.25} />
      </div>
    );
  }
  return (
    // Product media is served by the configured FastAPI host at runtime.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      className={className}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}
