// src/components/chat/MessageBubble.tsx
import ReactMarkdown from 'react-markdown'
import { ChatMessage, useChatStore } from '../../store/chatStore'
import { cn } from '../../lib/utils'

// Replaces [1], [2] tokens with clickable citation buttons
function renderWithCitations(content: string, msg: ChatMessage, onCite: (idx: number) => void) {
  const parts = content.split(/(\[\d+\])/g)
  return parts.map((part, i) => {
    const match = part.match(/^\[(\d+)\]$/)
    if (match && msg.citations) {
      const idx = parseInt(match[1], 10) - 1
      if (msg.citations[idx]) {
        return (
          <button
            key={i}
            onClick={() => onCite(idx)}
            className="inline-flex items-center justify-center text-[10px] font-semibold mx-0.5 px-1.5 py-0.5 rounded-full bg-primary/15 text-primary hover:bg-primary/25 transition align-middle"
          >
            {match[1]}
          </button>
        )
      }
    }
    return <span key={i}>{part}</span>
  })
}

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const setActiveCitation = useChatStore((s) => s.setActiveCitation)
  const isUser = message.role === 'user'

  function handleCite(idx: number) {
    if (message.citations?.[idx]) setActiveCitation(message.citations[idx])
  }

  return (
    <div className={cn('flex w-full', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[80%] rounded-lg px-4 py-2.5 text-sm leading-relaxed',
          isUser ? 'bg-primary text-primary-foreground' : 'glass'
        )}
      >
        {message.citations && message.citations.length > 0 ? (
          <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1">
            {renderWithCitations(message.content, message, handleCite)}
          </div>
        ) : (
          <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1">
            <ReactMarkdown>{message.content || (message.streaming ? '...' : '')}</ReactMarkdown>
          </div>
        )}
        {message.streaming && (
          <span className="inline-block w-1.5 h-4 ml-1 bg-current animate-pulse align-middle" />
        )}
      </div>
    </div>
  )
}
