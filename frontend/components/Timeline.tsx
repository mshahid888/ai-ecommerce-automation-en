import type { TimelineStep } from "@/lib/api";

function badgeClass(status: string): string {
  if (status === "failed") return "bg-red-100 text-red-800";
  if (status === "done") return "bg-green-100 text-green-800";
  return "bg-slate-100 text-slate-700";
}

function badgeLabel(status: string): string {
  if (status === "failed") return "failed";
  if (status === "done") return "done";
  return status;
}

interface TimelineProps {
  steps: TimelineStep[];
}

/** Renders the workflow timeline returned by the backend (Phase 1 skeleton). */
export default function Timeline({ steps }: TimelineProps) {
  if (steps.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        No steps recorded yet. Ask a question in the chat to see the workflow.
      </p>
    );
  }

  return (
    <ol className="space-y-3" aria-label="Workflow steps">
      {steps.map((step, index) => (
        <li
          key={`${step.key}-${index}`}
          className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2.5"
        >
          <div>
            <p className="text-sm font-medium text-slate-900">{step.label_de}</p>
            <p className="text-xs text-slate-500">{step.key}</p>
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
