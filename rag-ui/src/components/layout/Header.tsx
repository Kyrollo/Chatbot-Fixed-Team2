// src/components/layout/Header.tsx
import { useEffect, useState } from 'react'
import { Moon, Sun, LogOut, Activity, Database, User } from 'lucide-react'
import { useAuthStore } from '../../store/authStore'
import { useDomainStore } from '../../store/domainStore'
import { useNavigate } from 'react-router-dom'
import { healthApi } from '../../lib/api'

const SERVICES = [
  { label: 'Domain', path: '/domains' },
  { label: 'Ingest', path: '/ingest' },
  { label: 'Generate', path: '/generate' },
]

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
  }, [dark])
  return { dark, toggle: () => setDark((d) => !d) }
}

export default function Header() {
  const { dark, toggle } = useTheme()
  const { username, email, roles, logout } = useAuthStore()
  const { domains, activeDomainId, setActiveDomain } = useDomainStore()
  const navigate = useNavigate()
  const [statuses, setStatuses] = useState<Record<string, boolean>>({})

  useEffect(() => {
    let mounted = true
    async function check() {
      const results: Record<string, boolean> = {}
      for (const s of SERVICES) {
        results[s.label] = await healthApi.check(s.path)
      }
      if (mounted) setStatuses(results)
    }
    check()
    const id = setInterval(check, 30000)
    return () => {
      mounted = false
      clearInterval(id)
    }
  }, [])

  function handleLogout() {
    logout()
    navigate('/login')
  }

  return (
    <header className="h-16 border-b border-border flex items-center justify-between px-6 gap-4 bg-card/40 backdrop-blur-xl sticky top-0 z-10">
      <div className="flex items-center gap-3">
        <Database size={20} className="text-muted-foreground" />
        <select
          value={activeDomainId ?? ''}
          onChange={(e) => setActiveDomain(e.target.value)}
          className="text-sm font-semibold text-foreground border border-input bg-background/50 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring cursor-pointer hover:bg-background/80 transition"
        >
          {domains.length === 0 && <option value="">No domains</option>}
          {domains.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      </div>

      <div className="flex items-center gap-5">
        <div className="hidden md:flex items-center gap-4 border-r border-border/60 pr-5">
          {SERVICES.map((s) => (
            <div key={s.label} className="flex items-center gap-2 text-xs font-medium text-muted-foreground" title={`${s.label} service status`}>
              <span
                className={`h-2.5 w-2.5 rounded-full transition-colors duration-300 ${statuses[s.label] ? 'bg-status-success shadow-sm shadow-status-success/30' : 'bg-status-error shadow-sm shadow-status-error/30'}`}
              />
              {s.label}
            </div>
          ))}
          <Activity size={18} className="text-muted-foreground animate-pulse" />
        </div>

        <button onClick={toggle} className="p-2.5 rounded-lg hover:bg-accent hover:text-accent-foreground border border-border/30 transition shadow-sm" title="Toggle theme">
          {dark ? <Sun size={20} /> : <Moon size={20} />}
        </button>

        <div className="flex items-center gap-3">
          <div className="h-9 w-9 rounded-lg bg-muted flex items-center justify-center border border-border">
            <User size={18} className="text-foreground" />
          </div>
          <div className="text-left hidden sm:block">
            <div className="text-sm font-semibold leading-none text-foreground">{username ?? 'User'}</div>
            <div className="text-xs text-muted-foreground leading-none mt-1">{email}</div>
          </div>
          <div className="flex gap-1.5 ml-1">
            {roles.slice(0, 1).map((r) => (
              <span key={r} className="text-[10px] uppercase font-bold tracking-wider px-2 py-1 rounded bg-primary/10 text-primary border border-primary/20">
                {r.replace('_', ' ')}
              </span>
            ))}
          </div>
          <button 
            onClick={handleLogout} 
            className="p-2.5 rounded-lg hover:bg-destructive/10 hover:text-destructive text-muted-foreground border border-border/30 hover:border-destructive/20 transition shadow-sm ml-2" 
            title="Logout"
          >
            <LogOut size={20} />
          </button>
        </div>
      </div>
    </header>
  )
}
