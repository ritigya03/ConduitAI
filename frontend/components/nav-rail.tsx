"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Upload, ScanSearch, ListChecks, Inbox, BarChart3 } from "lucide-react";
import type { ComponentType } from "react";
import { cn } from "@/lib/utils";

const BATCH_ROUTE_RE = /^\/batches\/([^/]+)\//;

interface Stage {
  key: string;
  label: string;
  icon: ComponentType<{ className?: string }>;
  href: (batchId: string | null) => string | null;
}

// A literal pipeline-stage sequence, not decorative step-numbering --
// the workflow genuinely is sequential (upload before profile, profile
// before mapping, ...), so this doubles as both nav and progress
// indicator. Profile/Mapping/Load are batch-scoped and only linkable
// once a batch exists in the current URL -- there's no GET /batches
// endpoint to pick one from otherwise (see lib/recent-batches.ts).
const STAGES: Stage[] = [
  { key: "upload", label: "Upload", icon: Upload, href: () => "/" },
  { key: "profile", label: "Profile", icon: ScanSearch, href: (id) => (id ? `/batches/${id}/profile` : null) },
  { key: "mapping", label: "Mapping Review", icon: ListChecks, href: (id) => (id ? `/batches/${id}/mapping` : null) },
  { key: "load", label: "Load & Quarantine", icon: Inbox, href: (id) => (id ? `/batches/${id}/load` : null) },
  { key: "metrics", label: "Metrics", icon: BarChart3, href: () => "/metrics" },
];

export function NavRail() {
  const pathname = usePathname();
  const batchMatch = pathname.match(BATCH_ROUTE_RE);
  const batchId = batchMatch ? batchMatch[1] : null;

  const currentStageKey = pathname === "/"
    ? "upload"
    : pathname === "/metrics"
      ? "metrics"
      : pathname.includes("/profile")
        ? "profile"
        : pathname.includes("/mapping")
          ? "mapping"
          : pathname.includes("/load")
            ? "load"
            : null;

  return (
    <nav className="w-56 shrink-0 border-r border-border bg-sidebar flex flex-col py-6">
      <div className="px-5 pb-6">
        <span className="text-sm font-semibold tracking-tight text-foreground">ConduitAI</span>
        <p className="text-xs text-muted-foreground mt-0.5">Onboarding pipeline</p>
      </div>
      <ol className="flex flex-col gap-0.5 px-3">
        {STAGES.map((stage, index) => {
          const href = stage.href(batchId);
          const isCurrent = stage.key === currentStageKey;
          const Icon = stage.icon;

          const content = (
            <span className="flex items-center gap-2.5">
              <span
                className={cn(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-medium",
                  isCurrent ? "bg-foreground text-background" : "bg-muted text-muted-foreground",
                )}
              >
                {index + 1}
              </span>
              <Icon className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{stage.label}</span>
            </span>
          );

          if (href === null) {
            return (
              <li
                key={stage.key}
                className="flex items-center rounded-md px-2.5 py-2 text-sm text-muted-foreground/50 cursor-not-allowed"
              >
                {content}
              </li>
            );
          }

          return (
            <li key={stage.key}>
              <Link
                href={href}
                className={cn(
                  "flex items-center rounded-md px-2.5 py-2 text-sm transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isCurrent
                    ? "bg-secondary text-foreground font-medium"
                    : "text-muted-foreground hover:bg-secondary/60 hover:text-foreground",
                )}
              >
                {content}
              </Link>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
