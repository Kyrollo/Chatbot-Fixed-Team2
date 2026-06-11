/**
 * ChatInput.jsx — message composer bar at bottom of chat
 *
 * Features:
 *  - Auto-grow textarea (Shift+Enter for newline, Enter to send)
 *  - Stream toggle
 *  - Send / Stop button
 *  - Disabled when no domain is selected
 */
import { useRef, useState, useCallback } from "react";
import { Send, Square } from "lucide-react";
import clsx from "clsx";

export function ChatInput({ onSend, onCancel, streaming, disabled }) {
  const [value, setValue]   = useState("");
  const [stream, setStream] = useState(true);
  const textareaRef         = useRef(null);

  const handleSubmit = useCallback(() => {
    const q = value.trim();
    if (!q || streaming) return;
    onSend(q, { stream });
    setValue("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [value, streaming, stream, onSend]);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleInput = (e) => {
    setValue(e.target.value);
    // Auto-grow
    const el = e.target;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  };

  return (
    <div className="border-t border-surface-4 bg-surface-1 px-4 py-3">
      {/* Textarea row */}
      <div className={clsx(
        "flex items-end gap-2 rounded-lg border px-3 py-2.5 transition-colors",
        disabled
          ? "border-surface-3 opacity-50 cursor-not-allowed"
          : "border-surface-4 focus-within:border-accent/60 focus-within:ring-1 focus-within:ring-accent/20 bg-surface-2",
      )}>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          disabled={disabled || streaming}
          rows={1}
          placeholder={
            disabled
              ? "Select a domain to start chatting…"
              : "Ask a question… (Enter to send, Shift+Enter for newline)"
          }
          className={clsx(
            "flex-1 bg-transparent resize-none text-sm text-text-primary",
            "placeholder-text-muted outline-none leading-relaxed",
            "disabled:cursor-not-allowed",
          )}
          style={{ minHeight: "24px" }}
        />

        {/* Send / Stop */}
        {streaming ? (
          <button
            onClick={onCancel}
            className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded bg-red/20 text-red hover:bg-red/30 transition-colors"
            title="Stop generating"
          >
            <Square size={14} />
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={!value.trim() || disabled}
            className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded bg-accent hover:bg-accent-dim disabled:opacity-30 disabled:cursor-not-allowed text-white transition-colors"
            title="Send (Enter)"
          >
            <Send size={14} />
          </button>
        )}
      </div>

      {/* Footer row */}
      <div className="flex items-center justify-between mt-2 px-0.5">
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <div
            onClick={() => !streaming && setStream((s) => !s)}
            className={clsx(
              "relative w-8 h-4 rounded-full transition-colors",
              stream ? "bg-accent" : "bg-surface-4",
              streaming && "opacity-40 cursor-not-allowed",
            )}
          >
            <span
              className={clsx(
                "absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform shadow-sm",
                stream ? "translate-x-4" : "translate-x-0.5",
              )}
            />
          </div>
          <span className="text-[11px] text-text-muted">Stream</span>
        </label>
        <span className="text-[11px] text-text-muted">
          {value.length > 0 && `${value.length} chars`}
        </span>
      </div>
    </div>
  );
}
