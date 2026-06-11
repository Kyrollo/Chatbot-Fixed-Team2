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
    set((state) => ({
      domains,
      activeDomainId: state.activeDomainId ?? domains[0]?.id ?? null,
    })),
  setActiveDomain: (id) => {
    localStorage.setItem('active_domain_id', id)
    set({ activeDomainId: id })
  },
}))
