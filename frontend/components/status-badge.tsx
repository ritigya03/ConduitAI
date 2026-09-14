import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { MappingBucket } from "@/lib/types";

// Confidence bucket -> color, the one place this build spends its
// single amber accent decoratively-adjacent: human_confirm literally
// *is* "needs your attention" (see design direction). Color augments
// the number, never replaces it -- both are always rendered together.
const BUCKET_STYLE: Record<MappingBucket, string> = {
  auto_accept: "border-[var(--success)]/30 bg-[var(--success)]/10 text-[var(--success)]",
  human_confirm: "border-[var(--accent-amber)]/30 bg-[var(--accent-amber)]/10 text-[var(--accent-amber)]",
  unmapped: "border-[var(--danger)]/30 bg-[var(--danger)]/10 text-[var(--danger)]",
};

const BUCKET_LABEL: Record<MappingBucket, string> = {
  auto_accept: "Auto-accept",
  human_confirm: "Needs review",
  unmapped: "Unmapped",
};

export function BucketBadge({ bucket, confidence }: { bucket: MappingBucket; confidence: number }) {
  return (
    <Badge variant="outline" className={cn("gap-1.5", BUCKET_STYLE[bucket])}>
      <span>{BUCKET_LABEL[bucket]}</span>
      <span className="font-mono">{confidence.toFixed(2)}</span>
    </Badge>
  );
}

export function ErrorCodeBadge({ code }: { code: string }) {
  return (
    <Badge
      variant="outline"
      className="border-[var(--danger)]/30 bg-[var(--danger)]/10 font-mono text-[var(--danger)]"
    >
      {code}
    </Badge>
  );
}

export function SuccessBadge({ children }: { children: React.ReactNode }) {
  return (
    <Badge variant="outline" className="border-[var(--success)]/30 bg-[var(--success)]/10 text-[var(--success)]">
      {children}
    </Badge>
  );
}
