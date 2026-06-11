// src/pages/ChatPage.tsx
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Send, Settings2 } from 'lucide-react'
import { useDomainStore } from '../store/domainStore'
import { useChatStore, ChatMessage } from '../store/chatStore'
import { useAuthStore } from '../store/authStore'
import { domainApi, generateApi } from '../lib/api'
import MessageBubble from '../components/chat/MessageBubble'
import CitationDrawer from '../components/chat/CitationDrawer'

export default function ChatPage() {
  const { activeDomainId, domains } = useDomainStore()
  const { messagesByDomain, addMessage, updateLastAssistant, setCitations } = useChatStore()
  const roles = useAuthStore((s) => s.roles)
  const isViewerOnly = roles.includes('reader') && !roles.includes('contributor') && !roles.includes('domain_admin') && !roles.includes('system_admin')
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  const messages = activeDomainId ? messagesByDomain[activeDomainId] ?? [] : []

  const { data: config } = useQuery({
    queryKey: ['domain-config', activeDomainId],
    queryFn: () => domainApi.getConfig(activeDomainId as string),
    enabled: !!activeDomainId,
  })

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  async function handleSend() {
    if (!input.trim() || !activeDomainId || loading) return
    const userMsg: ChatMessage = { id: crypto.randomUUID(), role: 'user', content: input.trim() }
    const assistantId = crypto.randomUUID()
    addMessage(activeDomainId, userMsg)
    addMessage(activeDomainId, { id: assistantId, role: 'assistant', content: '', streaming: true })
    setInput('')
    setLoading(true)

    const payload = {
      query: userMsg.content,
      domain_id: activeDomainId,
      stream: true,
      top_k_retrieve: config?.top_k_retrieve ?? 10,
      top_k_rerank: config?.top_k_rerank ?? 5,
      temperature: config?.temperature ?? 0.2,
      max_tokens: config?.max_tokens ?? 1024,
    }

    try {
      const stream = await generateApi.queryStream(payload)
      const reader = stream.getReader()
      const decoder = new TextDecoder()
      let acc = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        acc += decoder.decode(value, { stream: true })
        updateLastAssistant(activeDomainId, acc)
      }
    } catch (e) {
      // Fallback to non-streaming for citations
      try {
        const res = await generateApi.query({ ...payload, stream: false })
        updateLastAssistant(activeDomainId, res.answer)
        setCitations(activeDomainId, assistantId, res.citations ?? [])
      } catch (err) {
        updateLastAssistant(activeDomainId, `⚠️ Error: ${(err as Error).message}`)
      }
    } finally {
      setLoading(false)
    }
  }

  if (!activeDomainId) {
    return (
      <div className="flex items-center justify-center h-[60vh] text-muted-foreground text-sm">
        {domains.length === 0 ? 'No domains assigned to your account.' : 'Select a domain to start chatting.'}
      </div>
    )
  }

  return (
    <div className="flex h-[calc(100vh-5.5rem)] gap-4">
      <div className="flex-1 flex flex-col glass rounded-lg overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 className="font-semibold text-sm">Chat</h2>
          <button
            onClick={() => setShowSettings((s) => !s)}
            className="p-1.5 rounded-md hover:bg-accent transition flex items-center gap-1 text-xs text-muted-foreground"
          >
            <Settings2 size={14} /> Settings
          </button>
        </div>

        {showSettings && config && (
          <div className="px-4 py-3 border-b border-border bg-muted/30 grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
            <SettingStat label="Confidence Threshold" value={config.confidence_threshold} />
            <SettingStat label="Chunk Size" value={config.chunk_size} />
            <SettingStat label="Chunk Overlap" value={config.chunk_overlap} />
            <SettingStat label="LLM Route" value={config.llm_route} />
          </div>
        )}

        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
          {messages.length === 0 && (
            <div className="text-center text-sm text-muted-foreground mt-12">
              Ask a question about this domain's documents.
            </div>
          )}
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
        </div>

        <div className="p-3 border-t border-border flex items-center gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !isViewerOnly && handleSend()}
            placeholder={isViewerOnly ? "Read-only access: Viewer cannot send questions." : "Ask a question..."}
            disabled={isViewerOnly || loading}
            className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50 disabled:bg-muted text-foreground"
          />
          <button
            onClick={handleSend}
            disabled={isViewerOnly || loading}
            className="bg-primary text-primary-foreground rounded-md p-2.5 hover:opacity-90 transition disabled:opacity-50"
          >
            <Send size={16} />
          </button>
        </div>
      </div>

      <CitationDrawer />
    </div>
  )
}

function SettingStat({ label, value }: { label: string; value: any }) {
  return (
    <div>
      <div className="text-muted-foreground mb-0.5">{label}</div>
      <div className="font-medium">{String(value)}</div>
    </div>
  )
}
