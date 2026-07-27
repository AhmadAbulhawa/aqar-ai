"use client";

import Link from "next/link";
import { Menu, ShoppingBag, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useSelection } from "@/context/selection-context";

const links = [
  { href: "/shop", label: "Furniture" },
  { href: "/templates", label: "Templates" },
  { href: "/#brands", label: "Brands" },
  { href: "/#how-it-works", label: "How it works" },
];

export function SiteHeader() {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const { itemCount } = useSelection();
  const pathname = usePathname();
  const isHome = pathname === "/";
  const overlaysHero = isHome && !scrolled;

  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 36);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);

  return (
    <header className={`site-header ${isHome ? "is-home" : ""} ${overlaysHero ? "is-hero-overlay" : "is-scrolled"}`}>
      <div className="page-shell flex h-[76px] items-center justify-between gap-6">
        <Link href="/" className="logo-lockup" aria-label="Aqar AI home">
          <span className="logo-glyph" aria-hidden="true">A</span>
          <span>AQAR</span>
          <span className="logo-ai">AI</span>
        </Link>

        <nav className="hidden items-center gap-9 md:flex" aria-label="Main navigation">
          {links.map((link) => (
            <Link key={link.href} href={link.href} className="nav-link">
              {link.label}
            </Link>
          ))}
        </nav>

        <div className="flex items-center gap-3">
          <Link href="/studio" className="hidden rounded-full bg-ink px-5 py-3 text-xs font-semibold uppercase tracking-[0.12em] text-white sm:inline-flex">
            Design your room
          </Link>
          <Link href="/selection" className="selection-button" aria-label={`${itemCount} items in room selection`}>
            <ShoppingBag size={19} strokeWidth={1.6} />
            <span className="selection-count">{itemCount}</span>
          </Link>
          <button className="icon-button md:hidden" onClick={() => setOpen((value) => !value)} aria-expanded={open} aria-label="Toggle navigation">
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>

      {open && (
        <nav className="mobile-nav md:hidden" aria-label="Mobile navigation">
          {links.map((link) => (
            <Link key={link.href} href={link.href} onClick={() => setOpen(false)}>
              {link.label}
            </Link>
          ))}
          <Link href="/studio" onClick={() => setOpen(false)}>Design your room</Link>
        </nav>
      )}
    </header>
  );
}
