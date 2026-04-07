import { useEffect, useState } from 'react'
import type { ProjectInfo } from '../types'
import * as api from '../api'
import { ProjectEditor } from './ProjectEditor'

interface ProjectPickerProps {
  selectedProjectId: string | null
  onSelectProject: (project: ProjectInfo) => void
}

export function ProjectPicker({ selectedProjectId, onSelectProject }: ProjectPickerProps) {
  const [projects, setProjects] = useState<ProjectInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null)

  useEffect(() => {
    void loadProjects()
  }, [])

  async function loadProjects() {
    try {
      setLoading(true)
      setError(null)
      const data = await api.listProjects()
      setProjects(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load projects')
    } finally {
      setLoading(false)
    }
  }

  async function handleSaveProject(projectId: string, request: { display_name: string; current_path: string }) {
    const updated = await api.updateProject(projectId, request)
    setProjects((prev) => prev.map((project) => (project.id === projectId ? updated : project)))
    setEditingProjectId(null)
    onSelectProject(updated)
  }

  const selectedProject = projects.find((project) => project.id === selectedProjectId) ?? null

  if (loading) {
    return (
      <div style={{ padding: 'var(--spacing-lg)', textAlign: 'center' }}>
        <div className="spinner" />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 'var(--spacing-md)' }}>
        <div>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 600 }}>Projects</h2>
          <div className="text-sm text-dim">Detected under your configured projects root and from Codex threads.</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => void loadProjects()}>
          Refresh
        </button>
      </div>

      {error && <div className="error-message">{error}</div>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
        {projects.length === 0 ? (
          <div className="card text-dim text-sm">No projects found under the configured projects root.</div>
        ) : (
          projects.map((project) => {
            const isSelected = selectedProjectId === project.id
            const isEditing = editingProjectId === project.id
            return (
              <div
                key={project.id}
                className={`card card-interactive ${isSelected ? 'selected' : ''}`}
                style={{
                  borderColor: isSelected ? 'var(--color-primary)' : undefined,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 'var(--spacing-sm)',
                }}
              >
                <button
                  className="btn"
                  style={{
                    textAlign: 'left',
                    width: '100%',
                    padding: 0,
                    border: 'none',
                    background: 'transparent',
                    color: 'inherit',
                  }}
                  onClick={() => onSelectProject(project)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--spacing-sm)' }}>
                    <div style={{ fontWeight: 600 }}>{project.display_name}</div>
                    <span className="text-xs text-dim">{project.linked_thread_count} threads</span>
                  </div>
                  <div className="text-sm text-dim truncate">{project.current_path}</div>
                  <div className="text-xs text-dim" style={{ marginTop: 'var(--spacing-xs)' }}>
                    {project.detection_source}
                  </div>
                </button>

                <div style={{ display: 'flex', gap: 'var(--spacing-sm)', flexWrap: 'wrap' }}>
                  <button className="btn btn-secondary btn-sm" onClick={() => onSelectProject(project)}>
                    Open
                  </button>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => setEditingProjectId(isEditing ? null : project.id)}
                  >
                    {isEditing ? 'Close editor' : 'Edit project'}
                  </button>
                </div>

                {isEditing && (
                  <ProjectEditor
                    project={project}
                    onSave={(request) => handleSaveProject(project.id, request)}
                    onCancel={() => setEditingProjectId(null)}
                  />
                )}
              </div>
            )
          })
        )}
      </div>

      {selectedProject && (
        <div className="card">
          <div style={{ fontWeight: 600, marginBottom: 'var(--spacing-xs)' }}>Selected project</div>
          <div className="text-sm">{selectedProject.display_name}</div>
          <div className="text-xs text-dim mono">{selectedProject.current_path}</div>
        </div>
      )}
    </div>
  )
}
