// src/pages/DocumentsPage.tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { UploadCloud, FileText, AlertCircle } from 'lucide-react'
import { useDomainStore } from '../store/domainStore'
import { ingestApi } from '../lib/api'
import { cn, statusColor } from '../lib/utils'

interface TrackedDoc {
  document_id: string
  filename: string
  status: string
  error_msg?: string
}

const STORAGE_KEY = 'ingested_documents'
const MAX_SIZE = 50 * 1024 * 1024

export default function DocumentsPage() {
  const { activeDomainId } = useDomainStore()
  const [docs, setDocs] = useState<TrackedDoc[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? '[]')
    } catch {
      return []
    }
  })
  const [dragOver, setDragOver] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const intervalsRef = useRef<Record<string, number>>({})

  useEffect(() => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(docs))
  }, [docs])

  const updateDoc = useCallback((id: string, patch: Partial<TrackedDoc>) => {
    setDocs((prev) => prev.map((d) => (d.document_id === id ? { ...d, ...patch } : d)))
  }, [])

  const pollStatus = useCallback(
    (id: string) => {
      const interval = window.setInterval(async () => {
        try {
          const res = await ingestApi.status(id)
          updateDoc(id, { status: res.status, error_msg: res.error_msg })
          if (res.status === 'done' || res.status === 'failed') {
            clearInterval(intervalsRef.current[id])
            delete intervalsRef.current[id]
          }
        } catch {
          /* keep polling */
        }
      }, 3000)
      intervalsRef.current[id] = interval
    },
    [updateDoc]
  )

  useEffect(() => {
    // resume polling for any docs still in-flight on mount
    docs.forEach((d) => {
      if (d.status === 'pending' || d.status === 'processing') pollStatus(d.document_id)
    })
    return () => {
      Object.values(intervalsRef.current).forEach((i) => clearInterval(i))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function handleFiles(files: FileList | null) {
    if (!files || !activeDomainId) return
    setError('')
    const allowedExts = ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv'];
    for (const file of Array.from(files)) {
      const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
      if (!allowedExts.includes(ext)) {
        setError(`${file.name}: Unsupported file type. Supported types: PDF, Word, Excel, CSV`)
        continue
      }
      if (file.size > MAX_SIZE) {
        setError(`${file.name}: exceeds 50MB limit.`)
        continue
      }
      try {
        const res = await ingestApi.upload(file, activeDomainId)
        const doc: TrackedDoc = { document_id: res.document_id, filename: file.name, status: res.status }
        setDocs((prev) => [doc, ...prev])
        pollStatus(res.document_id)
      } catch (e) {
        setError(`${file.name}: ${(e as Error).message}`)
      }
    }
  }

  if (!activeDomainId) {
    return (
      <div className="flex flex-col items-center justify-center h-[50vh] text-center border-2 border-dashed border-border rounded-xl p-8 max-w-lg mx-auto mt-12 bg-card/25">
        <UploadCloud size={40} className="text-muted-foreground/60 mb-3 animate-pulse" />
        <h3 className="font-bold text-base text-foreground">No Knowledge Domain Selected</h3>
        <p className="text-sm text-muted-foreground mt-2 max-w-xs">
          Please select an active knowledge domain from the dropdown menu in the top navigation bar to manage and upload PDF documents.
        </p>
      </div>
    )
  }

  const activeDocs = docs.filter((d) => d.status === 'pending' || d.status === 'processing')
  const historyDocs = docs.filter((d) => d.status === 'done' || d.status === 'failed')

  return (
    <div className="space-y-6 max-w-4xl">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragOver(false)
          handleFiles(e.dataTransfer.files)
        }}
        onClick={() => inputRef.current?.click()}
        className={cn(
          'glass rounded-lg border-2 border-dashed p-10 text-center cursor-pointer transition',
          dragOver ? 'border-primary bg-primary/5' : 'border-border'
        )}
      >
        <UploadCloud className="mx-auto mb-3 text-primary" size={32} />
        <p className="font-medium text-sm">Drag & drop files here, or click to browse</p>
        <p className="text-xs text-muted-foreground mt-1">PDF, Word, Excel, CSV only · max 50MB per file</p>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,.doc,.xlsx,.xls,.csv"
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded-md px-3 py-2">
          <AlertCircle size={14} /> {error}
        </div>
      )}

      <div className="glass rounded-lg p-5">
        <h3 className="font-semibold text-sm mb-3">Active Queue</h3>
        {activeDocs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No active uploads.</p>
        ) : (
          <ul className="space-y-2">
            {activeDocs.map((d) => (
              <DocRow key={d.document_id} doc={d} />
            ))}
          </ul>
        )}
      </div>

      <div className="glass rounded-lg p-5">
        <h3 className="font-semibold text-sm mb-3">Processed History (this session)</h3>
        {historyDocs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No processed documents yet.</p>
        ) : (
          <ul className="space-y-2">
            {historyDocs.map((d) => (
              <DocRow key={d.document_id} doc={d} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

function DocRow({ doc }: { doc: TrackedDoc }) {
  return (
    <li className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 group relative">
      <div className="flex items-center gap-2 min-w-0">
        <FileText size={16} className="text-muted-foreground shrink-0" />
        <span className="text-sm truncate">{doc.filename}</span>
      </div>
      <span className={cn('text-xs px-2 py-0.5 rounded-full border shrink-0', statusColor(doc.status))}>{doc.status}</span>
      {doc.status === 'failed' && doc.error_msg && (
        <div className="absolute z-10 hidden group-hover:block top-full mt-1 right-0 max-w-xs rounded-md border border-border bg-popover text-popover-foreground text-xs p-2 shadow-lg">
          {doc.error_msg}
        </div>
      )}
    </li>
  )
}
