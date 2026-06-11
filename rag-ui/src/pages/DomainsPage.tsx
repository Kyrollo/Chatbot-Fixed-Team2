// src/pages/DomainsPage.tsx
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, UserPlus, Database } from 'lucide-react'
import { useDomainStore } from '../store/domainStore'
import { domainApi } from '../lib/api'

export default function DomainsPage() {
  const { activeDomainId, domains } = useDomainStore()
  const queryClient = useQueryClient()
  const activeDomain = domains.find((d) => d.id === activeDomainId)

  const { data: config } = useQuery({
    queryKey: ['domain-config', activeDomainId],
    queryFn: () => domainApi.getConfig(activeDomainId as string),
    enabled: !!activeDomainId,
  })

  const { data: members } = useQuery({
    queryKey: ['domain-members', activeDomainId],
    queryFn: () => domainApi.members(activeDomainId as string),
    enabled: !!activeDomainId,
  })

  const [localConfig, setLocalConfig] = useState<any>(null)
  useEffect(() => {
    if (config) setLocalConfig(config)
  }, [config])

  const saveConfig = useMutation({
    mutationFn: (data: any) => domainApi.updateConfig(activeDomainId as string, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domain-config', activeDomainId] }),
  })

  const [newUserId, setNewUserId] = useState('')
  const [newRole, setNewRole] = useState('reader')

  const addMember = useMutation({
    mutationFn: () => domainApi.addMember(activeDomainId as string, { user_id: newUserId, role: newRole }),
    onSuccess: () => {
      setNewUserId('')
      queryClient.invalidateQueries({ queryKey: ['domain-members', activeDomainId] })
    },
  })

  const updateMemberRole = useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: string }) =>
      domainApi.updateMember(activeDomainId as string, userId, { role }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domain-members', activeDomainId] }),
  })

  const removeMember = useMutation({
    mutationFn: (userId: string) => domainApi.removeMember(activeDomainId as string, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['domain-members', activeDomainId] }),
  })

  if (!activeDomainId) {
    return (
      <div className="flex flex-col items-center justify-center h-[50vh] text-center border-2 border-dashed border-border rounded-xl p-8 max-w-lg mx-auto mt-12 bg-card/25">
        <Database size={40} className="text-muted-foreground/60 mb-3 animate-pulse" />
        <h3 className="font-bold text-base text-foreground">No Knowledge Domain Selected</h3>
        <p className="text-sm text-muted-foreground mt-2 max-w-xs">
          Please select an active knowledge domain from the dropdown menu in the top navigation bar to configure RAG parameters and memberships.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="glass rounded-lg p-5">
        <h2 className="font-semibold mb-1">{activeDomain?.name}</h2>
        <p className="text-sm text-muted-foreground">{activeDomain?.description}</p>
        <span className="inline-block mt-2 text-xs px-2 py-0.5 rounded-full bg-accent text-accent-foreground border border-border">
          {activeDomain?.status ?? 'active'}
        </span>
      </div>

      {localConfig && (
        <div className="glass rounded-lg p-5 space-y-5">
          <h3 className="font-semibold text-sm">RAG Configuration</h3>

          <SliderField
            label="Confidence Threshold"
            value={localConfig.confidence_threshold}
            min={0}
            max={1}
            step={0.01}
            onChange={(v) => setLocalConfig({ ...localConfig, confidence_threshold: v })}
          />
          <SliderField
            label="Chunk Size"
            value={localConfig.chunk_size}
            min={0}
            max={8192}
            step={64}
            onChange={(v) => setLocalConfig({ ...localConfig, chunk_size: v })}
          />
          <SliderField
            label="Chunk Overlap"
            value={localConfig.chunk_overlap}
            min={0}
            max={4096}
            step={32}
            onChange={(v) => setLocalConfig({ ...localConfig, chunk_overlap: v })}
          />

          <div>
            <label className="text-sm font-medium mb-1 block">LLM Route</label>
            <select
              value={localConfig.llm_route}
              onChange={(e) => setLocalConfig({ ...localConfig, llm_route: e.target.value })}
              className="rounded-md border border-input bg-background px-3 py-2 text-sm"
            >
              <option value="api">API (Groq)</option>
              <option value="local">Local (Ollama)</option>
            </select>
          </div>

          <button
            onClick={() => saveConfig.mutate(localConfig)}
            disabled={saveConfig.isPending}
            className="bg-primary text-primary-foreground rounded-md px-4 py-2 text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {saveConfig.isPending ? 'Saving...' : 'Save Configuration'}
          </button>
        </div>
      )}

      <div className="glass rounded-lg p-5">
        <h3 className="font-semibold text-sm mb-3">Members</h3>

        <div className="flex flex-wrap gap-2 mb-4">
          <input
            value={newUserId}
            onChange={(e) => setNewUserId(e.target.value)}
            placeholder="User ID / username"
            className="flex-1 min-w-[160px] rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
          <select
            value={newRole}
            onChange={(e) => setNewRole(e.target.value)}
            className="rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="domain_admin">domain_admin</option>
            <option value="contributor">contributor</option>
            <option value="reader">reader</option>
          </select>
          <button
            onClick={() => addMember.mutate()}
            disabled={!newUserId || addMember.isPending}
            className="flex items-center gap-1.5 bg-primary text-primary-foreground rounded-md px-3 py-2 text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            <UserPlus size={14} /> Add
          </button>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted-foreground border-b border-border">
                <th className="py-2 pr-4">User</th>
                <th className="py-2 pr-4">Role</th>
                <th className="py-2 pr-4 w-10"></th>
              </tr>
            </thead>
            <tbody>
              {(members ?? []).map((m: any) => (
                <tr key={m.user_id} className="border-b border-border/50">
                  <td className="py-2 pr-4 font-mono text-xs">{m.user_id}</td>
                  <td className="py-2 pr-4">
                    <select
                      value={m.role}
                      onChange={(e) => updateMemberRole.mutate({ userId: m.user_id, role: e.target.value })}
                      className="rounded-md border border-input bg-background px-2 py-1 text-xs"
                    >
                      <option value="domain_admin">domain_admin</option>
                      <option value="contributor">contributor</option>
                      <option value="reader">reader</option>
                    </select>
                  </td>
                  <td className="py-2 pr-4">
                    <button onClick={() => removeMember.mutate(m.user_id)} className="p-1.5 rounded-md hover:bg-destructive/10 text-destructive">
                      <Trash2 size={14} />
                    </button>
                  </td>
                </tr>
              ))}
              {(!members || members.length === 0) && (
                <tr>
                  <td colSpan={3} className="py-4 text-center text-muted-foreground text-sm">
                    No members yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function SliderField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string
  value: number
  min: number
  max: number
  step: number
  onChange: (v: number) => void
}) {
  return (
    <div>
      <div className="flex justify-between text-sm font-medium mb-1">
        <span>{label}</span>
        <span className="text-muted-foreground">{value}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full accent-primary"
      />
    </div>
  )
}
