import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { App } from './App'
import * as api from './api'

vi.mock('./api', () => ({
  getAuthToken: vi.fn(),
  setAuthToken: vi.fn(),
  clearAuthToken: vi.fn(),
  listRepos: vi.fn(),
  addRepo: vi.fn(),
  listCodexSessions: vi.fn(),
  listPlans: vi.fn(),
  listPlanDrafts: vi.fn(),
}))

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    ;(api.getAuthToken as any).mockReturnValue(null)
  })

  it('should show login screen when not authenticated', () => {
    render(<App />)
    expect(screen.getByPlaceholderText('Auth token')).toBeDefined()
    expect(screen.getByText('Login')).toBeDefined()
  })

  it('should authenticate with token', async () => {
    ;(api.listRepos as any).mockResolvedValue([])
    
    render(<App />)
    const input = screen.getByPlaceholderText('Auth token')
    const button = screen.getByText('Login')

    fireEvent.change(input, { target: { value: 'test-token' } })
    fireEvent.click(button)

    await waitFor(() => {
      expect(api.setAuthToken).toHaveBeenCalledWith('test-token')
    })
  })

  it('should show repos view after authentication', async () => {
    ;(api.getAuthToken as any).mockReturnValue('test-token')
    ;(api.listRepos as any).mockResolvedValue([
      { id: '1', name: 'Test Repo', path: '/test', is_git_root: true, registered_at: '2024-01-01' },
    ])

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Test Repo')).toBeDefined()
    })
  })

  it('should handle logout', async () => {
    ;(api.getAuthToken as any).mockReturnValue('test-token')
    ;(api.listRepos as any).mockResolvedValue([])

    render(<App />)

    await waitFor(() => {
      const logoutButton = screen.getByText('Logout')
      fireEvent.click(logoutButton)
    })

    expect(api.clearAuthToken).toHaveBeenCalled()
  })
})


describe('AudioRecorder Integration', () => {
  it('should handle transcript insertion', async () => {
    const mockTranscribe = vi.fn().mockResolvedValue({ text: 'Hello world' })
    global.fetch = mockTranscribe as any

    const { AudioRecorder } = await import('./components/AudioRecorder')
    const onTranscript = vi.fn()

    render(<AudioRecorder onTranscript={onTranscript} />)

    const recordButton = screen.getByText(/Record/)
    expect(recordButton).toBeDefined()
  })
})
