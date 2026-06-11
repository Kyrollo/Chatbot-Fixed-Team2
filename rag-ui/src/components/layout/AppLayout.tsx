// src/components/layout/AppLayout.tsx
import { useEffect, useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import Sidebar from './Sidebar'
import Header from './Header'
import { domainApi } from '../../lib/api'
import { useDomainStore } from '../../store/domainStore'
import { useAuthStore } from '../../store/authStore'

export default function AppLayout() {
  const [collapsed, setCollapsed] = useState(false)
  const setDomains = useDomainStore((s) => s.setDomains)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()

  const { data, error } = useQuery({
    queryKey: ['domains'],
    queryFn: domainApi.list,
    retry: false,
  })

  useEffect(() => {
    if (error) {
      const msg = (error as Error).message ?? ''
      if (msg.includes('401') || msg.includes('403')) {
        logout()
        navigate('/login', { replace: true })
      }
    }
  }, [error, logout, navigate])

  useEffect(() => {
    if (Array.isArray(data)) setDomains(data)
  }, [data, setDomains])

  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((c) => !c)} />
      <div className="flex-1 flex flex-col min-w-0">
        <Header />
        <main className="flex-1 p-4 md:p-6 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
