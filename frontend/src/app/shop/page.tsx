"use client";

import { ArrowRight, Search, SlidersHorizontal, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { ProductCard } from "@/components/product-card";
import { ProductDetailDialog } from "@/components/product-detail-dialog";
import { BrandMark } from "@/components/brand-mark";
import { BRAND_BY_SLUG, BRANDS } from "@/lib/brands";
import { isBrowsableProduct } from "@/lib/catalog-eligibility";
import { getFurniture } from "@/lib/api";
import { humanize } from "@/lib/format";
import type { Furniture } from "@/types/api";

const preferredCategoryOrder = [
  "sofa", "armchair", "coffee_table", "side_table", "tv_stand", "television",
  "sideboard", "rug", "floor_lamp", "dining_chair", "dining_table", "office_chair",
  "desk", "bookshelf", "bed", "wardrobe", "nightstand", "dresser", "mirror",
  "wall_shelf", "storage_cabinet", "wall_art", "table_lamp", "wall_lamp",
  "ceiling_light", "chandelier", "pendant_light", "ceiling_spotlight",
  "refrigerator", "washing_machine", "dryer", "dishwasher", "oven", "cooktop",
  "freestanding_cooker", "range_hood", "microwave", "shop_only",
];
const styles = ["modern", "classic", "minimal", "luxury"];
const PRODUCT_BATCH_SIZE = 48;

type Filters = {
  category: string;
  style: string;
  color: string;
  minPrice: string;
  maxPrice: string;
  availability: string;
};

const emptyFilters: Filters = {
  category: "", style: "", color: "", minPrice: "", maxPrice: "", availability: "",
};

function ShopContent() {
  const searchParams = useSearchParams();
  const requestedBrand = searchParams.get("brand") || "";
  const [brandSlug, setBrandSlug] = useState(BRAND_BY_SLUG[requestedBrand] ? requestedBrand : "");
  const [catalog, setCatalog] = useState<Furniture[]>([]);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [selected, setSelected] = useState<Furniture | null>(null);
  const [visibleCount, setVisibleCount] = useState(PRODUCT_BATCH_SIZE);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let alive = true;
    async function loadCatalog() {
      setLoading(true);
      setError("");
      try {
        const items = await getFurniture();
        if (alive) setCatalog(items);
      } catch {
        if (alive) setError("The furniture catalog is unavailable. Confirm the FastAPI backend is running.");
      } finally {
        if (alive) setLoading(false);
      }
    }
    void loadCatalog();
    return () => { alive = false; };
  }, [reloadToken]);

  const activeBrand = brandSlug ? BRAND_BY_SLUG[brandSlug] : null;
  const colors = useMemo(
    () => [...new Set(catalog.map((item) => item.color).filter((value): value is string => Boolean(value)))].sort((a, b) => a.localeCompare(b)),
    [catalog],
  );
  const categories = useMemo(() => {
    const discovered = new Set(
      catalog.filter(isBrowsableProduct).map((item) => item.category),
    );
    return [...discovered].sort((left, right) => {
      const leftIndex = preferredCategoryOrder.indexOf(left);
      const rightIndex = preferredCategoryOrder.indexOf(right);
      if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right);
      if (leftIndex === -1) return 1;
      if (rightIndex === -1) return -1;
      return leftIndex - rightIndex;
    });
  }, [catalog]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return catalog.filter((item) => {
      const brandMatch = !activeBrand || item.store_name === activeBrand.storeName;
      const searchMatch = !needle || [item.name, item.category, item.color, item.material, item.store_name]
        .some((value) => value?.toLowerCase().includes(needle));
      const categoryMatch = !filters.category || item.category === filters.category;
      const styleMatch = !filters.style || item.style === filters.style;
      const colorMatch = !filters.color || item.color === filters.color;
      const minPriceMatch = !filters.minPrice || item.price >= Number(filters.minPrice);
      const maxPriceMatch = !filters.maxPrice || item.price <= Number(filters.maxPrice);
      const availabilityMatch = filters.availability === ""
        || item.availability === (filters.availability === "true");
      return brandMatch
        && searchMatch
        && categoryMatch
        && styleMatch
        && colorMatch
        && minPriceMatch
        && maxPriceMatch
        && availabilityMatch
        && isBrowsableProduct(item);
    });
  }, [catalog, activeBrand, filters, query]);
  const displayedProducts = visible.slice(0, visibleCount);

  const activeFilterCount = Object.entries(filters).filter(([key, value]) => value && !(key === "availability" && value === "true")).length + (brandSlug ? 1 : 0);
  const activeCatalogCount = catalog.filter(
    (item) =>
      isBrowsableProduct(item) &&
      (!activeBrand || item.store_name === activeBrand.storeName),
  ).length;

  function setFilter(name: keyof Filters, value: string) {
    setFilters((current) => ({ ...current, [name]: value }));
    setVisibleCount(PRODUCT_BATCH_SIZE);
  }

  function setBrand(value: string) {
    setBrandSlug(value);
    setVisibleCount(PRODUCT_BATCH_SIZE);
  }

  function clearFilters() {
    setFilters(emptyFilters);
    setBrandSlug("");
    setVisibleCount(PRODUCT_BATCH_SIZE);
  }

  const filterPanel = (
    <div className="shop-filter-stack">
      <div>
        <label className="field-label" htmlFor="brand-filter">Brand</label>
        <select id="brand-filter" className="select-field" value={brandSlug} onChange={(event) => setBrand(event.target.value)}>
          <option value="">All brands</option>
          {BRANDS.map((brand) => <option key={brand.slug} value={brand.slug}>{brand.displayName}</option>)}
        </select>
      </div>
      <div>
        <label className="field-label" htmlFor="category-filter">Category</label>
        <select id="category-filter" className="select-field" value={filters.category} onChange={(event) => setFilter("category", event.target.value)}>
          <option value="">All categories</option>
          {categories.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
        </select>
      </div>
      <div>
        <label className="field-label" htmlFor="style-filter">Style</label>
        <select id="style-filter" className="select-field" value={filters.style} onChange={(event) => setFilter("style", event.target.value)}>
          <option value="">All styles</option>
          {styles.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
        </select>
      </div>
      <div>
        <label className="field-label" htmlFor="color-filter">Color</label>
        <select id="color-filter" className="select-field" value={filters.color} onChange={(event) => setFilter("color", event.target.value)}>
          <option value="">All colors</option>
          {colors.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
        </select>
      </div>
      <div>
        <span className="field-label">Price range (JOD)</span>
        <div className="grid grid-cols-2 gap-2">
          <input aria-label="Minimum price" type="number" min="0" placeholder="Min" className="text-field" value={filters.minPrice} onChange={(event) => setFilter("minPrice", event.target.value)} />
          <input aria-label="Maximum price" type="number" min="0" placeholder="Max" className="text-field" value={filters.maxPrice} onChange={(event) => setFilter("maxPrice", event.target.value)} />
        </div>
      </div>
      <div>
        <label className="field-label" htmlFor="availability-filter">Availability</label>
        <select id="availability-filter" className="select-field" value={filters.availability} onChange={(event) => setFilter("availability", event.target.value)}>
          <option value="">All</option><option value="true">Available</option><option value="false">Unavailable</option>
        </select>
      </div>
      <button className="w-full border-b border-ink pb-2 text-left text-[11px] font-bold uppercase tracking-[.13em]" onClick={clearFilters}>
        Clear all filters
      </button>
    </div>
  );

  return (
    <>
      <section className="shop-masthead border-b border-line bg-[#ebe7df] py-14 md:py-20">
        <div className="page-shell grid gap-8 lg:grid-cols-[1fr_.7fr] lg:items-end">
          <div>
            <p className="eyebrow">Real furniture · exact inventory</p>
            <h1 className="section-title mt-5">{activeBrand ? `${activeBrand.displayName}, selected for real rooms.` : "Furniture with a real place in your room."}</h1>
            <p className="shop-masthead-copy">Browse connected retail inventory and inspect the real source details. Products with complete placement data can continue into the Studio; incomplete items remain clearly marked Shop-only.</p>
          </div>
          <div className="shop-masthead-stat">
            <span>{activeBrand ? activeBrand.displayName : "Connected catalog"}</span>
            <strong>{catalog.length ? activeCatalogCount : "—"}</strong>
            <p>catalog pieces available to review</p>
          </div>
        </div>
      </section>

      <section className="page-shell shop-brand-section" aria-labelledby="shop-brand-title">
        <div className="shop-brand-heading">
          <div><p className="eyebrow">Choose the source</p><h2 id="shop-brand-title">Shop the collection by brand.</h2></div>
          <p>Each brand view is filtered by the exact database store value.</p>
        </div>
        <nav className="shop-brand-rail" aria-label="Furniture brands">
          <button type="button" className={!brandSlug ? "is-active" : ""} aria-pressed={!brandSlug} onClick={() => setBrand("")}>
            <span>All retail partners</span>
            <strong>{catalog.filter(isBrowsableProduct).length || "—"} pieces</strong>
            <i><ArrowRight size={14} /></i>
          </button>
          {BRANDS.map((brand) => {
            const count = catalog.filter((item) => isBrowsableProduct(item) && item.store_name === brand.storeName).length;
            return (
              <button key={brand.slug} type="button" className={brandSlug === brand.slug ? "is-active" : ""} aria-pressed={brandSlug === brand.slug} onClick={() => setBrand(brand.slug)}>
                <span className="shop-brand-mark"><BrandMark brand={brand} /></span>
                <strong>{count || "—"} pieces</strong>
                <i><ArrowRight size={14} /></i>
              </button>
            );
          })}
        </nav>
      </section>

      <section className="page-shell shop-collection py-10 md:py-14">
        <div className="shop-search-row">
          <div className="shop-search-label"><span>Catalog</span><strong>Find your piece</strong></div>
          <div className="relative flex-1">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-muted" size={18} />
            <input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setVisibleCount(PRODUCT_BATCH_SIZE); }} placeholder="Search product, material, color or store" className="shop-search-input" />
          </div>
          <button className="secondary-button flex items-center gap-2 lg:hidden" onClick={() => setFiltersOpen(true)}><SlidersHorizontal size={16} /> Filters {activeFilterCount ? `(${activeFilterCount})` : ""}</button>
        </div>

        <div className="shop-layout mt-9 grid gap-10 lg:grid-cols-[230px_1fr] xl:grid-cols-[250px_1fr]">
          <aside className="shop-filter-panel hidden lg:block"><div className="sticky top-28">{filterPanel}</div></aside>
          <div>
            <div className="shop-results-heading">
              <div><span>{loading ? "Loading catalog…" : `${visible.length} piece${visible.length === 1 ? "" : "s"}`}</span><strong>{activeBrand ? `${activeBrand.displayName} only` : "All furniture"}</strong></div>
              {activeFilterCount > 0 && <button type="button" onClick={clearFilters}>Reset {activeFilterCount} active filter{activeFilterCount === 1 ? "" : "s"}</button>}
            </div>
            {error ? (
              <div className="form-error">
                <p>{error}</p>
                <button type="button" className="secondary-button mt-4" onClick={() => setReloadToken((value) => value + 1)}>Retry catalog</button>
              </div>
            ) : loading ? (
              <div className="grid grid-cols-2 gap-5 md:grid-cols-3"><div className="aspect-square animate-pulse bg-stone" /><div className="aspect-square animate-pulse bg-stone" /><div className="hidden aspect-square animate-pulse bg-stone md:block" /></div>
            ) : visible.length ? (
              <>
                <div className="shop-product-grid">
                  {displayedProducts.map((product) => <ProductCard key={product.id} product={product} onOpen={setSelected} />)}
                </div>
                {displayedProducts.length < visible.length && (
                  <div className="mt-14 flex justify-center">
                    <button type="button" className="secondary-button" onClick={() => setVisibleCount((value) => value + PRODUCT_BATCH_SIZE)}>
                      Load more ({visible.length - displayedProducts.length} remaining)
                    </button>
                  </div>
                )}
              </>
            ) : (
              <div className="border border-line p-12 text-center"><p className="font-serif text-3xl">No pieces match.</p><p className="mt-3 text-sm text-muted">Adjust a filter or try a broader search.</p></div>
            )}
          </div>
        </div>
      </section>

      {filtersOpen && (
        <div className="fixed inset-0 z-[70] bg-black/40 lg:hidden" onClick={() => setFiltersOpen(false)}>
          <aside className="absolute bottom-0 right-0 top-0 w-[min(90vw,390px)] overflow-y-auto bg-paper p-7" onClick={(event) => event.stopPropagation()}>
            <div className="mb-9 flex items-center justify-between"><h2 className="font-serif text-3xl">Filters</h2><button className="icon-button" onClick={() => setFiltersOpen(false)}><X size={18} /></button></div>
            {filterPanel}
            <button className="primary-button mt-8 w-full" onClick={() => setFiltersOpen(false)}>Show {visible.length} pieces</button>
          </aside>
        </div>
      )}
      <ProductDetailDialog product={selected} onClose={() => setSelected(null)} />
    </>
  );
}

export default function ShopPage() {
  return <Suspense fallback={<div className="page-shell min-h-[60vh] py-20">Loading catalog…</div>}><ShopContent /></Suspense>;
}
