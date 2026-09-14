import type { ReactNode } from "react";
import { NavRail } from "./nav-rail";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen w-full">
      <NavRail />
      <main className="min-w-0 flex-1 overflow-x-auto">
        <div className="mx-auto max-w-5xl px-8 py-10">{children}</div>
      </main>
    </div>
  );
}
