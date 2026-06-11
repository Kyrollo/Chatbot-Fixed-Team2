// src/pages/AdminPage.tsx
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Archive } from 'lucide-react'
import { domainApi } from '../lib/api'
import { cn } from '../lib/utils'

// Mock user registry (no global user-list endpoint exists)
const MOCK_USERS = [
  { id: 'u-admin', username: 'admin', email: 'admin@example.com' },
  { id: 'u-reader1', username: 'reader1', email: 'reader1@example.com' },
  { id: 'u-contrib1', username: 'contrib1', email: 'contrib1@example.com' },
]

export default function AdminPage() {
  const queryClient = useQueryClient()
  const { data: domains } = useQuery({ queryKey: ['domains'], queryFn: domainApi.list })
  const [modalOpen, setModalOpen] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  const createDomain = useMutation({
    mutationFn: () => domainApi.create({ name, description }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['domains'] })
      setModalOpen(false)
      setName('')
      setDescription('')
    },
  })

  const archiveDomain = useMutation({
    mutationFn: (id: string) => domainApi.archive(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domains'] }),
  })

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="glass rounded-lg p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-sm">Domain Catalog</h3>
          <button
            onClick={() => setModalOpen(true)}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-md px-3 py-2 text-sm font-medium hover:opacity-90 transition"
          >
            <Plus size={14} /> New Domain
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground border-b border-border">
                <th className="py-2 pr-4">Name</th>
                <th className="py-2 pr-4">Description</th>
                <th className="py-2 pr-4">Status</th>
                <th className="py-2 pr-4 w-10"></th>
              </tr>
            </thead>
            <tbody>
              {(domains ?? []).map((d: any) => (
                <tr key={d.id} className="border-b border-border/50">
                  <td className="py-2 pr-4 font-medium">{d.name}</td>
                  <td className="py-2 pr-4 text-muted-foreground">{d.description}</td>
                  <td className="py-2 pr-4">
                    <span
                      className={cn(
                        'text-xs px-2 py-0.5 rounded-full border',
                        d.status === 'archived'
                          ? 'bg-muted text-muted-foreground border-border'
                          : 'bg-status-success/15 text-status-success border-status-success/30'
                      )}
                    >
                      {d.status ?? 'active'}
                    </span>
                  </td>
                  <td className="py-2 pr-4">
                    {d.status !== 'archived' && (
                      <button onClick={() => archiveDomain.mutate(d.id)} className="p-1.5 rounded-md hover:bg-destructive/10 text-destructive" title="Archive domain">
                        <Archive size={14} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="glass rounded-lg p-5">
        <h3 className="font-semibold text-sm mb-3">Keycloak User Registry (mock)</h3>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-muted-foreground border-b border-border">
              <th className="py-2 pr-4">Username</th>
              <th className="py-2 pr-4">Email</th>
              <th className="py-2 pr-4">User ID</th>
            </tr>
          </thead>
          <tbody>
            {MOCK_USERS.map((u) => (
              <tr key={u.id} className="border-b border-border/50">
                <td className="py-2 pr-4 font-medium">{u.username}</td>
                <td className="py-2 pr-4 text-muted-foreground">{u.email}</td>
                <td className="py-2 pr-4 font-mono text-xs">{u.id}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-muted-foreground mt-3">
          To assign these users to a domain, go to the Domains page and add them by User ID with a role.
        </p>
      </div>

      {modalOpen && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/30">
          <div className="bg-card border border-border rounded-lg p-5 w-full max-w-sm shadow-xl">
            <h3 className="font-semibold text-sm mb-4">Create Domain</h3>
            <label className="text-sm font-medium mb-1 block">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm mb-3"
            />
            <label className="text-sm font-medium mb-1 block">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm mb-4"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setModalOpen(false)} className="px-3 py-2 text-sm rounded-md border border-border hover:bg-accent">
                Cancel
              </button>
              <button
                onClick={() => createDomain.mutate()}
                disabled={!name || createDomain.isPending}
                className="px-3 py-2 text-sm rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
              >
                {createDomain.isPending ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
