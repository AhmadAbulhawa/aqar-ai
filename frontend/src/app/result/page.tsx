"use client";

import Link from "next/link";
import { ArrowLeft, CheckCircle2, Eye, ShieldCheck } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ComplianceFailure } from "@/components/compliance-failure";
import { ResultProductDrawer } from "@/components/result-product-drawer";
import {
  getFurniture,
  getLatestGeneratedDesignAudit,
  isFailedCompliance,
  mediaUrl,
} from "@/lib/api";
import {
  FAILURE_KEY,
  loadJson,
  PENDING_KEY,
  RESULT_KEY,
  type PendingDesign,
} from "@/lib/result-storage";
import type { FailedComplianceDetail, Furniture, RenderResponse } from "@/types/api";

function ResultContent() {
  const searchParams = useSearchParams();
  const status = searchParams.get("status");
  const recoveryRoomId = Number(searchParams.get("room_request_id"));
  const recoverySelectionId = searchParams.get("selection_id");
  const [result, setResult] = useState<RenderResponse | null>(null);
  const [failure, setFailure] = useState<FailedComplianceDetail | null>(null);
  const [pending, setPending] = useState<PendingDesign | null>(null);
  const [catalog, setCatalog] = useState<Furniture[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [failureDrawerOpen, setFailureDrawerOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setResult(loadJson<RenderResponse>(RESULT_KEY));
      let storedFailure = loadJson<FailedComplianceDetail>(FAILURE_KEY);
      if (
        status === "recover"
        && Number.isInteger(recoveryRoomId)
        && recoveryRoomId > 0
        && recoverySelectionId
      ) {
        try {
          const audit = await getLatestGeneratedDesignAudit(
            recoveryRoomId,
            recoverySelectionId,
          );
          if (isFailedCompliance(audit)) storedFailure = audit;
        } catch {
          // Fall back to browser-session storage when no matching audit exists.
        }
      }
      if (cancelled) return;
      setFailure(storedFailure);
      setPending(loadJson<PendingDesign>(PENDING_KEY));
      setHydrated(true);
    }, 0);
    getFurniture().then(setCatalog).catch(() => undefined);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [recoveryRoomId, recoverySelectionId, status]);

  if (!hydrated) return <div className="page-shell min-h-[60vh] py-16 text-sm text-muted">Loading design result…</div>;
  if ((status === "failed-compliance" || status === "recover") && failure) {
    const inventory = pending?.recommendation;
    return (
      <section className="result-stage">
        <div className={`result-main ${failureDrawerOpen && inventory ? "drawer-visible" : ""}`}>
          <ComplianceFailure detail={failure} />
        </div>
        {inventory && (
          <ResultProductDrawer
            open={failureDrawerOpen}
            onToggle={() => setFailureDrawerOpen((value) => !value)}
            items={inventory.selected_furniture}
            catalog={catalog}
            total={inventory.total_price}
            currency={inventory.currency}
          />
        )}
      </section>
    );
  }

  if (!result) {
    return (
      <section className="page-shell grid min-h-[68vh] place-items-center py-16 text-center">
        <div><p className="eyebrow">Result</p><h1 className="mt-5 font-serif text-5xl">No finished design on this device.</h1><p className="mx-auto mt-4 max-w-lg text-sm leading-7 text-muted">Results are stored for this browser session after an explicit render completes.</p><Link href={pending ? "/studio" : "/shop"} className="secondary-button mt-7 inline-flex items-center gap-3"><ArrowLeft size={15} /> {pending ? "Return to studio" : "Explore furniture"}</Link></div>
      </section>
    );
  }

  return (
    <section className="result-stage">
      <div className={`result-main ${drawerOpen ? "drawer-visible" : ""}`}>
        <div className="page-shell py-8 md:py-10">
          <div className="mb-7 flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
            <div><div className="flex items-center gap-2 text-olive"><CheckCircle2 size={18} /><span className="eyebrow text-olive">Design generated</span></div><h1 className="mt-3 font-serif text-4xl md:text-5xl">Your room, brought into view.</h1></div>
            <div className="flex gap-4 text-[10px] font-bold uppercase tracking-[.12em] text-muted"><span className="flex items-center gap-2"><ShieldCheck size={15} /> {result.compliance.status}</span><span className="flex items-center gap-2"><Eye size={15} /> Exact inventory</span></div>
          </div>
          <figure className="relative overflow-hidden bg-[#dedbd4]">
            {/* The backend returns this image only after compliance did not fail. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={mediaUrl(result.generated_image.image_url) || ""} alt="Generated furnished room" className="max-h-[calc(100svh-220px)] min-h-[420px] w-full object-contain" />
            <figcaption className="absolute bottom-4 left-4 bg-black/68 px-4 py-2 text-[9px] font-bold uppercase tracking-[.14em] text-white backdrop-blur">Generated design · {result.generated_design_id}</figcaption>
          </figure>
        </div>
      </div>
      <ResultProductDrawer open={drawerOpen} onToggle={() => setDrawerOpen((value) => !value)} items={result.selected_furniture} catalog={catalog} total={result.total_price} currency={result.currency} />
    </section>
  );
}

export default function ResultPage() {
  return <Suspense fallback={<div className="page-shell min-h-[60vh] py-16">Loading result…</div>}><ResultContent /></Suspense>;
}
