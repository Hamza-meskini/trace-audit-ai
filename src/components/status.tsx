import { cn } from "@/lib/utils";
import type { CoverageStatus, ReviewState, Severity } from "@/lib/mock-data";
import type { ReactNode } from "react";

const base =
  "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap";

export function Dot({ className }: { className?: string }) {
  return <span className={cn("size-1.5 rounded-full", className)} />;
}

const coverageStyles: Record<CoverageStatus, string> = {
  Supported: "border-success/25 bg-success-soft text-success",
  Partial: "border-warning/30 bg-warning-soft text-warning",
  Missing: "border-critical/25 bg-critical-soft text-critical",
  Conflict: "border-critical/25 bg-critical-soft text-critical",
};

const coverageDot: Record<CoverageStatus, string> = {
  Supported: "bg-success",
  Partial: "bg-warning",
  Missing: "bg-critical",
  Conflict: "bg-critical",
};

export function CoverageBadge({ status }: { status: CoverageStatus }) {
  return (
    <span className={cn(base, coverageStyles[status])}>
      <Dot className={coverageDot[status]} />
      {status}
    </span>
  );
}

const severityStyles: Record<Severity, string> = {
  Critical: "border-critical/30 bg-critical-soft text-critical",
  High: "border-warning/35 bg-warning-soft text-warning",
  Medium: "border-info/25 bg-info-soft text-info",
  Low: "border-border bg-neutral-soft text-muted-foreground",
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <span className={cn(base, severityStyles[severity])}>{severity}</span>;
}

const reviewStyles: Record<string, string> = {
  Reviewed: "border-success/25 bg-success-soft text-success",
  Approved: "border-success/25 bg-success-soft text-success",
  "Needs review": "border-warning/30 bg-warning-soft text-warning",
  Open: "border-border bg-neutral-soft text-muted-foreground",
  Rejected: "border-critical/25 bg-critical-soft text-critical",
};

export function ReviewBadge({ state }: { state: ReviewState | string }) {
  return <span className={cn(base, reviewStyles[state] ?? reviewStyles["Open"])}>{state}</span>;
}

export function Tag({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        base,
        "border-border bg-muted text-muted-foreground font-normal",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono text-xs tabular", className)}>{children}</span>;
}
