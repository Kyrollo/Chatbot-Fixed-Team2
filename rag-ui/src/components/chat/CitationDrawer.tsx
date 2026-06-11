// src/components/chat/CitationDrawer.tsx
import { X } from 'lucide-react'
import { useChatStore } from '../../store/chatStore'

export default function CitationDrawer() {
  const { activeCitation, setActiveCitation } = useChatStore()

  if (!activeCitation) return null

  return (
    <div className="fixed inset-0 z-30 flex justify-end">
      <div className="absolute inset-0 bg-black/30" onClick={() => setActiveCitation(null)} />
      <div className="relative w-full max-w-md h-full bg-card border-l border-border shadow-xl p-5 overflow-y-auto animate-in slide-in-from-right">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-sm">Source Citation</h3>
          <button onClick={() => setActiveCitation(null)} className="p-1 rounded-md hover:bg-accent">
            <X size={16} />
          </button>
        </div>

        <div className="space-y-3 text-sm">
          <div>
            <div className="text-xs text-muted-foreground mb-1">Document ID</div>
            <div className="font-mono text-xs break-all">{activeCitation.document_id}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">Chunk ID</div>
            <div className="font-mono text-xs break-all">{activeCitation.chunk_id}</div>
          </div>
          <div className="flex gap-6">
            <div>
              <div className="text-xs text-muted-foreground mb-1">Page</div>
              <div>{activeCitation.page}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground mb-1">Score</div>
              <div>{activeCitation.score.toFixed(3)}</div>
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">Retrieved Passage</div>
            <div className="rounded-md border border-border bg-muted/40 p-3 text-sm leading-relaxed whitespace-pre-wrap">
              {activeCitation.text}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
