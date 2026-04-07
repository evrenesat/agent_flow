import { useState, useEffect } from 'react'
import type { RepoInfo } from '../types'
import * as api from '../api'

interface RepoPickerProps {
  selectedRepoId: string | null
  onSelectRepo: (repo: RepoInfo) => void
}

export function RepoPicker({ selectedRepoId, onSelectRepo }: RepoPickerProps) {
  const [repos, setRepos] = useState<RepoInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [newRepoPath, setNewRepoPath] = useState('')
  const [newRepoName, setNewRepoName] = useState('')
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    loadRepos()
  }, [])

  async function loadRepos() {
    try {
      setLoading(true)
      setError(null)
      const data = await api.listRepos()
      setRepos(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load repos')
    } finally {
      setLoading(false)
    }
  }

  async function handleAddRepo() {
    if (!newRepoPath.trim()) return

    try {
      setAdding(true)
      setError(null)
      const repo = await api.addRepo(newRepoPath, newRepoName || undefined)
      setRepos([...repos, repo])
      setNewRepoPath('')
      setNewRepoName('')
      setShowAddForm(false)
      onSelectRepo(repo)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add repo')
    } finally {
      setAdding(false)
    }
  }

  if (loading) {
    return (
      <div style={{ padding: 'var(--spacing-lg)', textAlign: 'center' }}>
        <div className="spinner" />
      </div>
    )
  }

  return (
    <div style={{ padding: 'var(--spacing-md)', display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ fontSize: '1.25rem', fontWeight: 600 }}>Repositories</h2>
        <button className="btn btn-primary btn-sm" onClick={() => setShowAddForm(!showAddForm)}>
          {showAddForm ? 'Cancel' : 'Add Repo'}
        </button>
      </div>

      {error && <div className="error-message">{error}</div>}

      {showAddForm && (
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
          <input
            className="input"
            type="text"
            placeholder="Repository path"
            value={newRepoPath}
            onChange={(e) => setNewRepoPath(e.target.value)}
          />
          <input
            className="input"
            type="text"
            placeholder="Name (optional)"
            value={newRepoName}
            onChange={(e) => setNewRepoName(e.target.value)}
          />
          <button className="btn btn-primary" onClick={handleAddRepo} disabled={adding || !newRepoPath.trim()}>
            {adding ? <div className="spinner" /> : 'Add Repository'}
          </button>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
        {repos.length === 0 ? (
          <div className="card text-dim text-sm">No repositories registered</div>
        ) : (
          repos.map((repo) => (
            <div
              key={repo.id}
              className={`card card-interactive ${selectedRepoId === repo.id ? 'selected' : ''}`}
              onClick={() => onSelectRepo(repo)}
              style={{
                borderColor: selectedRepoId === repo.id ? 'var(--color-primary)' : undefined,
              }}
            >
              <div style={{ fontWeight: 500 }}>{repo.name}</div>
              <div className="text-sm text-dim truncate">{repo.path}</div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
