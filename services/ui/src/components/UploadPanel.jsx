/**
 * UploadPanel.jsx — PDF upload drag-and-drop area + upload status list
 */
import { useRef, useState } from "react";
import { Upload, CheckCircle, XCircle, Clock, Loader } from "lucide-react";
import clsx from "clsx";

const STATUS_ICON = {
  uploading:  <Loader   size={13} className="animate-spin text-accent" />,
  pending:    <Clock    size={13} className="text-amber" />,
  processing: <Loader   size={13} className="animate-spin text-accent" />,
  done:       <CheckCircle size={13} className="text-teal" />,
  failed:     <XCircle  size={13} className="text-red" />,
  timeout:    <XCircle  size={13} className="text-amber" />,
};

const STATUS_LABEL = {
  uploading:  "Uploading…",
  pending:    "Queued",
  processing: "Processing…",
  done:       "Ready",
  failed:     "Failed",
  timeout:    "Timed out",
};

export function UploadPanel({ uploads, onUpload, disabled }) {
  const inputRef       = useRef(null);
  const [dragging, setDragging] = useState(false);

  const handleFiles = (files) => {
    const pdfs = Array.from(files).filter((f) => f.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length) return;
    pdfs.forEach(onUpload);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    if (disabled) return;
    handleFiles(e.dataTransfer.files);
  };

  return (
    <div className="flex flex-col gap-3">
      {/* Drop zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => !disabled && inputRef.current?.click()}
        className={clsx(
          "flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-6 cursor-pointer transition-colors text-center",
          disabled
            ? "border-surface-3 opacity-40 cursor-not-allowed"
            : dragging
            ? "border-accent bg-accent/10"
            : "border-surface-4 hover:border-surface-4 hover:bg-surface-3/40",
        )}
      >
        <Upload size={20} className={dragging ? "text-accent" : "text-text-muted"} />
        <div>
          <p className="text-xs text-text-secondary font-medium">Drop PDF here</p>
          <p className="text-[11px] text-text-muted">or click to browse — max 50 MB</p>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf"
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {/* Upload list */}
      {uploads.length > 0 && (
        <ul className="space-y-1.5">
          {uploads.map((u) => (
            <li
              key={u.id}
              className="flex items-center gap-2 bg-surface-3 rounded px-3 py-2 text-xs"
            >
              {STATUS_ICON[u.status] || <Clock size={13} className="text-text-muted" />}
              <span className="flex-1 truncate text-text-secondary">{u.filename}</span>
              <span className={clsx(
                "flex-shrink-0 font-mono text-[10px]",
                u.status === "done"   ? "text-teal" :
                u.status === "failed" ? "text-red"  : "text-text-muted",
              )}>
                {STATUS_LABEL[u.status] ?? u.status}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
