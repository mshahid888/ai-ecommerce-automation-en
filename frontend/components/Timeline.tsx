import type { TimelineStep } from "@/lib/api";

function statusDot(status: string): string {
  if (status === "failed") return "bg-red-500";
  if (status === "done") return "bg-green-500";
  return "bg-slate-300";
}

function badgeClass(status: string): string {
  if (status === "failed") return "bg-red-100 text-red-800";
  if (status === "done") return "bg-green-100 text-green-800";
  return "bg-slate-100 text-slate-600";
}

function badgeLabel(status: string): string {
  if (status === "failed") return "failed";
  if (status === "done") return "done";
  return status;
}

interface TimelineProps {
  steps: TimelineStep[];
}

/** Renders the workflow timeline returned by the backend. */
export default function Timeline({ steps }: TimelineProps) {
  if (steps.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-6 text-center">
        <p className="text-sm leading-relaxed text-slate-500">
          No steps recorded yet.
          <br />
          Ask a question in the chat to see the workflow.
        </p>
      </div>
    );
  }

  return (
    <ol className="space-y-2.5" aria-label="Workflow steps">
      {steps.map((step, index) => (
        <li
          key={`${step.key}-${index}`}
          className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 shadow-sm transition-colors hover:border-blue-200"
        >
          <div className="flex min-w-0 items-center gap-2.5">
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${statusDot(step.status)}`}
              aria-hidden="true"
            />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-900">
                {step.label_de}
              </p>
              <p className="font-mono text-[11px] text-slate-400">{step.key}</p>
            </div>
          </div>
          <span
            className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold ${badgeClass(step.status)}`}
          >
            {badgeLabel(step.status)}
          </span>
        </li>
      ))}
    </ol>
  );
}
