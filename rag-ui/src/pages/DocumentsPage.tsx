// src/pages/DocumentsPage.tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  UploadCloud,
  FileText,
  AlertCircle,
  Eye,
  Trash2,
  X,
  StopCircle,
  RefreshCw,
  CheckCircle2,
  Database,
  Code,
  FileJson,
  Search,
  Check,
} from 'lucide-react'
import { useDomainStore } from '../store/domainStore'
import { useAuthStore } from '../store/authStore'
import { domainApi, ingestApi } from '../lib/api'
import { cn, statusColor } from '../lib/utils'

interface DocumentItem {
  id: string
  domain_id: string
  user_id: string
  filename: string
  status: string
  error_msg?: string | null
  created_at: string
  updated_at: string
  chunk_count: number
}

interface ChunkItem {
  id: string
  document_id: string
  domain_id: string
  page_num: number | null
  chunk_index: number
  text: string
  chunk_type: string
  source_type: string
  filename: string
  created_at: string | null
}

type ViewMode = 'formatted' | 'json' | 'raw_db' | 'plain_text' | 'markdown_preview'

export default function DocumentsPage() {
  const { domains, activeDomainId } = useDomainStore()
  const isSystemAdmin = useAuthStore((state) => state.isSystemAdmin)
  const activeDomain = domains.find((d) => d.id === activeDomainId)
  const userRole = activeDomain?.role

  const hasEditAccess = isSystemAdmin || userRole === 'domain_admin' || userRole === 'contributor'

  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  
  // Chunk Inspector State
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [inspectorDoc, setInspectorDoc] = useState<DocumentItem | null>(null)
  const [chunks, setChunks] = useState<ChunkItem[]>([])
  const [chunksLoading, setChunksLoading] = useState(false)
  const [inspectorViewMode, setInspectorViewMode] = useState<ViewMode>('formatted')
  
  const pollingIntervals = useRef<Record<string, number>>({})

  // Fetch documents list from backend
  const fetchDocuments = useCallback(async () => {
    if (!activeDomainId) return
    try {
      const docs = await domainApi.documents(activeDomainId)
      setDocuments(docs)
    } catch (err) {
      console.error('Failed to fetch documents:', err)
      setError('Could not load documents from server.')
    }
  }, [activeDomainId])

  // Initial load and refetch on domain change
  useEffect(() => {
    if (activeDomainId) {
      setError('')
      setLoading(true)
      fetchDocuments().finally(() => setLoading(false))
    }
    return () => {
      // Clear polling intervals on unmount or domain change
      Object.values(pollingIntervals.current).forEach(clearInterval)
      pollingIntervals.current = {}
    }
  }, [activeDomainId, fetchDocuments])

  // Set up polling for active documents
  const pollStatus = useCallback(
    (id: string) => {
      if (pollingIntervals.current[id]) return

      const interval = window.setInterval(async () => {
        try {
          const res = await ingestApi.status(id)
          setDocuments((prev) =>
            prev.map((doc) =>
              doc.id === id
                ? { ...doc, status: res.status, error_msg: res.error_msg }
                : doc
            )
          )
          
          if (res.status === 'done' || res.status === 'failed' || res.status === 'cancelled') {
            clearInterval(pollingIntervals.current[id])
            delete pollingIntervals.current[id]
            // Refresh documents list to update chunk counts
            fetchDocuments()
          }
        } catch (err) {
          console.error(`Error polling status for ${id}:`, err)
        }
      }, 3000)

      pollingIntervals.current[id] = interval
    },
    [fetchDocuments]
  )

  // Start polling for existing pending/processing docs
  useEffect(() => {
    documents.forEach((doc) => {
      if (doc.status === 'pending' || doc.status === 'processing') {
        pollStatus(doc.id)
      }
    })
  }, [documents, pollStatus])

  // Handle file uploads
  async function handleFiles(files: FileList | null) {
    if (!files || !activeDomainId || !hasEditAccess) return
    setError('')
    const allowedExts = ['.pdf', '.docx', '.doc', '.xlsx', '.xls', '.csv']
    
    // Add documents locally first with 'pending' status
    const uploadPromises = Array.from(files).map(async (file) => {
      const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase()
      if (!allowedExts.includes(ext)) {
        setError(`"${file.name}": Unsupported type. Supported: PDF, Word, Excel, CSV`)
        return
      }
      if (file.size > 50 * 1024 * 1024) {
        setError(`"${file.name}": Exceeds 50MB size limit.`)
        return
      }

      // Create a temporary local placeholder
      const tempId = `temp-${Math.random()}`
      const newDoc: DocumentItem = {
        id: tempId,
        domain_id: activeDomainId,
        user_id: '',
        filename: file.name,
        status: 'pending',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        chunk_count: 0
      }
      setDocuments((prev) => [newDoc, ...prev])

      try {
        const uploadRes = await ingestApi.upload(file, activeDomainId)
        
        // Update documents with real backend info
        setDocuments((prev) =>
          prev.map((doc) =>
            doc.id === tempId
              ? { ...doc, id: uploadRes.document_id, status: uploadRes.status }
              : doc
          )
        )
        
        pollStatus(uploadRes.document_id)
      } catch (err) {
        setError(`Failed to upload ${file.name}: ${(err as Error).message}`)
        setDocuments((prev) => prev.filter((doc) => doc.id !== tempId))
      }
    })

    await Promise.all(uploadPromises)
  }

  // Handle document deletion
  async function handleDelete(doc: DocumentItem) {
    if (!activeDomainId || !hasEditAccess) return
    const confirmed = window.confirm(`Are you sure you want to delete "${doc.filename}"? This will permanently remove the document and all of its ${doc.chunk_count} chunks from both PostgreSQL and Qdrant.`)
    if (!confirmed) return

    try {
      // Optimistic delete
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id))
      await domainApi.deleteDocument(activeDomainId, doc.id)
    } catch (err) {
      setError(`Failed to delete document: ${(err as Error).message}`)
      fetchDocuments()
    }
  }

  // Handle cancellation of processing
  async function handleCancel(docId: string) {
    if (!hasEditAccess) return
    try {
      await ingestApi.cancel(docId)
      setDocuments((prev) =>
        prev.map((doc) => (doc.id === docId ? { ...doc, status: 'cancelled' } : doc))
      )
      if (pollingIntervals.current[docId]) {
        clearInterval(pollingIntervals.current[docId])
        delete pollingIntervals.current[docId]
      }
    } catch (err) {
      setError(`Failed to cancel processing: ${(err as Error).message}`)
    }
  }

  // Open inspector and load chunks
  async function openInspector(doc: DocumentItem) {
    setInspectorDoc(doc)
    setInspectorOpen(true)
    setChunksLoading(true)
    setChunks([])
    try {
      if (activeDomainId) {
        const data = await domainApi.documentChunks(activeDomainId, doc.id)
        setChunks(data)
      }
    } catch (err) {
      setError(`Failed to fetch chunks: ${(err as Error).message}`)
    } finally {
      setChunksLoading(false)
    }
  }

  // Access check
  if (!activeDomainId) {
    return (
      <div className="flex flex-col items-center justify-center h-[50vh] text-center border-2 border-dashed border-border rounded-xl p-8 max-w-lg mx-auto mt-12 bg-card/25">
        <UploadCloud size={40} className="text-muted-foreground/60 mb-3 animate-pulse" />
        <h3 className="font-bold text-base text-foreground">No Knowledge Domain Selected</h3>
        <p className="text-sm text-muted-foreground mt-2 max-w-xs">
          Please select an active knowledge domain from the dropdown menu in the top navigation bar to manage and upload documents.
        </p>
      </div>
    )
  }

  if (!hasEditAccess) {
    return (
      <div className="flex flex-col items-center justify-center h-[50vh] text-center max-w-lg mx-auto mt-12">
        <AlertCircle size={40} className="text-destructive mb-3" />
        <h3 className="font-bold text-base text-foreground">Access Denied</h3>
        <p className="text-sm text-muted-foreground mt-2 max-w-xs">
          You do not have the required permissions to view or edit documents in this domain. Please contact your system or domain administrator.
        </p>
      </div>
    )
  }

  // Filter documents
  const filteredDocs = documents.filter((doc) => {
    const matchesSearch = doc.filename.toLowerCase().includes(searchQuery.toLowerCase())
    const matchesStatus = statusFilter === 'all' || doc.status === statusFilter
    return matchesSearch && matchesStatus
  })

  return (
    <div className="space-y-6 max-w-6xl pb-12">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">Documents Management</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Upload and view documents for <span className="font-semibold text-foreground">{activeDomain?.name}</span>.
          </p>
        </div>
        <button
          onClick={fetchDocuments}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-card/50 hover:bg-card text-xs transition"
        >
          <RefreshCw size={14} className={cn(loading && 'animate-spin')} /> Refresh
        </button>
      </div>

      {/* Drag & Drop Upload Zone */}
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
        onClick={() => fileInputRef.current?.click()}
        className={cn(
          'glass rounded-xl border-2 border-dashed p-8 text-center cursor-pointer transition flex flex-col items-center justify-center min-h-[160px]',
          dragOver ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/50 hover:bg-primary/5'
        )}
      >
        <div className="p-3 rounded-full bg-primary/10 text-primary mb-3">
          <UploadCloud size={24} />
        </div>
        <p className="font-semibold text-sm">Drag & drop files here, or click to browse</p>
        <p className="text-xs text-muted-foreground mt-1.5">
          PDF, Word, Excel, CSV are supported · up to 50MB per file
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.docx,.doc,.xlsx,.xls,.csv"
          multiple
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>

      {/* Error alert */}
      {error && (
        <div className="flex items-start gap-2 text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-lg p-3">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <div className="flex-1">
            <span className="font-medium">Error:</span> {error}
          </div>
          <button onClick={() => setError('')} className="text-destructive/80 hover:text-destructive shrink-0">
            <X size={16} />
          </button>
        </div>
      )}

      {/* Toolbar / Filters */}
      <div className="flex flex-col sm:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" size={16} />
          <input
            type="text"
            placeholder="Search documents by filename..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-4 py-2 text-sm bg-card/50 border border-border rounded-lg focus:outline-none focus:border-primary"
          />
        </div>
        <div className="flex gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="px-3 py-2 text-sm bg-card/50 border border-border rounded-lg focus:outline-none focus:border-primary"
          >
            <option value="all">All Statuses</option>
            <option value="pending">Pending</option>
            <option value="processing">Processing</option>
            <option value="done">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div>
      </div>

      {/* Documents List */}
      <div className="glass rounded-xl overflow-hidden border border-border">
        {loading && documents.length === 0 ? (
          <div className="p-12 text-center text-muted-foreground flex flex-col items-center justify-center gap-2">
            <RefreshCw size={24} className="animate-spin text-primary" />
            <p className="text-sm">Loading domain documents...</p>
          </div>
        ) : filteredDocs.length === 0 ? (
          <div className="p-12 text-center text-muted-foreground">
            {searchQuery || statusFilter !== 'all' ? (
              <p className="text-sm">No documents matching your search filters.</p>
            ) : (
              <p className="text-sm">No documents in this domain. Upload a file above to begin.</p>
            )}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-muted/30 border-b border-border text-xs font-semibold text-muted-foreground tracking-wider">
                  <th className="px-5 py-3.5">Filename</th>
                  <th className="px-5 py-3.5">Status</th>
                  <th className="px-5 py-3.5">Uploaded</th>
                  <th className="px-5 py-3.5">Chunks</th>
                  <th className="px-5 py-3.5 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {filteredDocs.map((doc) => {
                  const isProcessing = doc.status === 'pending' || doc.status === 'processing'
                  return (
                    <tr key={doc.id} className="hover:bg-muted/10 transition text-sm">
                      <td className="px-5 py-4 font-medium max-w-xs truncate" title={doc.filename}>
                        <div className="flex items-center gap-2">
                          <FileText size={16} className="text-muted-foreground shrink-0" />
                          <span>{doc.filename}</span>
                        </div>
                      </td>
                      <td className="px-5 py-4">
                        <span className={cn('text-xs px-2.5 py-1 rounded-full font-medium border shrink-0', statusColor(doc.status))}>
                          {doc.status}
                        </span>
                      </td>
                      <td className="px-5 py-4 text-muted-foreground text-xs">
                        {new Date(doc.created_at).toLocaleDateString()} {new Date(doc.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </td>
                      <td className="px-5 py-4 font-mono text-xs">
                        {doc.status === 'done' ? (
                          <span className="font-semibold text-foreground">{doc.chunk_count} chunks</span>
                        ) : (
                          <span className="text-muted-foreground/60">—</span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-right">
                        <div className="flex items-center justify-end gap-1.5">
                          {doc.status === 'done' && (
                            <button
                              onClick={() => openInspector(doc)}
                              className="p-1.5 rounded-md hover:bg-muted border border-transparent hover:border-border text-muted-foreground hover:text-foreground transition"
                              title="Inspect Chunks"
                            >
                              <Eye size={16} />
                            </button>
                          )}
                          
                          {isProcessing ? (
                            <button
                              onClick={() => handleCancel(doc.id)}
                              className="p-1.5 rounded-md hover:bg-destructive/10 border border-transparent hover:border-destructive/30 text-destructive/80 hover:text-destructive transition"
                              title="Cancel Processing"
                            >
                              <StopCircle size={16} />
                            </button>
                          ) : (
                            doc.id && !doc.id.startsWith('temp-') && (
                              <button
                                onClick={() => handleDelete(doc)}
                                className="p-1.5 rounded-md hover:bg-destructive/10 border border-transparent hover:border-destructive/30 text-destructive/85 hover:text-destructive transition"
                                title="Delete Document"
                              >
                                <Trash2 size={16} />
                              </button>
                            )
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Multi-View Chunk Inspector Modal */}
      {inspectorOpen && inspectorDoc && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="glass w-full max-w-5xl h-[85vh] rounded-2xl flex flex-col shadow-2xl overflow-hidden border border-border animate-in fade-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-muted/20">
              <div>
                <h3 className="font-bold text-base text-foreground flex items-center gap-2">
                  <Database size={18} className="text-primary" />
                  Chunk Inspector
                </h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Analyzing document: <span className="font-semibold text-foreground">{inspectorDoc.filename}</span>
                </p>
              </div>
              <button
                onClick={() => setInspectorOpen(false)}
                className="p-1.5 rounded-full hover:bg-muted border border-transparent hover:border-border transition"
              >
                <X size={18} />
              </button>
            </div>

            {/* View Mode Tabs */}
            <div className="flex items-center gap-1.5 px-6 py-3 border-b border-border bg-muted/10 overflow-x-auto">
              <button
                onClick={() => setInspectorViewMode('formatted')}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition shrink-0',
                  inspectorViewMode === 'formatted'
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border bg-card/50 hover:bg-card text-muted-foreground hover:text-foreground'
                )}
              >
                <CheckCircle2 size={13} /> Formatted
              </button>
              <button
                onClick={() => setInspectorViewMode('markdown_preview')}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition shrink-0',
                  inspectorViewMode === 'markdown_preview'
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border bg-card/50 hover:bg-card text-muted-foreground hover:text-foreground'
                )}
              >
                <Code size={13} /> Markdown Preview
              </button>
              <button
                onClick={() => setInspectorViewMode('plain_text')}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition shrink-0',
                  inspectorViewMode === 'plain_text'
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border bg-card/50 hover:bg-card text-muted-foreground hover:text-foreground'
                )}
              >
                <FileText size={13} /> Plain Text
              </button>
              <button
                onClick={() => setInspectorViewMode('json')}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition shrink-0',
                  inspectorViewMode === 'json'
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border bg-card/50 hover:bg-card text-muted-foreground hover:text-foreground'
                )}
              >
                <FileJson size={13} /> JSON
              </button>
              <button
                onClick={() => setInspectorViewMode('raw_db')}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold border transition shrink-0',
                  inspectorViewMode === 'raw_db'
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border bg-card/50 hover:bg-card text-muted-foreground hover:text-foreground'
                )}
              >
                <Database size={13} /> Raw DB View
              </button>
            </div>

            {/* Inspector Body */}
            <div className="flex-1 overflow-y-auto p-6 bg-card/10">
              {chunksLoading ? (
                <div className="h-full flex flex-col items-center justify-center gap-2 text-muted-foreground">
                  <RefreshCw size={24} className="animate-spin text-primary" />
                  <p className="text-sm">Fetching document chunks...</p>
                </div>
              ) : chunks.length === 0 ? (
                <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                  No chunks stored in PostgreSQL for this document.
                </div>
              ) : (
                <div className="space-y-6">
                  {inspectorViewMode === 'formatted' && (
                    <div className="space-y-4">
                      {chunks.map((c) => (
                        <div key={c.id} className="glass border border-border/80 rounded-xl overflow-hidden shadow-sm">
                          <div className="bg-muted/40 px-4 py-2.5 border-b border-border flex items-center justify-between text-xs">
                            <div className="flex items-center gap-3">
                              <span className="font-semibold text-foreground font-mono">
                                Chunk #{c.chunk_index}
                              </span>
                              <span className="text-muted-foreground/80">
                                Page: <span className="font-semibold text-foreground">{c.page_num ?? 'N/A'}</span>
                              </span>
                            </div>
                            <span className={cn(
                              'text-[10px] uppercase font-bold px-2 py-0.5 rounded border tracking-wider',
                              c.chunk_type === 'table_nl' ? 'border-sky-500/30 bg-sky-500/10 text-sky-400' :
                              c.chunk_type === 'table_md' ? 'border-amber-500/30 bg-amber-500/10 text-amber-400' :
                              'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
                            )}>
                              {c.chunk_type}
                            </span>
                          </div>
                          <div className="p-4 text-sm font-sans whitespace-pre-wrap leading-relaxed text-foreground/95 bg-card/10">
                            {c.text}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}

                  {inspectorViewMode === 'markdown_preview' && (
                    <div className="space-y-5">
                      {chunks.map((c) => {
                        const hasMDTable = c.text.includes('|') && c.text.includes('---')
                        const isNLTable = c.chunk_type === 'table_nl'
                        
                        return (
                          <div key={c.id} className="glass border border-border/80 rounded-xl p-4 space-y-3">
                            <div className="flex items-center justify-between text-xs border-b border-border/40 pb-2">
                              <span className="font-semibold text-foreground font-mono">Chunk #{c.chunk_index} (Page {c.page_num ?? 'N/A'})</span>
                              <span className="text-[10px] text-muted-foreground font-semibold px-2 py-0.5 bg-muted rounded border border-border">
                                {c.chunk_type}
                              </span>
                            </div>
                            <div className="text-sm overflow-x-auto">
                              {hasMDTable ? (
                                <MarkdownTableRenderer text={c.text} />
                              ) : isNLTable ? (
                                <NLTableRenderer text={c.text} />
                              ) : (
                                <p className="whitespace-pre-wrap leading-relaxed text-foreground/90">{c.text}</p>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}

                  {inspectorViewMode === 'plain_text' && (
                    <div className="glass border border-border rounded-xl p-4 bg-muted/20 font-mono text-xs text-foreground/90 whitespace-pre-wrap overflow-x-auto leading-relaxed">
                      {chunks.map((c) => `--- CHUNK #${c.chunk_index} (PAGE ${c.page_num ?? 'N/A'}, TYPE ${c.chunk_type}) ---\n${c.text}\n\n`).join('')}
                    </div>
                  )}

                  {inspectorViewMode === 'json' && (
                    <div className="glass border border-border rounded-xl p-4 bg-muted/20 font-mono text-xs text-foreground/90 whitespace-pre overflow-x-auto">
                      {JSON.stringify(chunks, null, 2)}
                    </div>
                  )}

                  {inspectorViewMode === 'raw_db' && (
                    <div className="glass border border-border rounded-xl overflow-hidden">
                      <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse font-mono text-xs">
                          <thead>
                            <tr className="bg-muted/40 border-b border-border text-muted-foreground font-semibold">
                              <th className="px-4 py-2 border-r border-border/60">id</th>
                              <th className="px-4 py-2 border-r border-border/60">document_id</th>
                              <th className="px-4 py-2 border-r border-border/60">domain_id</th>
                              <th className="px-4 py-2 border-r border-border/60">page_num</th>
                              <th className="px-4 py-2 border-r border-border/60">chunk_index</th>
                              <th className="px-4 py-2 border-r border-border/60">chunk_type</th>
                              <th className="px-4 py-2 border-r border-border/60">source_type</th>
                              <th className="px-4 py-2 border-r border-border/60 text-center">text (preview)</th>
                              <th className="px-4 py-2">created_at</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-border/60">
                            {chunks.map((c) => (
                              <tr key={c.id} className="hover:bg-muted/20">
                                <td className="px-4 py-2 border-r border-border/60 font-semibold text-primary truncate max-w-[80px]" title={c.id}>{c.id}</td>
                                <td className="px-4 py-2 border-r border-border/60 truncate max-w-[80px]" title={c.document_id}>{c.document_id}</td>
                                <td className="px-4 py-2 border-r border-border/60 truncate max-w-[80px]" title={c.domain_id}>{c.domain_id}</td>
                                <td className="px-4 py-2 border-r border-border/60 text-center">{c.page_num ?? 'NULL'}</td>
                                <td className="px-4 py-2 border-r border-border/60 text-center">{c.chunk_index}</td>
                                <td className="px-4 py-2 border-r border-border/60 text-center">{c.chunk_type}</td>
                                <td className="px-4 py-2 border-r border-border/60 text-center">{c.source_type}</td>
                                <td className="px-4 py-2 border-r border-border/60 max-w-[200px] truncate" title={c.text}>{c.text}</td>
                                <td className="px-4 py-2 truncate max-w-[120px] text-muted-foreground" title={c.created_at ?? ''}>{c.created_at}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
            
            {/* Modal Footer */}
            <div className="px-6 py-3 border-t border-border bg-muted/20 flex items-center justify-between text-xs text-muted-foreground">
              <span>Total chunks: <span className="font-semibold text-foreground">{chunks.length}</span></span>
              <span>Loaded successfully from database</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Renders raw markdown pipe tables into styled HTML tables
 */
function MarkdownTableRenderer({ text }: { text: string }) {
  // Extract table text between markers if they exist
  let tablePart = text
  const tableMarkers = ['[TABLE_MD]', '[/TABLE_MD]', '[TABLE]', '[/TABLE]']
  tableMarkers.forEach(m => { tablePart = tablePart.replace(m, '') })
  tablePart = tablePart.trim()

  const lines = tablePart.split('\n').map(l => l.trim()).filter(l => l)
  const rows: string[][] = []
  
  lines.forEach(line => {
    // Skip separator lines e.g. |---|---|
    if (line.match(/^\|[\s\-:|]+\|$/)) {
      return
    }
    if (line.startsWith('|') && line.endsWith('|')) {
      const cells = line.slice(1, -1).split('|').map(c => c.trim())
      rows.push(cells)
    }
  })

  if (rows.length === 0) {
    return <p className="whitespace-pre-wrap leading-relaxed text-foreground/90">{text}</p>
  }

  const headers = rows[0]
  const dataRows = rows.slice(1)

  return (
    <div className="border border-border/80 rounded-lg overflow-hidden my-2 max-w-full">
      <table className="w-full text-xs text-left border-collapse">
        <thead>
          <tr className="bg-muted border-b border-border text-foreground font-semibold">
            {headers.map((h, i) => (
              <th key={i} className="px-4 py-2 border-r border-border/40 last:border-r-0">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border/40 bg-card/10">
          {dataRows.map((row, idx) => (
            <tr key={idx} className="hover:bg-muted/10">
              {row.map((val, cellIdx) => (
                <td key={cellIdx} className="px-4 py-2 border-r border-border/40 last:border-r-0 text-foreground/90">{val}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Beautiful representation of structured NL tables
 */
function NLTableRenderer({ text }: { text: string }) {
  // Strip markers
  let clean = text
  const nlMarkers = ['[TABLE_NL]', '[/TABLE_NL]']
  nlMarkers.forEach(m => { clean = clean.replace(m, '') })
  clean = clean.trim()

  // Split into separate rows if there are multiple "Row: " blocks
  const rowBlocks = clean.split(/(?=Row: )/).map(b => b.trim()).filter(b => b)

  return (
    <div className="space-y-4">
      {rowBlocks.map((block, idx) => {
        const lines = block.split('\n').map(l => l.trim()).filter(l => l)
        let headerText = ''
        let titleText = ''
        const items: { key: string; val: string }[] = []

        lines.forEach(line => {
          if (line.startsWith('Table:')) {
            titleText = line.replace('Table:', '').trim()
          } else if (line.startsWith('Row:')) {
            headerText = line.replace('Row:', '').trim()
          } else if (line.startsWith('-')) {
            const separatorIdx = line.indexOf(':')
            if (separatorIdx !== -1) {
              const k = line.substring(1, separatorIdx).trim()
              const v = line.substring(separatorIdx + 1).trim()
              items.push({ key: k, val: v })
            }
          }
        })

        return (
          <div key={idx} className="border border-border/80 rounded-xl overflow-hidden shadow-sm bg-card/2 bg-card/4 hover:bg-card/10 transition">
            {/* Title / Heading info */}
            {titleText && (
              <div className="bg-muted/30 px-4 py-1.5 border-b border-border/40 text-[11px] font-semibold text-muted-foreground">
                {titleText}
              </div>
            )}
            
            <div className="p-4 space-y-3">
              {/* Row identifier */}
              {headerText && (
                <h4 className="font-bold text-sm text-foreground flex items-center gap-1.5">
                  <Check size={14} className="text-primary shrink-0" />
                  {headerText}
                </h4>
              )}

              {/* Cell properties */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
                {items.map((item, itemIdx) => (
                  <div key={itemIdx} className="flex justify-between border-b border-border/20 pb-1.5 last:border-b-0">
                    <span className="text-muted-foreground font-medium pr-2">{item.key}:</span>
                    <span className="text-foreground font-semibold font-mono text-right">{item.val}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
