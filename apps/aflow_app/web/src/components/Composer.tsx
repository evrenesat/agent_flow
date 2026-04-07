import { useState } from 'react'
import { AudioRecorder } from './AudioRecorder'

interface ComposerProps {
  onSend: (message: string) => void
  disabled?: boolean
  placeholder?: string
}

export function Composer({ onSend, disabled = false, placeholder = 'Type a message...' }: ComposerProps) {
  const [message, setMessage] = useState('')

  function handleSend() {
    if (!message.trim() || disabled) return
    onSend(message)
    setMessage('')
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSend()
    }
  }

  function handleTranscript(text: string) {
    setMessage((prev) => (prev ? `${prev} ${text}` : text))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--spacing-sm)' }}>
      <textarea
        className="textarea"
        placeholder={placeholder}
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        style={{ minHeight: '100px' }}
      />
      <div style={{ display: 'flex', gap: 'var(--spacing-sm)', alignItems: 'center' }}>
        <button className="btn btn-primary" style={{ flex: 1 }} onClick={handleSend} disabled={disabled || !message.trim()}>
          Send
        </button>
        <AudioRecorder onTranscript={handleTranscript} disabled={disabled} />
      </div>
    </div>
  )
}
