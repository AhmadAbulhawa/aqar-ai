import Link from "next/link";
import { AlertTriangle, ArrowLeft, ShieldX } from "lucide-react";
import { mediaUrl } from "@/lib/api";
import { humanize } from "@/lib/format";
import type { FailedComplianceDetail } from "@/types/api";

function unique(values: string[]) {
  return values.filter((value, index) => value && values.indexOf(value) === index);
}

function IssueList({ title, values, tone = "dark" }: { title: string; values: string[]; tone?: "dark" | "warm" }) {
  if (!values.length) return null;
  return (
    <section className={`issue-panel ${tone === "warm" ? "issue-panel-warm" : ""}`}>
      <h2 className="eyebrow">{title}</h2>
      <ul className="mt-4 space-y-3 text-sm leading-6">
        {values.map((value, index) => <li key={`${value}-${index}`} className="flex gap-3"><span className="font-serif italic text-muted">0{index + 1}</span><span>{humanize(value)}</span></li>)}
      </ul>
    </section>
  );
}

export function ComplianceFailure({ detail }: { detail: FailedComplianceDetail }) {
  const primaryIssues = unique([
    ...detail.hard_failure_codes,
    ...detail.failure_reasons,
  ]);
  const otherIssues = unique(
    detail.violations.filter((value) => !primaryIssues.includes(value)),
  );

  return (
    <div className="page-shell compliance-failure py-8 md:py-12">
      <header className="compliance-failure-header">
        <div>
          <div className="flex items-center gap-3 text-[#953d30]"><ShieldX size={22} /><span className="eyebrow text-[#953d30]">Failed compliance</span></div>
          <h1 className="mt-3 font-serif text-4xl leading-[.97] tracking-[-.035em] md:text-6xl">Design Not Approved</h1>
        </div>
        <p className="max-w-xl text-sm leading-7 text-muted">{detail.message}</p>
      </header>

      {detail.generated_image?.image_url && (
        <figure className="compliance-failure-figure">
          {/* Preserve the complete backend audit image at natural aspect ratio. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={mediaUrl(detail.generated_image.image_url) || ""}
            alt="Complete rejected generated design"
            className="compliance-failure-image"
          />
          <figcaption>Rejected render · shown in full for review</figcaption>
        </figure>
      )}

      <section className="compliance-audit" aria-labelledby="compliance-comments-title">
        <div>
          <p className="eyebrow">Post-render review</p>
          <h2 id="compliance-comments-title" className="mt-2 font-serif text-3xl md:text-4xl">Compliance comments</h2>
          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <IssueList title="Required corrections" values={primaryIssues} />
            <IssueList title="Other observations" values={otherIssues} />
            <div className="md:col-span-2"><IssueList title="Warnings" values={detail.warnings} tone="warm" /></div>
          </div>
        </div>

        <aside className="compliance-audit-meta">
          <div>
            <p className="field-label">Generated design ID</p>
            <p className="break-all font-mono text-sm">{detail.generated_design_id}</p>
          </div>
          <div className="flex items-start gap-3 bg-[#eee5d5] p-4 text-xs leading-6 text-[#6b5533]"><AlertTriangle className="mt-0.5 shrink-0" size={17} /><p>No automatic retry was attempted. Paid retry is disabled and this rejected image is never presented as a successful design.</p></div>
          <Link href="/studio" className="secondary-button inline-flex items-center justify-center gap-3"><ArrowLeft size={15} /> Return to studio</Link>
        </aside>
      </section>
    </div>
  );
}
