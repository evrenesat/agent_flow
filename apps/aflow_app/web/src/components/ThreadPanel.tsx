import { useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../api'
import type { ProjectInfo, ProjectThread, ThreadTurn, ThreadTurnItem, ThreadUserInputText } from '../types'

interface ThreadPanelProps {
  project: ProjectInfo
  onSavePlanDraft: (content: string) => void
}

const TURN_POLL_INTERVAL_MS = 1000
const TURN_POLL_TIMEOUT_MS = 15000
const TERMINAL_TURN_STATUSES = new Set(['completed', 'failed', 'cancelled', 'canceled', 'aborted'])

function stringifyItem(item: ThreadTurnItem): string {
  if (typeof item === 'string') return item
  if (item === null || typeof item !== 'object') return String(item)

  const record = item as Record<string, unknown>
  const textFields = ['text', 'content', 'message', 'summary', 'preview']
  for (const field of textFields) {
    const value = record[field]
    if (typeof value === 'string' && value.trim()) {
      return value
    }
  }

  if (Array.isArray(record.items)) {
    const nested = record.items.map((nestedItem) => stringifyItem(nestedItem as ThreadTurnItem)).filter(Boolean)
    if (nested.length > 0) {
      return nested.join('\n')
    }
  }

  return JSON.stringify(item, null, 2)
}

function stringifyTurn(turn: ThreadTurn): string {
  const parts = turn.items.map((item) => stringifyItem(item)).filter((value) => value.trim().length > 0)
  if (parts.length > 0) {
    return parts.join('\n\n')
  }
  if (turn.error) {
    return JSON.stringify(turn.error, null, 2)
  }
  return ''
}

function looksLikePlanMarkdown(content: string): boolean {
  return /^#\s+.+/m.test(content) && /##\s+/.test(content)
}

function isTerminalTurnStatus(status: string | null | undefined): boolean {
  if (!status) return false
  return TERMINAL_TURN_STATUSES.has(status.toLowerCase())
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms)
  })
}

function upsertThread(threads: ProjectThread[], thread: ProjectThread): ProjectThread[] {
  const nextThreads = threads.map((existing) => (existing.id === thread.id ? thread : existing))
  if (nextThreads.some((existing) => existing.id === thread.id)) {
    return nextThreads
  }
  return [thread, ...threads]
}

function upsertTurn(thread: ProjectThread, turn: ThreadTurn): ProjectThread {
  const turns = thread.turns.map((existing) => (existing.id === turn.id ? turn : existing))
  if (turns.some((existing) => existing.id === turn.id)) {
    return { ...thread, turns }
  }
  return { ...thread, turns: [...thread.turns, turn] }
}

function getTurnStatus(thread: ProjectThread, turnId: string): string | null {
  return thread.turns.find((turn) => turn.id === turnId)?.status ?? thread.turns[thread.turns.length - 1]?.status ?? null
}

