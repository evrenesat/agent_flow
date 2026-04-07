import { describe, it, expect, beforeEach, vi } from 'vitest'
import * as api from './api'

describe('API Client', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    api.clearAuthToken()
  })

  describe('Auth Token Management', () => {
    it('should set and get auth token', () => {
      api.setAuthToken('test-token')
      expect(api.getAuthToken()).toBe('test-token')
    })

    it('should clear auth token', () => {
      api.setAuthToken('test-token')
      api.clearAuthToken()
      expect(api.getAuthToken()).toBeNull()
    })
  })

  describe('Repository Operations', () => {
    it('should list repos with auth header', async () => {
      api.setAuthToken('test-token')
      const mockRepos = [{ id: '1', name: 'test', path: '/test', is_git_root: true, registered_at: '2024-01-01' }]
      
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => mockRepos,
      })

      const repos = await api.listRepos()
      expect(repos).toEqual(mockRepos)
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/repos',
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: 'Bearer test-token',
          }),
        })
      )
    })

    it('should add repo', async () => {
      api.setAuthToken('test-token')
      const mockRepo = { id: '1', name: 'test', path: '/test', is_git_root: true, registered_at: '2024-01-01' }
      
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => mockRepo,
      })

      const repo = await api.addRepo('/test', 'test')
      expect(repo).toEqual(mockRepo)
    })
  })

  describe('Codex Operations', () => {
    it('should list sessions', async () => {
      api.setAuthToken('test-token')
      const mockSessions = [{ id: '1', name: 'session', repo_path: null, created_at: '2024-01-01', updated_at: '2024-01-01' }]
      
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => mockSessions,
      })

      const sessions = await api.listCodexSessions()
      expect(sessions).toEqual(mockSessions)
    })

    it('should send message', async () => {
      api.setAuthToken('test-token')
      const mockMessage = { id: '1', role: 'assistant', content: 'response', timestamp: '2024-01-01' }
      
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: true,
        json: async () => mockMessage,
      })

      const message = await api.sendCodexMessage('session-1', 'hello')
      expect(message).toEqual(mockMessage)
    })
  })

  describe('Error Handling', () => {
    it('should throw ApiError on failed request', async () => {
      api.setAuthToken('test-token')
      
      ;(global.fetch as any).mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () => JSON.stringify({ detail: 'Unauthorized' }),
      })

      await expect(api.listRepos()).rejects.toThrow('Unauthorized')
    })
  })
})
