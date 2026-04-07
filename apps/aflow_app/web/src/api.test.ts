import { describe, it, expect, beforeEach, vi } from 'vitest'
import * as api from './api'

function mockOkJson<T>(value: T) {
  ;(global.fetch as any).mockResolvedValueOnce({
    ok: true,
    json: async () => value,
  })
}

describe('API Client', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
    api.clearAuthToken()
  })

  describe('Auth Token Management', () => {
    it('sets and clears the auth token', () => {
      api.setAuthToken('test-token')
      expect(api.getAuthToken()).toBe('test-token')

      api.clearAuthToken()
      expect(api.getAuthToken()).toBeNull()
    })
  })

  describe('Project operations', () => {
    it('lists projects with auth headers', async () => {
      api.setAuthToken('test-token')
      mockOkJson([{ id: 'project-1', display_name: 'Alpha', current_path: '/tmp/alpha' }])

      const projects = await api.listProjects()

      expect(projects[0].display_name).toBe('Alpha')
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects',
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: 'Bearer test-token',
          }),
        })
      )
    })

    it('updates a project override', async () => {
      api.setAuthToken('test-token')
      mockOkJson({ id: 'project-1', display_name: 'Renamed', current_path: '/tmp/renamed' })

      const project = await api.updateProject('project-1', {
        display_name: 'Renamed',
        current_path: '/tmp/renamed',
      })

      expect(project.current_path).toBe('/tmp/renamed')
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1',
        expect.objectContaining({
          method: 'PATCH',
          body: JSON.stringify({
            display_name: 'Renamed',
            current_path: '/tmp/renamed',
          }),
        })
      )
    })
  })

  describe('Thread operations', () => {
    it('lists project threads with snake_case query params', async () => {
      api.setAuthToken('test-token')
      mockOkJson({ threads: [], next_cursor: null })

      await api.listProjectThreads('project-1', {
        search_term: 'hello',
        limit: 5,
        source_kinds: ['app-server'],
      })

      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/threads?search_term=hello&limit=5&source_kinds=app-server',
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: 'Bearer test-token',
          }),
        })
      )
    })

    it('reads a project thread with turns by default', async () => {
      api.setAuthToken('test-token')
      mockOkJson({
        id: 'thread-1',
        preview: 'preview',
        ephemeral: false,
        model_provider: 'openai',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
        status: {},
        path: null,
        cwd: '/tmp/project',
        cli_version: '1',
        source: 'app-server',
        agent_nickname: null,
        agent_role: null,
        git_info: null,
        name: 'Thread',
        turns: [],
      })

      await api.getProjectThread('project-1', 'thread-1')

      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/threads/thread-1?include_turns=true',
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: 'Bearer test-token',
          }),
        })
      )
    })

    it('starts a turn with the official user input shape', async () => {
      api.setAuthToken('test-token')
      mockOkJson({
        id: 'turn-1',
        status: 'inProgress',
        items: [],
        error: null,
      })

      await api.startProjectTurn('project-1', 'thread-1', {
        input: [
          {
            type: 'text',
            text: 'hello',
            text_elements: [],
          },
        ],
        cwd: '/tmp/project',
      })

      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/threads/thread-1/turns',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            input: [
              {
                type: 'text',
                text: 'hello',
                text_elements: [],
              },
            ],
            cwd: '/tmp/project',
          }),
        })
      )
    })

    it('starts, resumes, and forks with cwd overrides', async () => {
      api.setAuthToken('test-token')
      mockOkJson({
        thread: { id: 'thread-1', preview: '', ephemeral: false, model_provider: 'openai', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z', status: {}, path: null, cwd: '/tmp/project', cli_version: '1', source: 'app-server', agent_nickname: null, agent_role: null, git_info: null, name: null, turns: [] },
        model: null,
        model_provider: null,
        service_tier: null,
        cwd: '/tmp/project',
        approval_policy: null,
        approvals_reviewer: {},
        sandbox: {},
        reasoning_effort: null,
      })

      await api.startProjectThread('project-1', { cwd: '/tmp/project' })
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/threads',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            cwd: '/tmp/project',
          }),
        })
      )

      mockOkJson({
        thread: { id: 'thread-2', preview: '', ephemeral: false, model_provider: 'openai', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z', status: {}, path: null, cwd: '/tmp/project', cli_version: '1', source: 'app-server', agent_nickname: null, agent_role: null, git_info: null, name: null, turns: [] },
        model: null,
        model_provider: null,
        service_tier: null,
        cwd: '/tmp/project',
        approval_policy: null,
        approvals_reviewer: {},
        sandbox: {},
        reasoning_effort: null,
      })

      await api.resumeProjectThread('project-1', 'thread-1', { cwd: '/tmp/project' })
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/threads/thread-1/resume',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            cwd: '/tmp/project',
          }),
        })
      )

      mockOkJson({
        thread: { id: 'thread-3', preview: '', ephemeral: false, model_provider: 'openai', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z', status: {}, path: null, cwd: '/tmp/project', cli_version: '1', source: 'app-server', agent_nickname: null, agent_role: null, git_info: null, name: null, turns: [] },
        model: null,
        model_provider: null,
        service_tier: null,
        cwd: '/tmp/project',
        approval_policy: null,
        approvals_reviewer: {},
        sandbox: {},
        reasoning_effort: null,
      })

      await api.forkProjectThread('project-1', 'thread-1', { cwd: '/tmp/project' })
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/threads/thread-1/fork',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            cwd: '/tmp/project',
          }),
        })
      )
    })
  })

  describe('Plan draft operations', () => {
    it('saves, loads, promotes, and deletes drafts with the corrected contract', async () => {
      api.setAuthToken('test-token')

      mockOkJson({ name: 'plan-a', path: '/tmp/plan-a.md', status: 'draft' })
      await api.savePlanDraft('project-1', { name: 'plan-a', content: '# Plan' })
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/plans/drafts',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ name: 'plan-a', content: '# Plan' }),
        })
      )

      mockOkJson({ name: 'plan-a', content: '# Plan' })
      await api.loadPlanDraft('project-1', 'plan-a')
      expect(global.fetch).toHaveBeenCalledWith('/api/projects/project-1/plans/drafts/plan-a', expect.any(Object))

      mockOkJson({ name: 'plan-a', path: '/tmp/plan-a.md', status: 'in_progress' })
      await api.promotePlanDraft('project-1', { draft_name: 'plan-a', target_name: 'plan-a' })
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/plans/promote',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ draft_name: 'plan-a', target_name: 'plan-a' }),
        })
      )

      mockOkJson(undefined)
      await api.deletePlanDraft('project-1', 'plan-a')
      expect(global.fetch).toHaveBeenCalledWith(
        '/api/projects/project-1/plans/drafts/plan-a',
        expect.objectContaining({
          method: 'DELETE',
        })
      )
    })
  })

  describe('Execution operations', () => {
    it('starts execution against a project id', async () => {
      api.setAuthToken('test-token')
      mockOkJson({ run_id: 'run-1' })

      await api.startExecution({ project_id: 'project-1', plan_path: 'plans/in-progress/demo.md' })

      expect(global.fetch).toHaveBeenCalledWith(
        '/api/executions',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            project_id: 'project-1',
            plan_path: 'plans/in-progress/demo.md',
          }),
        })
      )
    })
  })

  describe('Error handling', () => {
    it('throws ApiError on failed request', async () => {
      api.setAuthToken('test-token')

      ;(global.fetch as any).mockResolvedValueOnce({
        ok: false,
        status: 401,
        text: async () => JSON.stringify({ detail: 'Unauthorized' }),
      })

      await expect(api.listProjects()).rejects.toThrow('Unauthorized')
    })
  })
})
