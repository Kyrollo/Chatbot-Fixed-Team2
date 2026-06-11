// src/store/chatStore.ts
import { create } from 'zustand'

export interface Citation {
  chunk_id: string
  document_id: string
  page: number
  score: number
  text: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  streaming?: boolean
}

interface ChatState {
  messagesByDomain: Record<string, ChatMessage[]>
  activeCitation: Citation | null
  addMessage: (domainId: string, msg: ChatMessage) => void
  updateLastAssistant: (domainId: string, content: string) => void
  setCitations: (domainId: string, msgId: string, citations: Citation[]) => void
  setActiveCitation: (c: Citation | null) => void
}

export const useChatStore = create<ChatState>((set) => ({
  messagesByDomain: {},
  activeCitation: null,

  addMessage: (domainId, msg) =>
    set((state) => ({
      messagesByDomain: {
        ...state.messagesByDomain,
        [domainId]: [...(state.messagesByDomain[domainId] ?? []), msg],
      },
    })),

  updateLastAssistant: (domainId, content) =>
    set((state) => {
      const msgs = [...(state.messagesByDomain[domainId] ?? [])]
      const last = msgs[msgs.length - 1]
      if (last && last.role === 'assistant') {
        msgs[msgs.length - 1] = { ...last, content }
      }
      return { messagesByDomain: { ...state.messagesByDomain, [domainId]: msgs } }
    }),

  setCitations: (domainId, msgId, citations) =>
    set((state) => {
      const msgs = (state.messagesByDomain[domainId] ?? []).map((m) =>
        m.id === msgId ? { ...m, citations } : m
      )
      return { messagesByDomain: { ...state.messagesByDomain, [domainId]: msgs } }
    }),

  setActiveCitation: (c) => set({ activeCitation: c }),
}))