export function ThreadPanel({ project, onSavePlanDraft }: ThreadPanelProps) {
  const [threads, setThreads] = useState<ProjectThread[]>([])
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null)
  const [selectedThread, setSelectedThread] = useState<ProjectThread | null>(null)
  const [loadingThreads, setLoadingThreads] = useState(true)
  const [loadingThread, setLoadingThread] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [turnInput, setTurnInput] = useState('')
  const [sendingTurn, setSendingTurn] = useState(false)
  const [startingThread, setStartingThread] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const threadLoadRequestRef = useRef(0)
  const turnPollSessionRef = useRef(0)

  useEffect(() => {
    turnPollSessionRef.current += 1
    setSelectedThreadId(null)
    setSelectedThread(null)
    void loadThreads(null)
  }, [project.id])

  useEffect(() => {
    threadLoadRequestRef.current += 1
    turnPollSessionRef.current += 1
    setSendingTurn(false)
    if (selectedThreadId) {
      void loadThread(selectedThreadId)
    }
  }, [selectedThreadId])

  useEffect(() => {
    return () => {
      threadLoadRequestRef.current += 1
      turnPollSessionRef.current += 1
    }
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [selectedThread])

  async function loadThreads(preferredThreadId: string | null = selectedThreadId) {
    try {
      setLoadingThreads(true)
      setError(null)
      const data = await api.listProjectThreads(project.id)
      setThreads(data.threads)
      if (data.threads.length > 0) {
        const nextSelected = data.threads.find((thread) => thread.id === preferredThreadId) ?? data.threads[0]
        setSelectedThreadId(nextSelected.id)
      } else {
        setSelectedThreadId(null)
        setSelectedThread(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load threads')
    } finally {
      setLoadingThreads(false)
    }
  }

  async function loadThread(threadId: string) {
    const requestId = ++threadLoadRequestRef.current

    try {
      setLoadingThread(true)
      setError(null)
      const data = await api.getProjectThread(project.id, threadId)
      if (requestId !== threadLoadRequestRef.current) return
      setSelectedThread(data)
      setThreads((prev) => upsertThread(prev, data))
    } catch (err) {
      if (requestId !== threadLoadRequestRef.current) return
      setError(err instanceof Error ? err.message : 'Failed to load thread')
    } finally {
      if (requestId === threadLoadRequestRef.current) {
        setLoadingThread(false)
      }
    }
  }

  async function refreshThreads(preferredThreadId: string | null = selectedThreadId) {
    const data = await api.listProjectThreads(project.id)
    setThreads(data.threads)
    if (data.threads.length > 0) {
      const nextSelected = data.threads.find((thread) => thread.id === preferredThreadId) ?? data.threads[0]
      setSelectedThreadId(nextSelected.id)
    } else {
      setSelectedThreadId(null)
      setSelectedThread(null)
    }
  }

  async function handleStartThread() {
    try {
      setStartingThread(true)
      setError(null)
      const result = await api.startProjectThread(project.id, {
        cwd: project.current_path,
        persist_extended_history: true,
      })
      setThreads((prev) => [result.thread, ...prev.filter((thread) => thread.id !== result.thread.id)])
      setSelectedThreadId(result.thread.id)
      setSelectedThread(result.thread)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start thread')
    } finally {
      setStartingThread(false)
    }
  }

  async function handleResumeOrFork(action: 'resume' | 'fork') {
    if (!selectedThread) return

    try {
      setError(null)
      const request = {
        cwd: project.current_path,
        persist_extended_history: true,
      }
      const result =
        action === 'resume'
          ? await api.resumeProjectThread(project.id, selectedThread.id, request)
          : await api.forkProjectThread(project.id, selectedThread.id, request)

      setThreads((prev) => [result.thread, ...prev.filter((thread) => thread.id !== result.thread.id)])
      setSelectedThreadId(result.thread.id)
      setSelectedThread(result.thread)
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to ${action} thread`)
    }
  }

  async function handleSendTurn() {
    if (!selectedThread || !turnInput.trim()) return
    const threadId = selectedThread.id
    const sessionId = ++turnPollSessionRef.current
    let shouldRefreshThreads = false
    let nextError: Error | null = null

    try {
      setSendingTurn(true)
      setError(null)
      const input: ThreadUserInputText[] = [
        {
          type: 'text',
          text: turnInput,
          text_elements: [],
        },
      ]
      const turn = (await api.startProjectTurn(project.id, threadId, {
        input,
        cwd: project.current_path,
      })) as unknown as ThreadTurn

      if (turnPollSessionRef.current !== sessionId) {
        return
      }

      setTurnInput('')
      const updatedThread = upsertTurn(selectedThread, turn)
      setSelectedThread((prev) => (prev && prev.id === threadId ? updatedThread : prev))
      setThreads((prev) => upsertThread(prev, updatedThread))
      shouldRefreshThreads = true

      let turnSettled = isTerminalTurnStatus(turn.status)
      if (!isTerminalTurnStatus(turn.status)) {
        const deadline = Date.now() + TURN_POLL_TIMEOUT_MS
        while (Date.now() < deadline && turnPollSessionRef.current === sessionId) {
          await sleep(TURN_POLL_INTERVAL_MS)
          if (turnPollSessionRef.current !== sessionId) {
            return
          }

          const data = await api.getProjectThread(project.id, threadId)
          if (turnPollSessionRef.current !== sessionId) {
            return
          }

          setSelectedThread((prev) => (prev && prev.id === threadId ? data : prev))
          setThreads((prev) => upsertThread(prev, data))

          if (isTerminalTurnStatus(getTurnStatus(data, turn.id))) {
            turnSettled = true
            break
          }
        }

        if (turnPollSessionRef.current === sessionId && !turnSettled) {
          throw new Error('Timed out waiting for the turn to finish')
        }
      }
    } catch (err) {
      if (turnPollSessionRef.current === sessionId) {
        nextError = err instanceof Error ? err : new Error('Failed to send turn')
      }
    } finally {
      if (turnPollSessionRef.current === sessionId) {
        if (shouldRefreshThreads) {
          try {
            await refreshThreads(threadId)
          } catch (err) {
            if (!nextError) {
              nextError = err instanceof Error ? err : new Error('Failed to refresh threads')
            }
          }
        }
        setSendingTurn(false)
        if (nextError) {
          setError(nextError.message)
        }
      }
    }
  }

  const selectedThreadIsStale = useMemo(() => {
    if (!selectedThread) return false
    return selectedThread.cwd !== project.current_path
  }, [project.current_path, selectedThread])

  if (loadingThreads) {
    return (
      <div style={{ padding: 'var(--spacing-lg)', textAlign: 'center' }}>
        <div className="spinner" />
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 300px) minmax(0, 1fr)', gap: 'var(--spacing-md)', height: '100%' }}>
      <aside style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)', minWidth: 0 }}>
        <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-xs)' }}>
          <div style={{ fontWeight: 600 }}>Threads</div>
          <div className="text-xs text-dim mono truncate">{project.current_path}</div>
          <div className="text-xs text-dim">{threads.length} matched threads</div>
        </div>

        <div style={{ display: 'flex', gap: 'var(--spacing-sm)' }}>
          <button className="btn btn-primary btn-sm" onClick={() => void handleStartThread()} disabled={startingThread}>
            {startingThread ? <div className="spinner" /> : 'New thread'}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={() => void loadThreads()}>
            Refresh
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
          {threads.length === 0 ? (
            <div className="card text-dim text-sm">No threads found for this project yet.</div>
          ) : (
            threads.map((thread) => (
              <button
                key={thread.id}
                className={`card card-interactive ${selectedThreadId === thread.id ? 'selected' : ''}`}
                style={{
                  textAlign: 'left',
                  width: '100%',
                  padding: 'var(--spacing-md)',
                  border: 'none',
                  background: 'transparent',
                  borderColor: selectedThreadId === thread.id ? 'var(--color-primary)' : undefined,
                }}
                onClick={() => setSelectedThreadId(thread.id)}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--spacing-sm)' }}>
                  <div style={{ fontWeight: 600 }} className="truncate">
                    {thread.name || thread.preview || thread.id}
                  </div>
                  <span className="text-xs text-dim">{new Date(thread.updated_at).toLocaleDateString()}</span>
                </div>
                <div className="text-xs text-dim truncate">{thread.cwd}</div>
                <div className="text-xs text-dim truncate" style={{ marginTop: 'var(--spacing-xs)' }}>
                  {thread.preview || thread.source}
                </div>
              </button>
            ))
          )}
        </div>
      </aside>

      <section className="card" style={{ display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
        {error && (
          <div style={{ padding: 'var(--spacing-md)' }}>
            <div className="error-message">{error}</div>
          </div>
        )}

        {!selectedThread ? (
          <div style={{ padding: 'var(--spacing-lg)', color: 'var(--color-text-dim)' }}>
            Select a thread to read its history, or start a new one for this project.
          </div>
        ) : (
          <>
            <div
              style={{
                padding: 'var(--spacing-md)',
                borderBottom: '1px solid var(--color-border)',
                display: 'flex',
                flexWrap: 'wrap',
                justifyContent: 'space-between',
                gap: 'var(--spacing-sm)',
                alignItems: 'center',
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 600 }} className="truncate">
                  {selectedThread.name || selectedThread.preview || selectedThread.id}
                </div>
                <div className="text-xs text-dim mono truncate">{selectedThread.cwd}</div>
              </div>

              <div style={{ display: 'flex', gap: 'var(--spacing-sm)', flexWrap: 'wrap' }}>
                {selectedThreadIsStale && (
                  <>
                    <button className="btn btn-secondary btn-sm" onClick={() => void handleResumeOrFork('resume')}>
                      Resume here
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={() => void handleResumeOrFork('fork')}>
                      Fork here
                    </button>
                  </>
                )}
                <button className="btn btn-secondary btn-sm" onClick={() => void loadThread(selectedThread.id)}>
                  Reload
                </button>
              </div>
            </div>

            {selectedThreadIsStale && (
              <div style={{ padding: 'var(--spacing-md)', borderBottom: '1px solid var(--color-border)' }}>
                <div className="card" style={{ background: 'var(--color-surface-hover)' }}>
                  <div style={{ fontWeight: 600, marginBottom: 'var(--spacing-xs)' }}>This thread still points at an old path.</div>
                  <div className="text-sm text-dim mono">{selectedThread.cwd}</div>
                  <div className="text-sm text-dim" style={{ marginTop: 'var(--spacing-xs)' }}>
                    The next resume or fork will run in {project.current_path}.
                  </div>
                </div>
              </div>
            )}

            <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--spacing-md)', display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
              {loadingThread ? (
                <div style={{ padding: 'var(--spacing-lg)', textAlign: 'center' }}>
                  <div className="spinner" />
                </div>
              ) : selectedThread.turns.length === 0 ? (
                <div className="text-sm text-dim">No turn history loaded for this thread.</div>
              ) : (
                selectedThread.turns.map((turn, index) => {
                  const content = stringifyTurn(turn)
                  return (
                    <div key={turn.id} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-xs)' }}>
                      <div className="text-sm text-dim">
                        Turn {index + 1} • {turn.status}
                      </div>
                      <div className="card">
                        <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>{content || JSON.stringify(turn.items, null, 2)}</pre>
                        {content && looksLikePlanMarkdown(content) && (
                          <button
                            className="btn btn-primary btn-sm"
                            style={{ marginTop: 'var(--spacing-md)' }}
                            onClick={() => onSavePlanDraft(content)}
                          >
                            Save plan draft
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })
              )}
              <div ref={messagesEndRef} />
            </div>

            <div style={{ padding: 'var(--spacing-md)', borderTop: '1px solid var(--color-border)' }}>
              <textarea
                className="textarea"
                placeholder="Send a new turn to this thread"
                value={turnInput}
                onChange={(e) => setTurnInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    void handleSendTurn()
                  }
                }}
                style={{ minHeight: '96px' }}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--spacing-sm)', marginTop: 'var(--spacing-sm)' }}>
                <div className="text-xs text-dim">
                  Cmd/Ctrl+Enter sends the turn.
                </div>
                <button className="btn btn-primary" onClick={() => void handleSendTurn()} disabled={sendingTurn || !turnInput.trim()}>
                  {sendingTurn ? <div className="spinner" /> : 'Send turn'}
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
