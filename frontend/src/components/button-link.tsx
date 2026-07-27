import Link from "next/link";
import { ArrowRight } from "lucide-react";

export function ButtonLink({
  href,
  children,
  variant = "dark",
}: {
  href: string;
  children: React.ReactNode;
  variant?: "dark" | "light" | "line";
}) {
  return (
    <Link href={href} className={`button-link button-link-${variant}`}>
      {children}<ArrowRight size={16} strokeWidth={1.7} />
    </Link>
  );
}
