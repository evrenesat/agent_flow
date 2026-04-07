import { useState, useEffect } from 'react'
import type { RepoInfo } from './types'
import { RepoPicker } from './components/RepoPicker'
import { SessionPanel } from './components/SessionPanel'
import { PlanPanel } from './components/PlanPanel'
import { ExecutionPanel } from './components/ExecutionPanel'
import * as api from './api'

type View = 'repos' | 'sessions' | 'plans' | 'execution'

export function App() {
  const [authToken, setAuthTokenState] = useState('')
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [selectedRepo, setSelectedRepo] = useState<RepoInfo | null>(null)
  const [currentView, setCurrentView] = useState<View>('repos')
  const [executionPlanPath, setExecutionPlanPath] = useState<string | null>(null)
  const [savingPlan, setSavingPlan] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    const token = api.getAuthToken()
    if (token) {
      setAuthTokenState(token)
      setIsAuthenticated(true)
    }
  }, [])

  function handleLogin() {
    if (!authToken.trim()) return
    api.setAuthToken(authToken)
    setIsAuthenticated(true)
  }

  function handleLogout() {
    api.clearAuthToken()
    setIsAuthenticated(false)
    setAuthTokenState('')
    setSelectedRepo(null)
    setCurrentView('repos')
  }

  function handleSelectRepo(repo: RepoInfo) {
    setSelectedRepo(repo)
    setCurrentView('sessions')
  }

  async function handleSavePlan(content: string) {
    if (!selectedRepo) return

    try {
      setSavingPlan(true)
      setSaveError(null)
      const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5)
      const filename = `plan-${timestamp}.md`
      await api.savePlanDraft(selectedRepo.id, filename, content)
      setCurrentView('plans')
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save plan')
    } finally {
      setSavingPlan(false)
    }
  }

  function handleStartExecution(planPath: string) {
    setExecutionPlanPath(planPath)
    setCurrentView('execution')
  }

  function handleCloseExecution() {
    setExecutionPlanPath(null)
    setCurrentView('plans')
  }

  if (!isAuthenticated) {
    return (
      <div
        style={{
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 'var(--spacing-lg)',
        }}
      >
        <div className="card" style={{ maxWidth: '400px', width: '100%' }}>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 600, marginBottom: 'var(--spacing-lg)' }}>aflow Remote</h1>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
            <input
              className="input"
              type="password"
              placeholder="Auth token"
              value={authToken}
              onChange={(e) => setAuthTokenState(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
            />
            <button className="btn btn-primary" onClick={handleLogin} disabled={!authToken.trim()}>
              Login
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <header
        style={{
          padding: 'var(--spacing-md)',
          borderBottom: '1px solid var(--color-border)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--spacing-md)' }}>
          <h1 style={{ fontSize: '1.25rem', fontWeight: 600 }}>aflow</h1>
          {selectedRepo && (
            <>
              <span className="text-dim">•</span>
              <span className="text-sm truncate" style={{ maxWidth: '200px' }}>
                {selectedRepo.name}
              </span>
            </>
          )}
        </div>
        <button className="btn btn-secondary btn-sm" onClick={handleLogout}>
          Logout
        </button>
      </header>

      {selectedRepo && (
        <nav
          style={{
            display: 'flex',
            borderBottom: '1px solid var(--color-border)',
            overflowX: 'auto',
          }}
        >
          <button
            className="btn"
            style={{
              borderRadius: 0,
              borderBottom: currentView === 'repos' ? '2px solid var(--color-primary)' : '2px solid transparent',
              color: currentView === 'repos' ? 'var(--color-primary)' : 'var(--color-text-dim)',
            }}
            onClick={() => setCurrentView('repos')}
          >
            Repos
          </button>
          <button
            className="btn"
            style={{
              borderRadius: 0,
              borderBottom: currentView === 'sessions' ? '2px solid var(--color-primary)' : '2px solid transparent',
              color: currentView === 'sessions' ? 'var(--color-primary)' : 'var(--color-text-dim)',
            }}
            onClick={() => setCurrentView('sessions')}
          >
            Sessions
          </button>
          <button
            className="btn"
            style={{
              borderRadius: 0,
              borderBottom: currentView === 'plans' ? '2px solid var(--color-primary)' : '2px solid transparent',
              color: currentView === 'plans' ? 'var(--color-primary)' : 'var(--color-text-dim)',
            }}
            onClick={() => setCurrentView('plans')}
          >
            Plans
          </button>
        </nav>
      )}

      <main style={{ flex: 1, overflow: 'hidden' }}>
        {currentView === 'repos' && <RepoPicker selectedRepoId={selectedRepo?.id || null} onSelectRepo={handleSelectRepo} />}
        {currentView === 'sessions' && selectedRepo && <SessionPanel repo={selectedRepo} onSavePlan={handleSavePlan} />}
        {currentView === 'plans' && selectedRepo && <PlanPanel repo={selectedRepo} onStartExecution={handleStartExecution} />}
        {currentView === 'execution' && selectedRepo && executionPlanPath && (
          <ExecutionPanel repo={selectedRepo} planPath={executionPlanPath} onClose={handleCloseExecution} />
        )}
      </main>

      {savingPlan && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            background: 'rgba(0, 0, 0, 0.8)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div className="card">
            <div className="spinner" />
            <div className="text-sm text-dim" style={{ marginTop: 'var(--spacing-md)' }}>
              Saving plan draft...
            </div>
          </div>
        </div>
      )}

      {saveError && (
        <div style={{ position: 'fixed', bottom: 'var(--spacing-md)', left: 'var(--spacing-md)', right: 'var(--spacing-md)' }}>
          <div className="error-message">{saveError}</div>
        </div>
      )}
    </div>
  )
}
