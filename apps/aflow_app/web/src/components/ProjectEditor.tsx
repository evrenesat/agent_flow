import { useEffect, useState } from 'react'
import type { ProjectInfo } from '../types'

interface ProjectEditorProps {
  project: ProjectInfo
  onSave: (request: { display_name: string; current_path: string }) => Promise<void>
  onCancel: () => void
}

export function ProjectEditor({ project, onSave, onCancel }: ProjectEditorProps) {
  const [displayName, setDisplayName] = useState(project.display_name)
  const [currentPath, setCurrentPath] = useState(project.current_path)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDisplayName(project.display_name)
    setCurrentPath(project.current_path)
    setError(null)
  }, [project])

  async function handleSave() {
    if (!displayName.trim() || !currentPath.trim()) return

    try {
      setSaving(true)
      setError(null)
      await onSave({
        display_name: displayName.trim(),
        current_path: currentPath.trim(),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save project')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
      <div>
        <div style={{ fontWeight: 600 }}>{project.display_name}</div>
        <div className="text-xs text-dim mono">{project.current_path}</div>
      </div>

      {error && <div className="error-message">{error}</div>}

      <label style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-xs)' }}>
        <span className="text-xs text-dim">Display name</span>
        <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </label>

      <label style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-xs)' }}>
        <span className="text-xs text-dim">Current path</span>
        <input className="input mono" value={currentPath} onChange={(e) => setCurrentPath(e.target.value)} />
      </label>

      <div style={{ display: 'flex', gap: 'var(--spacing-sm)' }}>
        <button className="btn btn-primary" onClick={handleSave} disabled={saving || !displayName.trim() || !currentPath.trim()}>
          {saving ? <div className="spinner" /> : 'Save project'}
        </button>
        <button className="btn btn-secondary" onClick={onCancel} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  )
}
