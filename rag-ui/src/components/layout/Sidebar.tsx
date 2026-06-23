// src/components/layout/Sidebar.tsx
import { NavLink } from 'react-router-dom'
import { MessageSquare, Database, FileUp, ShieldCheck, Activity, ChevronLeft, ChevronRight } from 'lucide-react'
import { useAuthStore } from '../../store/authStore'
import { cn } from '../../lib/utils'

import { BarChart2 } from 'lucide-react'  // already imported if you have it

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
  roles: string[]
}

const NAV_ITEMS: NavItem[] = [
  { to: '/chat', label: 'Chat', icon: <MessageSquare size={22} />, roles: ['system_admin', 'domain_admin', 'contributor', 'reader'] },
  { to: '/domains', label: 'Domains', icon: <Database size={22} />, roles: ['system_admin', 'domain_admin'] },
  { to: '/documents', label: 'Documents', icon: <FileUp size={22} />, roles: ['system_admin', 'domain_admin', 'contributor'] },
  { to: '/admin', label: 'Admin', icon: <ShieldCheck size={22} />, roles: ['system_admin'] },
  { to: '/monitoring', label: 'Monitoring', icon: <Activity size={22} />, roles: ['system_admin'] },
  
  // ADD this item to the NAV_ITEMS array, after the monitoring item:
  { to: '/quality', label: 'Quality', icon: <BarChart2 size={22} />, roles: ['system_admin'] },
 
]

export default function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const roles = useAuthStore((s) => s.roles)
  const effectiveRoles = roles.length ? roles : ['reader']

  const visibleItems = NAV_ITEMS.filter((item) => item.roles.some((r) => effectiveRoles.includes(r)))

  return (
    <aside
      className={cn(
        'h-screen sticky top-0 flex flex-col border-r border-border bg-card/60 backdrop-blur-xl transition-all duration-300 shadow-lg',
        collapsed ? 'w-20' : 'w-64'
      )}
    >
      <div className="flex items-center justify-between px-5 h-16 border-b border-border bg-card/40">
        {!collapsed && (
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-primary flex items-center justify-center shadow-md shadow-primary/20">
              <ShieldCheck className="text-primary-foreground" size={18} />
            </div>
            <span className="font-bold text-base tracking-tight text-foreground bg-gradient-to-r from-primary to-primary/80 bg-clip-text text-transparent">
              RAG Platform
            </span>
          </div>
        )}
        <button 
          onClick={onToggle} 
          className="p-2 rounded-lg hover:bg-accent hover:text-accent-foreground border border-border/40 transition-colors shadow-sm ml-auto"
        >
          {collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
        </button>
      </div>

      <nav className="flex-1 p-3 space-y-2 mt-4">
        {visibleItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-4 rounded-xl px-4 py-3.5 text-sm font-semibold transition-all duration-200 border border-transparent',
                isActive 
                  ? 'bg-primary text-primary-foreground shadow-md shadow-primary/10 border-primary/20 scale-[1.02]' 
                  : 'text-muted-foreground hover:bg-accent hover:text-foreground hover:border-border/30 hover:scale-[1.01]',
                collapsed && 'justify-center px-0 py-3.5 scale-100 hover:scale-105'
              )
            }
          >
            <span className={cn('shrink-0 transition-transform duration-200')}>
              {item.icon}
            </span>
            {!collapsed && <span className="tracking-wide">{item.label}</span>}
          </NavLink>
        ))}
      </nav>
      
      {!collapsed && (
        <div className="p-4 border-t border-border bg-card/20 text-center">
          <p className="text-[10px] text-muted-foreground/60 uppercase tracking-widest font-mono">
            RAG Console v1.0.0
          </p>
        </div>
      )}
    </aside>
  )
}
