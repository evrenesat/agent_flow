import { useState, useEffect } from 'react'
import type { PlanInfo, RepoInfo } from '../types'
import * as api from '../api'

interface PlanPanelProps {
  repo: RepoInfo
  onStartExecution: (planPath: string) => void
}

export function PlanPanel({ repo, onStartExecution }: PlanPanelProps) {
  const [plans, setPlans] = useState<PlanInfo[]>([])
  const [drafts, setDrafts] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedDraft, setSelectedDraft] = useState<string | null>(null)
  const [draftContent, setDraftContent] = useState('')
  const [viewingDraft, setViewingDraft] = useState(false)

  useEffect(() => {
    loadPlans()
    loadDrafts()
  }, [repo])

  async function loadPlans() {
    try {
      setLoading(true)
      setError(null)
      const data = await api.listPlans(repo.id)
      setPlans(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load plans')
    } finally {
      setLoading(false)
    }
  }

  async function loadDrafts() {
    try {
      const data = await api.listPlanDrafts(repo.id)
      setDrafts(data)
    } catch (err) {
      console.error('Failed to load drafts:', err)
    }
  }

  async function handleViewDraft(filename: string) {
    try {
      setError(null)
      const content = await api.loadPlanDraft(repo.id, filename)
      setDraftContent(content)
      setSelectedDraft(filename)
      setViewingDraft(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load draft')
    }
  }

  async function handlePromoteDraft() {
    if (!selectedDraft) return

    try {
      setError(null)
      await api.promotePlanDraft(repo.id, selectedDraft)
      setViewingDraft(false)
      setSelectedDraft(null)
      setDraftContent('')
      await loadPlans()
      await loadDrafts()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to promote draft')
    }
  }

  async function handleDeleteDraft() {
    if (!selectedDraft) return

    try {
      setError(null)
      await api.deletePlanDraft(repo.id, selectedDraft)
      setViewingDraft(false)
      setSelectedDraft(null)
      setDraftContent('')
      await loadDrafts()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete draft')
    }
  }

  if (loading) {
    return (
      <div style={{ padding: 'var(--spacing-lg)', textAlign: 'center' }}>
        <div className="spinner" />
      </div>
    )
  }

  if (viewingDraft && selectedDraft) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div
          style={{
            padding: 'var(--spacing-md)',
            borderBottom: '1px solid var(--color-border)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <button className="btn btn-secondary btn-sm" onClick={() => setViewingDraft(false)}>
            ← Back
          </button>
          <div className="text-sm truncate" style={{ flex: 1, marginLeft: 'var(--spacing-md)' }}>
            {selectedDraft}
          </div>
        </div>

        {error && (
          <div style={{ padding: 'var(--spacing-md)' }}>
            <div className="error-message">{error}</div>
          </div>
        )}

        <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--spacing-md)' }}>
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '0.875rem' }}>{draftContent}</pre>
        </div>

        <div style={{ padding: 'var(--spacing-md)', borderTop: '1px solid var(--color-border)', display: 'flex', gap: 'var(--spacing-sm)' }}>
          <button className="btn btn-primary" style={{ flex: 1 }} onClick={handlePromoteDraft}>
            Promote to In-Progress
          </button>
          <button className="btn btn-danger" onClick={handleDeleteDraft}>
            Delete
          </button>
        </div>
      </div>
    )
  }

  return (
    <div style={{ padding: 'var(--spacing-md)', display: 'flex', flexDirection: 'column', gap: 'var(--spacing-lg)' }}>
      {error && <div className="error-message">{error}</div>}

      <div>
        <h3 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: 'var(--spacing-md)' }}>Plan Drafts</h3>
        {drafts.length === 0 ? (
          <div className="card text-dim text-sm">No drafts</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
            {drafts.map((draft) => (
              <div key={draft} className="card card-interactive" onClick={() => handleViewDraft(draft)}>
                <div className="mono text-sm">{draft}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <h3 style={{ fontSize: '1.125rem', fontWeight: 600, marginBottom: 'var(--spacing-md)' }}>In-Progress Plans</h3>
        {plans.filter((p) => p.status === 'in_progress').length === 0 ? (
          <div className="card text-dim text-sm">No in-progress plans</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
            {plans
              .filter((p) => p.status === 'in_progress')
              .map((plan) => (
                <div key={plan.path} className="card">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--spacing-sm)' }}>
                    <div className="mono text-sm" style={{ fontWeight: 500 }}>
                      {plan.name}
                    </div>
                    {plan.is_complete && <span className="text-xs" style={{ color: 'var(--color-success)' }}>✓ Complete</span>}
                  </div>
                  <div className="text-xs text-dim" style={{ marginBottom: 'var(--spacing-md)' }}>
                    {plan.checkpoint_count} checkpoints • {plan.unchecked_count} remaining
                  </div>
                  <button className="btn btn-primary btn-sm" onClick={() => onStartExecution(plan.path)}>
                    Start Execution
                  </button>
                </div>
              ))}
          </div>
        )}
      </div>
    </div>
  )
}
