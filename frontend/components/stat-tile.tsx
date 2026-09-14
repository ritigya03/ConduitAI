import { cn } from "@/lib/utils";

/** Numbers as the hero: a big monospace figure with a small label under
 * it, reused everywhere this build shows a count (load summary,
 * metrics dashboard) instead of a chart for what's fundamentally a
 * point-in-time count. */
export function StatTile({
  value,
  label,
  tone = "default",
}: {
  value: number | string;
  label: string;
  tone?: "default" | "success" | "danger";
}) {
  return (
    <div className="rounded-xl bg-card p-5 ring-1 ring-foreground/10">
      <div
        className={cn(
          "font-mono text-3xl font-semibold tabular-nums",
          tone === "success" && "text-[var(--success)]",
          tone === "danger" && "text-[var(--danger)]",
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-xs text-muted-foreground">{label}</div>
    </div>
  );
}
