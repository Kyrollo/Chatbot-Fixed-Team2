// src/store/domainStore.ts
import { create } from 'zustand'

export interface Domain {
  id: string
  name: string
  description?: string
  status?: string
  role?: string // local role of current user in this domain
}

interface DomainState {
  domains: Domain[]
  activeDomainId: string | null
  setDomains: (domains: Domain[]) => void
  setActiveDomain: (id: string) => void
}

export const useDomainStore = create<DomainState>((set) => ({
  domains: [],
  activeDomainId: localStorage.getItem('active_domain_id'),
  setDomains: (domains) =>
    set((state) => {
      const exists = domains.some((d) => d.id === state.activeDomainId)
      const newActiveId = exists ? state.activeDomainId : (domains[0]?.id ?? null)
      if (newActiveId) {
        localStorage.setItem('active_domain_id', newActiveId)
      } else {
        localStorage.removeItem('active_domain_id')
      }
      return {
        domains,
        activeDomainId: newActiveId,
      }
    }),
  setActiveDomain: (id) => {
    localStorage.setItem('active_domain_id', id)
    set({ activeDomainId: id })
  },
}))
