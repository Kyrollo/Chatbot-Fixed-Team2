/**
 * CitationPanel.jsx — slide-in sidebar showing full citation text
 *
 * Receives the active citation object:
 *   { chunk_id, document_id, page, score, text }
 */
import { X, FileText } from "lucide-react";
import clsx from "clsx";
import { Badge } from "./ui.jsx";

export function CitationPanel({ citation, onClose }) {
  if (!citation) return null;

  const pct = Math.round(citation.score * 100);

  return (
    <aside
      className={clsx(
        "flex flex-col w-80 min-w-[18rem] max-w-[22rem] h-full",
        "bg-surface-2 border-l border-surface-4",
        "animate-slide-up",
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-4 flex-shrink-0">
        <div className="flex items-center gap-2 text-sm font-medium text-text-primary">
          <FileText size={14} className="text-teal" />
          Source
        </div>
        <button
          onClick={onClose}
          className="btn-ghost p-1 rounded"
          aria-label="Close citation panel"
        >
          <X size={14} />
        </button>
      </div>

      {/* Meta */}
      <div className="px-4 py-3 border-b border-surface-4 flex-shrink-0 space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          {citation.page != null && (
            <Badge variant="teal">Page {citation.page}</Badge>
          )}
          <Badge variant="amber">{pct}% relevance</Badge>
        </div>
        <div>
          <p className="text-[11px] text-text-muted font-mono truncate">
            doc: {citation.document_id}
          </p>
          <p className="text-[11px] text-text-muted font-mono truncate">
            chunk: {citation.chunk_id}
          </p>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">
          {citation.text}
        </p>
      </div>
    </aside>
  );
}
