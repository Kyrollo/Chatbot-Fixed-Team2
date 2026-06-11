/**
 * ChatPage.jsx — main chat interface
 *
 * Layout:
 *   [DomainSidebar] | [Chat + Input] | [CitationPanel (optional)]
 */
import { useState, useEffect, useRef } from "react";
import { Trash2, Upload as UploadIcon, X, Settings } from "lucide-react";
import clsx from "clsx";

import { DomainSidebar }  from "@/components/DomainSidebar.jsx";
import { ChatMessage }    from "@/components/ChatMessage.jsx";
import { ChatInput }      from "@/components/ChatInput.jsx";
import { CitationPanel }  from "@/components/CitationPanel.jsx";
import { UploadPanel }    from "@/components/UploadPanel.jsx";
import { useChat }        from "@/hooks/useChat.js";
import { useUpload }      from "@/hooks/useUpload.js";
import { useDomains }     from "@/hooks/useDomains.js";
import { useAuth }        from "@/lib/auth.jsx";
import { Avatar, Tooltip } from "@/components/ui.jsx";

export default function ChatPage() {
  const { user, logout }    = useAuth();
  const { domains, loading: domainsLoading, refetch, createDomain } = useDomains();

  const [activeDomainId, setActiveDomainId] = useState(null);
  const [activeCitation, setActiveCitation] = useState(null);
  const [showUpload, setShowUpload]         = useState(false);

  const { messages, streaming, send, cancel, clear } = useChat(activeDomainId);
  const { uploads, upload, dismiss }                  = useUpload(activeDomainId);

  const bottomRef = useRef(null);

  // Auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Pick first active domain automatically
  useEffect(() => {
    if (!activeDomainId && domains.length > 0) {
      const first = domains.find((d) => d.status === "active") ?? domains[0];
      setActiveDomainId(first.id);
    }
  }, [domains, activeDomainId]);

  const activeDomain = domains.find((d) => d.id === activeDomainId);

  return (
    <div className="flex flex-col h-screen bg-surface-1 overflow-hidden">
      {/* ── Top bar ── */}
      <header className="flex items-center justify-between px-4 h-12 border-b border-surface-4 bg-surface-2 flex-shrink-0 z-10">
        <div className="flex items-center gap-3">
          {/* Logo mark */}
          <span className="w-6 h-6 rounded bg-accent flex items-center justify-center text-xs font-bold text-white select-none">
            R
          </span>
          <span className="font-display text-sm font-semibold text-text-primary hidden sm:block">
            RAG System
          </span>
          {activeDomain && (
            <>
              <span className="text-text-muted text-sm">/</span>
              <span className="text-sm text-text-secondary truncate max-w-[200px]">
                {activeDomain.name}
              </span>
            </>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Upload toggle */}
          <Tooltip label="Upload documents">
            <button
              onClick={() => setShowUpload((s) => !s)}
              disabled={!activeDomainId}
              className={clsx(
                "btn-ghost p-2 rounded",
                showUpload && "bg-surface-3 text-text-primary",
                !activeDomainId && "opacity-30 cursor-not-allowed",
              )}
            >
              <UploadIcon size={15} />
            </button>
          </Tooltip>

          {/* Clear chat */}
          <Tooltip label="Clear conversation">
            <button
              onClick={clear}
              disabled={!messages.length}
              className="btn-ghost p-2 rounded disabled:opacity-30"
            >
              <Trash2 size={15} />
            </button>
          </Tooltip>

          {/* User */}
          <div className="flex items-center gap-2 ml-1">
            <Avatar name={user?.username} size="sm" />
            <span className="text-xs text-text-secondary hidden md:block">{user?.username}</span>
          </div>

          <button onClick={logout} className="btn-ghost text-xs px-2 py-1.5">
            Sign out
          </button>
        </div>
      </header>

      {/* ── Main area ── */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Domain sidebar */}
        <DomainSidebar
          domains={domains}
          loading={domainsLoading}
          activeDomainId={activeDomainId}
          onSelect={(id) => { setActiveDomainId(id); setActiveCitation(null); }}
          onRefresh={refetch}
          onCreate={createDomain}
        />

        {/* Center column */}
        <div className="flex flex-col flex-1 min-w-0 h-full">
          {/* Upload slide-down panel */}
          {showUpload && activeDomainId && (
            <div className="border-b border-surface-4 bg-surface-2 px-4 py-4 animate-slide-up flex-shrink-0">
              <div className="flex items-center justify-between mb-3">
                <span className="text-xs font-semibold text-text-secondary uppercase tracking-widest">
                  Upload Documents
                </span>
                <button onClick={() => setShowUpload(false)} className="btn-ghost p-1 rounded">
                  <X size={13} />
                </button>
              </div>
              <UploadPanel
                uploads={uploads}
                onUpload={upload}
                disabled={!activeDomainId}
              />
            </div>
          )}

          {/* Messages */}
          <div className="flex-1 overflow-y-auto py-4">
            {messages.length === 0 ? (
              <EmptyState domain={activeDomain} />
            ) : (
              messages.map((msg) => (
                <ChatMessage
                  key={msg.id}
                  message={msg}
                  onCitationClick={setActiveCitation}
                  activeCitationId={activeCitation?.chunk_id}
                />
              ))
            )}
            <div ref={bottomRef} />
          </div>

          {/* Input */}
          <ChatInput
            onSend={send}
            onCancel={cancel}
            streaming={streaming}
            disabled={!activeDomainId}
          />
        </div>

        {/* Citation panel */}
        {activeCitation && (
          <CitationPanel
            citation={activeCitation}
            onClose={() => setActiveCitation(null)}
          />
        )}
      </div>
    </div>
  );
}

// ─── Empty state ──────────────────────────────────────────────────────────────
function EmptyState({ domain }) {
  if (!domain) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-8">
        <span className="text-4xl opacity-20">←</span>
        <p className="text-text-secondary text-sm">Select a domain to start chatting.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8 max-w-lg mx-auto">
      <div className="w-12 h-12 rounded-xl bg-accent/10 border border-accent/20 flex items-center justify-center">
        <span className="text-accent font-bold text-xl font-display">R</span>
      </div>
      <div>
        <h2 className="text-text-primary font-semibold font-display text-base mb-1">
          {domain.name}
        </h2>
        <p className="text-text-secondary text-sm">
          {domain.description || "Ask anything about the documents in this domain."}
        </p>
      </div>
      <div className="flex flex-col gap-2 w-full">
        {EXAMPLE_PROMPTS.map((p) => (
          <button
            key={p}
            className="text-left text-xs text-text-secondary bg-surface-2 hover:bg-surface-3 border border-surface-4 rounded-lg px-3 py-2 transition-colors"
          >
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}

const EXAMPLE_PROMPTS = [
  "Summarise the key findings in the uploaded documents.",
  "What are the main risks or limitations mentioned?",
  "List all dates and deadlines referenced.",
];
