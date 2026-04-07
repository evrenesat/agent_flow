import { useState, useEffect, useRef } from 'react'
import type { CodexSession, CodexMessage, RepoInfo } from '../types'
import * as api from '../api'

interface SessionPanelProps {
  repo: RepoInfo
  onSavePlan: (content: string) => void
}

export function SessionPanel({ repo, onSavePlan }: SessionPanelProps) {
  const [sessions, setSessions] = useState<CodexSession[]>([])
  const [selectedSession, setSelectedSession] = useState<CodexSession | null>(null)
  const [messages, setMessages] = useState<CodexMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [messageInput, setMessageInput] = useState('')
  const [sending, setSending] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    loadSessions()
  }, [repo])

  useEffect(() => {
    if (selectedSession) {
      loadMessages(selectedSession.id)
    }
  }, [selectedSession])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function loadSessions() {
    try {
      setLoading(true)
      setError(null)
      const data = await api.listCodexSessions(repo.path)
      setSessions(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load sessions')
    } finally {
      setLoading(false)
    }
  }

  async function loadMessages(sessionId: string) {
    try {
      setError(null)
      const data = await api.fetchCodexMessages(sessionId)
      setMessages(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load messages')
    }
  }

  async function handleSendMessage() {
    if (!selectedSession || !messageInput.trim()) return

    try {
      setSending(true)
      setError(null)
      const userMsg: CodexMessage = {
        id: `temp-${Date.now()}`,
        role: 'user',
        content: messageInput,
        timestamp: new Date().toISOString(),
      }
      setMessages([...messages, userMsg])
      setMessageInput('')

      const response = await api.sendCodexMessage(selectedSession.id, messageInput)
      setMessages((prev) => [...prev.filter((m) => m.id !== userMsg.id), userMsg, response])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message')
    } finally {
      setSending(false)
    }
  }

  function handleSavePlanFromMessage(content: string) {
    onSavePlan(content)
  }

  if (loading) {
    return (
      <div style={{ padding: 'var(--spacing-lg)', textAlign: 'center' }}>
        <div className="spinner" />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {error && (
        <div style={{ padding: 'var(--spacing-md)' }}>
          <div className="error-message">{error}</div>
        </div>
      )}

      {!selectedSession ? (
        <div style={{ padding: 'var(--spacing-md)', display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
          <h3 style={{ fontSize: '1.125rem', fontWeight: 600 }}>Codex Sessions</h3>
          {sessions.length === 0 ? (
            <div className="card text-dim text-sm">No sessions found for this repo</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
              {sessions.map((session) => (
                <div key={session.id} className="card card-interactive" onClick={() => setSelectedSession(session)}>
                  <div style={{ fontWeight: 500 }}>{session.name}</div>
                  <div className="text-sm text-dim">{new Date(session.updated_at).toLocaleString()}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <>
          <div
            style={{
              padding: 'var(--spacing-md)',
              borderBottom: '1px solid var(--color-border)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <button className="btn btn-secondary btn-sm" onClick={() => setSelectedSession(null)}>
              ← Back
            </button>
            <div className="text-sm truncate" style={{ flex: 1, marginLeft: 'var(--spacing-md)' }}>
              {selectedSession.name}
            </div>
          </div>

          <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--spacing-md)', display: 'flex', flexDirection: 'column', gap: 'var(--spacing-md)' }}>
            {messages.map((msg) => (
              <div key={msg.id} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-xs)' }}>
                <div className="text-sm text-dim">{msg.role === 'user' ? 'You' : 'Assistant'}</div>
                <div
                  className="card"
                  style={{
                    background: msg.role === 'user' ? 'var(--color-surface)' : 'var(--color-surface-hover)',
                  }}
                >
                  <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>{msg.content}</pre>
                  {msg.role === 'assistant' && msg.content.includes('# ') && (
                    <button
                      className="btn btn-primary btn-sm"
                      style={{ marginTop: 'var(--spacing-md)' }}
                      onClick={() => handleSavePlanFromMessage(msg.content)}
                    >
                      Save as Plan Draft
                    </button>
                  )}
                </div>
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>

          <div style={{ padding: 'var(--spacing-md)', borderTop: '1px solid var(--color-border)' }}>
            <div style={{ display: 'flex', gap: 'var(--spacing-sm)' }}>
              <textarea
                className="textarea"
                placeholder="Type your message..."
                value={messageInput}
                onChange={(e) => setMessageInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    handleSendMessage()
                  }
                }}
                style={{ minHeight: '80px' }}
              />
            </div>
            <button
              className="btn btn-primary"
              style={{ marginTop: 'var(--spacing-sm)', width: '100%' }}
              onClick={handleSendMessage}
              disabled={sending || !messageInput.trim()}
            >
              {sending ? <div className="spinner" /> : 'Send'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
