// src/App.tsx
import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuthStore } from './store/authStore'
import AppLayout from './components/layout/AppLayout'
import LoginPage from './pages/LoginPage'
import ChatPage from './pages/ChatPage'
import DomainsPage from './pages/DomainsPage'
import DocumentsPage from './pages/DocumentsPage'
import AdminPage from './pages/AdminPage'
import MonitoringPage from './pages/MonitoringPage'
import QualityPage from './pages/QualityPage'

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = useAuthStore((s) => s.token)
  if (!token) return <Navigate to="/login" replace />
  return children
}

function RequireRole({ roles, children }: { roles: string[]; children: JSX.Element }) {
  const userRoles = useAuthStore((s) => s.roles)
  const allowed = roles.some((r) => userRoles.includes(r))
  if (!allowed) return <Navigate to="/chat" replace />
  return children
}

export default function App() {
  const init = useAuthStore((s) => s.init)
  useEffect(() => {
    init()
  }, [init])

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="chat" element={<ChatPage />} />
        <Route
          path="domains"
          element={
            <RequireRole roles={['system_admin', 'domain_admin']}>
              <DomainsPage />
            </RequireRole>
          }
        />
        <Route
          path="documents"
          element={
            <RequireRole roles={['system_admin', 'domain_admin', 'contributor']}>
              <DocumentsPage />
            </RequireRole>
          }
        />
        <Route
          path="admin"
          element={
            <RequireRole roles={['system_admin']}>
              <AdminPage />
            </RequireRole>
          }
        />
        <Route
          path="monitoring"
          element={
            <RequireRole roles={['system_admin']}>
              <MonitoringPage />
            </RequireRole>
          }
        />
        <Route
          path="quality"
          element={
            <RequireRole roles={['system_admin']}>
              <QualityPage />
            </RequireRole>
          }
        />
        
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
