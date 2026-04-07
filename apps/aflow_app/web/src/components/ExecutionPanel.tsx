import { useState, useEffect, useRef } from 'react'
import type { ExecutionEvent, ProjectInfo } from '../types'
import * as api from '../api'

interface ExecutionPanelProps {
  project: ProjectInfo
  planPath: string
  onClose: () => void
}

export function ExecutionPanel({ project, planPath, onClose }: ExecutionPanelProps) {
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<ExecutionEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [isComplete, setIsComplete] = useState(false)
  const eventsEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    startExecution()
  }, [])

  useEffect(() => {
    if (runId) {
      const unsubscribe = api.subscribeToExecutionEvents(
        runId,
        (event) => {
          setEvents((prev) => [...prev, event])
          if (event.type === 'run_completed' || event.type === 'run_failed') {
            setIsComplete(true)
          }
        },
        (err) => {
          setError(err.message)
        }
      )
      return unsubscribe
    }
  }, [runId])

  useEffect(() => {
    eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [events])

  async function startExecution() {
    try {
      setStarting(true)
      setError(null)
      const result = await api.startExecution({
        project_id: project.id,
        plan_path: planPath,
      })
      setRunId(result.run_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start execution')
    } finally {
      setStarting(false)
    }
  }

  function formatEventType(type: string): string {
    return type
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  }

  function getEventColor(type: string): string {
    switch (type) {
      case 'run_started':
      case 'turn_started':
        return 'var(--color-primary)'
      case 'run_completed':
        return 'var(--color-success)'
      case 'run_failed':
        return 'var(--color-error)'
      default:
        return 'var(--color-text-dim)'
    }
  }

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
        <div>
          <div style={{ fontWeight: 600 }}>Execution</div>
          <div className="text-xs text-dim mono truncate">{planPath}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onClose} disabled={!isComplete && runId !== null}>
          {isComplete ? 'Close' : 'Running...'}
        </button>
      </div>

      {error && (
        <div style={{ padding: 'var(--spacing-md)' }}>
          <div className="error-message">{error}</div>
        </div>
      )}

      {starting && (
        <div style={{ padding: 'var(--spacing-lg)', textAlign: 'center' }}>
          <div className="spinner" />
          <div className="text-sm text-dim" style={{ marginTop: 'var(--spacing-md)' }}>
            Starting execution...
          </div>
        </div>
      )}

      {runId && (
        <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--spacing-md)', display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
          {events.map((event, idx) => (
            <div key={idx} className="card" style={{ borderLeft: `3px solid ${getEventColor(event.type)}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 'var(--spacing-xs)' }}>
                <div className="text-sm" style={{ fontWeight: 500 }}>
                  {formatEventType(event.type)}
                </div>
                <div className="text-xs text-dim">{new Date(event.timestamp).toLocaleTimeString()}</div>
              </div>
              {Object.keys(event.data).length > 0 && (
                <pre className="text-xs mono" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--color-text-dim)', margin: 0 }}>
                  {JSON.stringify(event.data, null, 2)}
                </pre>
              )}
            </div>
          ))}
          <div ref={eventsEndRef} />
        </div>
      )}

      {isComplete && (
        <div style={{ padding: 'var(--spacing-md)', borderTop: '1px solid var(--color-border)' }}>
          <div className="success-message">Execution completed</div>
        </div>
      )}
    </div>
  )
}
