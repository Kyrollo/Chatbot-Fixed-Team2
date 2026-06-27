// src/pages/LoginPage.tsx
import { useState, useEffect, useRef } from 'react'
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
  const [searchParams, setSearchParams] = useSearchParams()
  const codeUsed = useRef<string | null>(null)

  useEffect(() => {
    const code = searchParams.get('code')
    if (code && codeUsed.current !== code) {
      codeUsed.current = code
      // Clear the code parameter from URL immediately to prevent reuse on manual refresh
      const newParams = new URLSearchParams(searchParams)
      newParams.delete('code')
      setSearchParams(newParams, { replace: true })
      
      handleKeycloakCode(code)
    }
  }, [searchParams, setSearchParams])

  async function handleKeycloakCode(code: string) {
    setLoading(true)
    setError('')
    try {
      const res = await fetch('http://localhost:8180/realms/rag-system/protocol/openid-connect/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: 'rag-ui',
          code: code,
          redirect_uri: 'http://localhost:5173/login',
        }),
      })
      if (!res.ok) throw new Error(`Keycloak error: ${res.statusText}`)
      const data = await res.json()
      if (data.access_token) {
        setTokenStore(data.access_token)
        const decoded: any = jwtDecode(data.access_token)
        const roles = decoded.realm_access?.roles || []
        navigate(roles.includes('system_admin') ? '/admin' : roles.includes('domain_admin') ? '/domains' : '/chat')
      }
    } catch (err: any) {
      setError(err.message || 'Keycloak authentication failed.')
    } finally {
      setLoading(false)
    }
  }

  function handleKeycloakRedirect() {
    const authUrl =
      'http://localhost:8180/realms/rag-system/protocol/openid-connect/auth' +
      '?client_id=rag-ui' +
      '&redirect_uri=' + encodeURIComponent('http://localhost:5173/login') +
      '&response_type=code' +
      '&scope=openid%20profile%20email'
    window.location.href = authUrl
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault()
    if (!userId.trim()) {
      setError('Please enter your User ID or name.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const response = await domainApi.login(userId.trim())
      setTokenStore(response.token)
      const roles = response.roles || []
      navigate(
        roles.includes('system_admin') ? '/admin'
        : roles.includes('domain_admin') ? '/domains'
        : '/chat'
      )
    } catch (err: any) {
      setError(err.message || 'Login failed — check your User ID or contact your admin.')
    } finally {
      setLoading(false)
    }
  }

  const quickAccess = [
    { label: 'admin', badge: 'System Admin', value: 'admin' },
    { label: '652ec45e…', badge: 'Admin (UUID)', value: '652ec45e-1b68-478c-9bd3-81cc46fb24a9' },
    { label: 'manager', badge: 'Domain Manager', value: 'manager' },
    { label: 'contributor', badge: 'Contributor', value: 'contributor' },
    { label: 'viewer', badge: 'Reader / Viewer', value: 'viewer' },
    { label: 'd3794cbc…', badge: 'Viewer (UUID)', value: 'd3794cbc-9bb9-4c06-95e5-33603c71b287' },
  ]

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-md bg-card/40 backdrop-blur-xl border border-border rounded-xl p-8 shadow-xl">

        {/* Header */}
        <div className="flex flex-col items-center mb-8">
          <div className="h-16 w-16 rounded-full bg-primary/10 flex items-center justify-center mb-4 transition-transform hover:scale-105">
            <ShieldCheck className="text-primary" size={36} />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">RAG Enterprise Console</h1>
          <p className="text-sm text-muted-foreground mt-2 text-center max-w-[280px]">
            Sign in with your User ID or name to start a secure session
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleLogin} className="space-y-5">
          <div>
            <label className="text-sm font-medium flex items-center gap-2 mb-2 text-foreground">
              <KeyRound size={16} className="text-muted-foreground" /> User ID or Name
            </label>
            <input
              id="user-id-input"
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="e.g.  admin  ·  manager  ·  viewer  ·  652ec45e-…"
              className="w-full rounded-md border border-input bg-background/50 px-3.5 py-2.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring transition"
              disabled={loading}
              autoComplete="off"
            />
          </div>

          {error && (
            <div className="flex gap-2 items-start p-3 rounded-md bg-destructive/10 text-destructive text-sm">
              <AlertTriangle size={16} className="shrink-0 mt-0.5" />
              <p>{error}</p>
            </div>
          )}

          <button
            id="sign-in-btn"
            type="submit"
            disabled={loading}
            className="w-full bg-primary text-primary-foreground rounded-md py-2.5 font-semibold hover:opacity-90 transition shadow-lg shadow-primary/20 flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {loading ? 'Authenticating…' : 'Sign In'}
          </button>

          <div className="flex items-center my-2">
            <div className="flex-grow border-t border-border/60" />
            <span className="mx-3 text-xs text-muted-foreground uppercase font-semibold">Or</span>
            <div className="flex-grow border-t border-border/60" />
          </div>

          <button
            id="keycloak-btn"
            type="button"
            onClick={handleKeycloakRedirect}
            disabled={loading}
            className="w-full bg-card hover:bg-muted text-card-foreground border border-border rounded-md py-2.5 font-semibold transition flex items-center justify-center gap-2 disabled:opacity-50"
          >
            Sign In with Keycloak (OIDC)
          </button>
        </form>

        {/* Quick access panel */}
        <div className="mt-8 pt-6 border-t border-border/60">
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider text-center mb-3">
            Quick Access — click to fill
          </h2>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            {quickAccess.map(({ label, badge, value }) => (
              <button
                key={value}
                type="button"
                onClick={() => setUserId(value)}
                className="p-2 rounded bg-muted/50 border border-border/40 text-center hover:bg-muted/80 transition text-left"
              >
                <div className="font-semibold text-foreground truncate">{label}</div>
                <div className="text-[9px] text-muted-foreground font-sans">{badge}</div>
              </button>
            ))}
          </div>
          <p className="text-[10px] text-muted-foreground text-center mt-3">
            Accepts user name or UUID. Contact your admin if you don't have an account.
          </p>
        </div>

      </div>
    </div>
  )
}
