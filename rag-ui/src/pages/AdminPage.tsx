// src/pages/AdminPage.tsx
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Archive, UserPlus, Trash2, Shield, User, Users, RefreshCw } from 'lucide-react'
import { domainApi, adminApi } from '../lib/api'
import { cn } from '../lib/utils'

export default function AdminPage() {
  const queryClient = useQueryClient()
  const [domainModalOpen, setDomainModalOpen] = useState(false)
  const [userModalOpen, setUserModalOpen] = useState(false)
  
  // Create Domain State
  const [domainName, setDomainName] = useState('')
  const [domainDescription, setDomainDescription] = useState('')
  
  // Create User State
  const [userId, setUserId] = useState('')
  const [userName, setUserName] = useState('')
  const [userRole, setUserRole] = useState('reader')
  const [userError, setUserError] = useState('')

  // Queries
  const { data: domains, isLoading: domainsLoading, refetch: refetchDomains } = useQuery({ 
    queryKey: ['domains'], 
    queryFn: domainApi.list 
  })
  
  const { data: users, isLoading: usersLoading, refetch: refetchUsers } = useQuery({ 
    queryKey: ['users'], 
    queryFn: adminApi.listUsers 
  })

  // Mutations
  const createDomain = useMutation({
    mutationFn: () => domainApi.create({ name: domainName, description: domainDescription }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['domains'] })
      setDomainModalOpen(false)
      setDomainName('')
      setDomainDescription('')
    },
  })

  const archiveDomain = useMutation({
    mutationFn: (id: string) => domainApi.archive(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domains'] }),
  })

  const createUser = useMutation({
    mutationFn: () => adminApi.createUser({ id: userId, name: userName, role: userRole }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      setUserModalOpen(false)
      setUserId('')
      setUserName('')
      setUserRole('reader')
      setUserError('')
    },
    onError: (err: any) => {
      setUserError(err.message || 'Failed to create user')
    }
  })

  const deleteUser = useMutation({
    mutationFn: (id: string) => adminApi.deleteUser(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  })

  function handleDeleteUser(id: string, name: string) {
    const confirmed = window.confirm(`Are you sure you want to delete user "${name}" (${id})? This will delete the user and revoke all domain memberships they hold.`)
    if (confirmed) {
      deleteUser.mutate(id)
    }
  }

  return (
    <div className="space-y-6 max-w-5xl pb-12">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground font-sans">System Administration</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Manage system domains, user directory, and global RAG settings.
        </p>
      </div>

      {/* Domain Catalog Panel */}
      <div className="glass rounded-xl p-5 border border-border">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="font-bold text-sm flex items-center gap-1.5">
              <Users size={16} className="text-primary" />
              Domain Catalog
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">Define knowledge domains and partition resources.</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => refetchDomains()}
              className="p-1.5 rounded-lg border border-border bg-card hover:bg-card text-muted-foreground hover:text-foreground transition"
              title="Refresh"
            >
              <RefreshCw size={14} className={cn(domainsLoading && 'animate-spin')} />
            </button>
            <button
              onClick={() => setDomainModalOpen(true)}
              className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-xs font-semibold hover:opacity-90 transition shadow-sm"
            >
              <Plus size={14} /> New Domain
            </button>
          </div>
        </div>

        <div className="overflow-x-auto border border-border/60 rounded-lg">
          {domainsLoading ? (
            <div className="p-8 text-center text-muted-foreground flex items-center justify-center gap-2 text-xs">
              <RefreshCw size={14} className="animate-spin text-primary" /> Loading domains...
            </div>
          ) : (domains ?? []).length === 0 ? (
            <div className="p-8 text-center text-muted-foreground text-xs">No domains configured.</div>
          ) : (
            <table className="w-full text-sm border-collapse text-left">
              <thead>
                <tr className="bg-muted/30 border-b border-border text-xs font-semibold text-muted-foreground">
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Description</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 w-16 text-right"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {(domains ?? []).map((d: any) => (
                  <tr key={d.id} className="hover:bg-muted/5">
                    <td className="px-4 py-3 font-semibold text-foreground">{d.name}</td>
                    <td className="px-4 py-3 text-muted-foreground text-xs">{d.description || '—'}</td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'text-[10px] px-2 py-0.5 rounded-full border font-semibold tracking-wide uppercase',
                          d.status === 'archived'
                            ? 'bg-muted text-muted-foreground border-border'
                            : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                        )}
                      >
                        {d.status ?? 'active'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      {d.status !== 'archived' && (
                        <button
                          onClick={() => archiveDomain.mutate(d.id)}
                          className="p-1 rounded-md hover:bg-destructive/10 text-destructive/80 hover:text-destructive transition"
                          title="Archive domain"
                        >
                          <Archive size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* User Registry Panel */}
      <div className="glass rounded-xl p-5 border border-border">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="font-bold text-sm flex items-center gap-1.5">
              <Shield size={16} className="text-primary" />
              User Registry
              <span className="ml-1.5 px-2 py-0.5 rounded-full bg-primary/10 text-primary text-[10px] font-bold">
                {(users ?? []).length} Users
              </span>
            </h3>
            <p className="text-xs text-muted-foreground mt-0.5">Manage accounts, primary roles, and system access.</p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => refetchUsers()}
              className="p-1.5 rounded-lg border border-border bg-card hover:bg-card text-muted-foreground hover:text-foreground transition"
              title="Refresh"
            >
              <RefreshCw size={14} className={cn(usersLoading && 'animate-spin')} />
            </button>
            <button
              onClick={() => setUserModalOpen(true)}
              className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-lg px-3 py-1.5 text-xs font-semibold hover:opacity-90 transition shadow-sm"
            >
              <UserPlus size={14} /> Add User
            </button>
          </div>
        </div>

        <div className="overflow-x-auto border border-border/60 rounded-lg">
          {usersLoading ? (
            <div className="p-8 text-center text-muted-foreground flex items-center justify-center gap-2 text-xs">
              <RefreshCw size={14} className="animate-spin text-primary" /> Loading users...
            </div>
          ) : (users ?? []).length === 0 ? (
            <div className="p-8 text-center text-muted-foreground text-xs">No users registered in system.</div>
          ) : (
            <table className="w-full text-sm border-collapse text-left">
              <thead>
                <tr className="bg-muted/30 border-b border-border text-xs font-semibold text-muted-foreground">
                  <th className="px-4 py-3">Username/Display Name</th>
                  <th className="px-4 py-3">User ID (Keycloak ID)</th>
                  <th className="px-4 py-3">Global Role</th>
                  <th className="px-4 py-3 w-16 text-right"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {(users ?? []).map((u: any) => (
                  <tr key={u.id} className="hover:bg-muted/5">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="p-1.5 rounded-full bg-primary/10 text-primary">
                          <User size={13} />
                        </div>
                        <span className="font-semibold text-foreground">{u.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-muted-foreground">{u.id}</td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          'text-[10px] px-2.5 py-0.5 rounded-md border font-bold tracking-wide uppercase',
                          u.role === 'system_admin'
                            ? 'bg-red-500/10 text-red-400 border-red-500/20' :
                          u.role === 'domain_admin'
                            ? 'bg-amber-500/10 text-amber-400 border-amber-500/20' :
                          u.role === 'contributor'
                            ? 'bg-sky-500/10 text-sky-400 border-sky-500/20' :
                            'bg-muted text-muted-foreground border-border'
                        )}
                      >
                        {u.role.replace('_', ' ')}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => handleDeleteUser(u.id, u.name)}
                        className="p-1 rounded-md hover:bg-destructive/10 text-destructive/80 hover:text-destructive transition"
                        title="Delete User"
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-3">
          To grant these users edit or view access on particular domains, visit the domain setup members panel.
        </p>
      </div>

      {/* Create Domain Modal */}
      {domainModalOpen && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 backdrop-blur-xs">
          <div className="bg-card border border-border rounded-xl p-5 w-full max-w-sm shadow-2xl animate-in fade-in duration-100">
            <h3 className="font-bold text-sm mb-4">Create Knowledge Domain</h3>
            
            <div className="space-y-3">
              <div>
                <label className="text-xs font-semibold mb-1 block">Domain Name</label>
                <input
                  value={domainName}
                  onChange={(e) => setDomainName(e.target.value)}
                  placeholder="e.g. Legal documents"
                  className="w-full rounded-lg border border-border bg-background/50 px-3 py-2 text-sm focus:outline-none focus:border-primary"
                />
              </div>
              <div>
                <label className="text-xs font-semibold mb-1 block">Description</label>
                <textarea
                  value={domainDescription}
                  onChange={(e) => setDomainDescription(e.target.value)}
                  placeholder="Summarize the files context..."
                  rows={3}
                  className="w-full rounded-lg border border-border bg-background/50 px-3 py-2 text-sm focus:outline-none focus:border-primary"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button 
                onClick={() => setDomainModalOpen(false)} 
                className="px-3 py-2 text-xs font-semibold rounded-lg border border-border hover:bg-muted transition"
              >
                Cancel
              </button>
              <button
                onClick={() => createDomain.mutate()}
                disabled={!domainName || createDomain.isPending}
                className="px-3 py-2 text-xs font-semibold rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition"
              >
                {createDomain.isPending ? 'Creating...' : 'Create Domain'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create User Modal */}
      {userModalOpen && (
        <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 backdrop-blur-xs">
          <div className="bg-card border border-border rounded-xl p-5 w-full max-w-sm shadow-2xl animate-in fade-in duration-100">
            <h3 className="font-bold text-sm mb-4 flex items-center gap-1.5">
              <UserPlus size={16} className="text-primary" />
              Register New User
            </h3>

            {userError && (
              <div className="text-xs text-destructive bg-destructive/10 border border-destructive/20 rounded-md p-2 mb-3">
                {userError}
              </div>
            )}
            
            <div className="space-y-3">
              <div>
                <label className="text-xs font-semibold mb-1 block">User ID (Keycloak ID / Email)</label>
                <input
                  value={userId}
                  onChange={(e) => setUserId(e.target.value)}
                  placeholder="e.g. mike123 or mike@company.com"
                  className="w-full rounded-lg border border-border bg-background/50 px-3 py-2 text-sm focus:outline-none focus:border-primary"
                />
              </div>
              <div>
                <label className="text-xs font-semibold mb-1 block">Display Name</label>
                <input
                  value={userName}
                  onChange={(e) => setUserName(e.target.value)}
                  placeholder="Mike Mina"
                  className="w-full rounded-lg border border-border bg-background/50 px-3 py-2 text-sm focus:outline-none focus:border-primary"
                />
              </div>
              <div>
                <label className="text-xs font-semibold mb-1 block">System Role</label>
                <select
                  value={userRole}
                  onChange={(e) => setUserRole(e.target.value)}
                  className="w-full rounded-lg border border-border bg-background/50 px-3 py-2 text-sm focus:outline-none focus:border-primary"
                >
                  <option value="reader">Reader (View Only)</option>
                  <option value="contributor">Contributor (Upload / Edit)</option>
                  <option value="domain_admin">Domain Admin (Manage Members)</option>
                  <option value="system_admin">System Admin (Full Power)</option>
                </select>
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button 
                onClick={() => {
                  setUserModalOpen(false)
                  setUserError('')
                }} 
                className="px-3 py-2 text-xs font-semibold rounded-lg border border-border hover:bg-muted transition"
              >
                Cancel
              </button>
              <button
                onClick={() => createUser.mutate()}
                disabled={!userId || !userName || createUser.isPending}
                className="px-3 py-2 text-xs font-semibold rounded-lg bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50 transition"
              >
                {createUser.isPending ? 'Registering...' : 'Register User'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
