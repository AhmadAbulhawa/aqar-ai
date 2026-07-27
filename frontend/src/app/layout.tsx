import type { Metadata } from "next";
import { SelectionProvider } from "@/context/selection-context";
import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { CinematicMotion } from "@/components/cinematic-motion";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Aqar AI — Rooms, considered", template: "%s — Aqar AI" },
  description: "Select real furniture and see your room furnished before you buy.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" dir="ltr" data-scroll-behavior="smooth">
      <body>
        <SelectionProvider>
          <SiteHeader />
          <CinematicMotion>{children}</CinematicMotion>
          <SiteFooter />
        </SelectionProvider>
      </body>
    </html>
  );
}
