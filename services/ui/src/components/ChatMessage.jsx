/**
 * ChatMessage.jsx — renders a single user or assistant message
 *
 * Assistant messages support:
 *  - Streaming (shows TypingDots while loading)
 *  - Markdown rendering
 *  - Citation pills that expand a sidebar panel
 *  - Error state
 *  - Cache hit / model info footer
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import clsx from "clsx";
import { TypingDots, Badge, Avatar } from "./ui.jsx";

export function ChatMessage({ message, onCitationClick, activeCitationId }) {
  const isUser      = message.role === "user";
  const isLoading   = message.loading;
  const hasContent  = message.content?.trim().length > 0;
  const citations   = message.citations ?? [];

  return (
    <div
      className={clsx(
        "flex gap-3 px-4 py-3 animate-slide-up group",
        isUser ? "flex-row-reverse" : "flex-row",
      )}
    >
      {/* Avatar */}
      <Avatar name={isUser ? "You" : "AI"} />

      {/* Bubble */}
      <div className={clsx("flex flex-col gap-1.5 max-w-[80%] min-w-0", isUser && "items-end")}>
        <div
          className={clsx(
            "rounded-lg px-4 py-3 text-sm leading-relaxed",
            isUser
              ? "bg-accent text-white rounded-tr-sm"
              : "bg-surface-2 border border-surface-4 rounded-tl-sm",
          )}
        >
          {isLoading && !hasContent ? (
            <TypingDots />
          ) : message.error ? (
            <span className="text-red text-xs">{message.error}</span>
          ) : isUser ? (
            <p className="whitespace-pre-wrap">{message.content}</p>
          ) : (
            <div className="message-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Citation pills */}
        {citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-0.5">
            {citations.map((c, i) => (
              <button
                key={c.chunk_id}
                onClick={() => onCitationClick?.(c)}
                className={clsx(
                  "citation-pill transition-colors",
                  activeCitationId === c.chunk_id && "bg-teal/30 border-teal",
                )}
              >
                <span className="font-mono text-[10px] opacity-70">{i + 1}</span>
                {c.page != null && <span>p.{c.page}</span>}
                <span className="opacity-60 truncate max-w-[120px]">
                  {c.document_id.slice(0, 8)}…
                </span>
                <ScoreBar score={c.score} />
              </button>
            ))}
          </div>
        )}

        {/* Footer: model / cache */}
        {!isUser && !isLoading && (message.model || message.cache_hit) && (
          <div className="flex items-center gap-2 mt-0.5">
            {message.model && (
              <span className="text-[11px] text-text-muted font-mono">
                {message.llm_route}/{message.model}
              </span>
            )}
            {message.cache_hit && (
              <Badge variant="teal" className="text-[10px]">cached</Badge>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// Tiny visual confidence bar inside a citation pill
function ScoreBar({ score }) {
  const pct = Math.round(score * 100);
  return (
    <span className="flex items-center gap-1">
      <span
        className="h-1 rounded-full bg-teal opacity-70"
        style={{ width: `${Math.max(12, pct / 5)}px` }}
      />
      <span className="text-[10px] opacity-50">{pct}%</span>
    </span>
  );
}
