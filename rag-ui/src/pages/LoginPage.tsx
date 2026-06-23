// src/pages/LoginPage.tsx
import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuthStore } from '../store/authStore'
import { ShieldCheck, KeyRound, AlertTriangle } from 'lucide-react'
import { domainApi } from '../lib/api'
import { jwtDecode } from 'jwt-decode'

export default function LoginPage() {
  const [userId, setUserId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const setTokenStore = useAuthStore((s) => s.setToken)
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()

  useEffect(() => {
    const code = searchParams.get('code')
    if (code) {
      handleKeycloakCode(code)
    }
  }, [searchParams])

  async function handleKeycloakCode(code: string) {
    setLoading(true)
    setError('')
    try {
      const res = await fetch('http://localhost:8180/realms/rag-system/protocol/openid-connect/token', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: 'rag-ui',
          code: code,
          redirect_uri: 'http://localhost:5173/login',
        }),
      })

      if (!res.ok) {
        throw new Error(`Keycloak error: ${res.statusText}`)
      }

      const data = await res.json()
      if (data.access_token) {
        setTokenStore(data.access_token)
        const decoded: any = jwtDecode(data.access_token)
        const roles = decoded.realm_access?.roles || []
        if (roles.includes('system_admin')) {
          navigate('/admin')
        } else if (roles.includes('domain_admin')) {
          navigate('/domains')
        } else {
          navigate('/chat')
        }
      }
    } catch (err: any) {
      console.error(err)
      setError(err.message || 'Keycloak authentication failed.')
    } finally {
      setLoading(false)
    }
  }

  function handleKeycloakRedirect() {
    const authUrl = 'http://localhost:8180/realms/rag-system/protocol/openid-connect/auth' + 
      '?client_id=rag-ui' +
      '&redirect_uri=' + encodeURIComponent('http://localhost:5173/login') +
      '&response_type=code' +
      '&scope=openid%20profile%20email'
    window.location.href = authUrl
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault()
    if (!userId.trim()) {
      setError('Please enter your unique User ID.')
      return
    }

    setLoading(true)
    setError('')

    try {
      const response = await domainApi.login(userId.trim())
      setTokenStore(response.token)
      
      const roles = response.roles || []
      if (roles.includes('system_admin')) {
        navigate('/admin')
      } else if (roles.includes('domain_admin')) {
        navigate('/domains')
      } else if (roles.includes('contributor')) {
        navigate('/chat')
      } else if (roles.includes('reader')) {
        navigate('/chat')
      } else {
        navigate('/chat')
      }
    } catch (err: any) {
      console.error(err)
      setError(err.message || 'Invalid User ID. Please check your credentials.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-md bg-card/40 backdrop-blur-xl border border-border rounded-xl p-8 shadow-xl">
        <div className="flex flex-col items-center mb-8">
          <div className="h-16 w-16 rounded-full bg-primary/10 flex items-center justify-center mb-4 transition-transform hover:scale-105">
            <ShieldCheck className="text-primary" size={36} />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">RAG Enterprise Console</h1>
          <p className="text-sm text-muted-foreground mt-2 text-center max-w-[280px]">
            Enter your unique User ID to authenticate secure session
          </p>
        </div>

        <form onSubmit={handleLogin} className="space-y-5">
          <div>
            <label className="text-sm font-medium flex items-center gap-2 mb-2 text-foreground">
              <KeyRound size={16} className="text-muted-foreground" /> Unique User ID
            </label>
            <input
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="e.g. admin, manager, user, viewer"
              className="w-full rounded-md border border-input bg-background/50 px-3.5 py-2.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring transition"
              disabled={loading}
            />
          </div>

          {error && (
            <div className="flex gap-2 items-center p-3 rounded-md bg-destructive/10 text-destructive text-sm">
              <AlertTriangle size={16} className="shrink-0" />
              <p>{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-primary text-primary-foreground rounded-md py-2.5 font-semibold hover:opacity-90 transition shadow-lg shadow-primary/20 flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {loading ? 'Authenticating...' : 'Sign In'}
          </button>

          <div className="flex items-center my-4">
            <div className="flex-grow border-t border-border/60"></div>
            <span className="mx-3 text-xs text-muted-foreground uppercase font-semibold">Or</span>
            <div className="flex-grow border-t border-border/60"></div>
          </div>

          <button
            type="button"
            onClick={handleKeycloakRedirect}
            disabled={loading}
            className="w-full bg-card hover:bg-muted text-card-foreground border border-border rounded-md py-2.5 font-semibold transition flex items-center justify-center gap-2 disabled:opacity-50"
          >
            Sign In with Keycloak (OIDC)
          </button>
        </form>

        <div className="mt-8 pt-6 border-t border-border/60">
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider text-center mb-3">
            Quick Reference IDs
          </h2>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            <div className="p-2 rounded bg-muted/50 border border-border/40 text-center cursor-pointer hover:bg-muted/80 transition" onClick={() => setUserId('admin')}>
              <div className="font-semibold text-foreground">admin</div>
              <div className="text-[10px] text-muted-foreground">System Admin</div>
            </div>
            <div className="p-2 rounded bg-muted/50 border border-border/40 text-center cursor-pointer hover:bg-muted/80 transition" onClick={() => setUserId('manager')}>
              <div className="font-semibold text-foreground">manager</div>
              <div className="text-[10px] text-muted-foreground">Domain Manager</div>
            </div>
            <div className="p-2 rounded bg-muted/50 border border-border/40 text-center cursor-pointer hover:bg-muted/80 transition" onClick={() => setUserId('user')}>
              <div className="font-semibold text-foreground">user</div>
              <div className="text-[10px] text-muted-foreground">Regular User</div>
            </div>
            <div className="p-2 rounded bg-muted/50 border border-border/40 text-center cursor-pointer hover:bg-muted/80 transition" onClick={() => setUserId('viewer')}>
              <div className="font-semibold text-foreground">viewer</div>
              <div className="text-[10px] text-muted-foreground">Viewer / Reader</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
