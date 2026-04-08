import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { App } from './App'
import * as api from './api'

vi.mock('./api', () => ({
  getAuthToken: vi.fn(),
  setAuthToken: vi.fn(),
  clearAuthToken: vi.fn(),
  listProjects: vi.fn(),
  updateProject: vi.fn(),
  listProjectThreads: vi.fn(),
  getProjectThread: vi.fn(),
  savePlanDraft: vi.fn(),
  listProjectPlans: vi.fn(),
  listPlanDrafts: vi.fn(),
  startProjectThread: vi.fn(),
  resumeProjectThread: vi.fn(),
  forkProjectThread: vi.fn(),
  startProjectTurn: vi.fn(),
  startExecution: vi.fn(),
  subscribeToExecutionEvents: vi.fn(),
}))

const project = {
  id: 'project-1',
  display_name: 'Alpha Project',
  current_path: '/workspace/alpha',
  historical_aliases: [],
  detection_source: 'local_git_root',
  linked_thread_count: 1,
  is_git_root: true,
  registered_at: '2024-01-01T00:00:00Z',
}

const threadMarkdown = '# Thread Plan\n\n## Summary\nShip the update.'

const thread = {
  id: 'thread-1',
  preview: 'Plan turn preview',
  ephemeral: false,
  model_provider: 'openai',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  status: { type: 'active', activeFlags: [] },
  path: null,
  cwd: '/workspace/alpha',
  cli_version: '1.0.0',
  source: 'app-server',
  agent_nickname: null,
  agent_role: null,
  git_info: null,
  name: 'Planning thread',
  turns: [
    {
      id: 'turn-1',
      status: 'completed',
      items: [{ type: 'text', text: threadMarkdown, text_elements: [] }],
      error: null,
    },
  ],
}

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    ;(api.getAuthToken as any).mockReturnValue(null)
  })

  it('shows the login screen when not authenticated', () => {
    render(<App />)
    expect(screen.getByPlaceholderText('Auth token')).toBeDefined()
    expect(screen.getByText('Login')).toBeDefined()
  })

  it('shows projects and threads after authentication', async () => {
    ;(api.getAuthToken as any).mockReturnValue('test-token')
    ;(api.listProjects as any).mockResolvedValue([project])
    ;(api.listProjectThreads as any).mockResolvedValue({ threads: [thread], next_cursor: null })
    ;(api.getProjectThread as any).mockResolvedValue(thread)
    ;(api.listProjectPlans as any).mockResolvedValue([])
    ;(api.listPlanDrafts as any).mockResolvedValue([])

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Alpha Project')).toBeDefined()
    })
    expect(screen.getByText('Alpha Project').closest('button')?.className).toContain('content-button')
    fireEvent.click(screen.getByText('Open'))
    await waitFor(() => {
      expect(screen.getByText('Planning thread')).toBeDefined()
    })

    expect(api.listProjectThreads).toHaveBeenCalledWith('project-1')
  })

  it('lets the user edit a project path override', async () => {
    ;(api.getAuthToken as any).mockReturnValue('test-token')
    ;(api.listProjects as any).mockResolvedValue([project])
    ;(api.listProjectThreads as any).mockResolvedValue({ threads: [], next_cursor: null })
    ;(api.listProjectPlans as any).mockResolvedValue([])
    ;(api.listPlanDrafts as any).mockResolvedValue([])
    ;(api.updateProject as any).mockResolvedValue({
      ...project,
      display_name: 'Alpha Project',
      current_path: '/workspace/alpha-renamed',
    })

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Alpha Project')).toBeDefined()
    })
    fireEvent.click(screen.getByText('Open'))

    fireEvent.click(screen.getByText('Edit project'))
    const pathInput = screen.getByDisplayValue('/workspace/alpha') as HTMLInputElement
    fireEvent.change(pathInput, { target: { value: '/workspace/alpha-renamed' } })
    fireEvent.click(screen.getByText('Save project'))

    await waitFor(() => {
      expect(api.updateProject).toHaveBeenCalledWith('project-1', {
        display_name: 'Alpha Project',
        current_path: '/workspace/alpha-renamed',
      })
    })
  })

  it('keeps saving assistant markdown as a plan draft', async () => {
    ;(api.getAuthToken as any).mockReturnValue('test-token')
    ;(api.listProjects as any).mockResolvedValue([project])
    ;(api.listProjectThreads as any).mockResolvedValue({ threads: [thread], next_cursor: null })
    ;(api.getProjectThread as any).mockResolvedValue(thread)
    ;(api.listProjectPlans as any).mockResolvedValue([])
    ;(api.listPlanDrafts as any).mockResolvedValue([])
    ;(api.savePlanDraft as any).mockResolvedValue({ name: 'plan-2024', path: '/workspace/alpha/plans/drafts/plan-2024.md', status: 'draft' })

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Alpha Project')).toBeDefined()
    })
    fireEvent.click(screen.getByText('Open'))
    await waitFor(() => {
      expect(screen.getByText('Save plan draft')).toBeDefined()
    })

    fireEvent.click(screen.getByText('Save plan draft'))

    await waitFor(() => {
      expect(api.savePlanDraft).toHaveBeenCalledWith(
        'project-1',
        expect.objectContaining({
          name: expect.stringMatching(/^plan-/),
          content: threadMarkdown,
        })
      )
    })
  })

  it('shows a non-fatal Codex status banner when thread listing is uninitialized', async () => {
    ;(api.getAuthToken as any).mockReturnValue('test-token')
    ;(api.listProjects as any).mockResolvedValue([project])
    ;(api.listProjectThreads as any).mockResolvedValue({
      threads: [],
      next_cursor: null,
      backend_status: {
        state: 'uninitialized',
        message: 'Codex app-server is not initialized yet.',
        detail: 'Not initialized',
      },
    })
    ;(api.listProjectPlans as any).mockResolvedValue([])
    ;(api.listPlanDrafts as any).mockResolvedValue([])

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Alpha Project')).toBeDefined()
    })
    fireEvent.click(screen.getByText('Open'))

    await waitFor(() => {
      expect(screen.getByText('Codex backend unavailable')).toBeDefined()
    })
    expect(screen.getByText('Codex app-server is not initialized yet.')).toBeDefined()
    expect(screen.queryByText('Not initialized')).toBeNull()
    expect(screen.getByRole('button', { name: 'New thread' }).disabled).toBe(false)
    expect(screen.getByText('No threads found for this project yet.')).toBeDefined()
  })

  it('opens a plan for execution from the selected project path', async () => {
    ;(api.getAuthToken as any).mockReturnValue('test-token')
    ;(api.listProjects as any).mockResolvedValue([project])
    ;(api.listProjectThreads as any).mockResolvedValue({ threads: [], next_cursor: null })
    ;(api.listProjectPlans as any).mockResolvedValue([
      {
        name: 'demo',
        path: '/workspace/alpha/plans/in-progress/demo.md',
        status: 'in_progress',
        checkpoint_count: 3,
        unchecked_count: 1,
        is_complete: false,
      },
    ])
    ;(api.listPlanDrafts as any).mockResolvedValue([])
    ;(api.startExecution as any).mockResolvedValue({ run_id: 'run-1' })
    ;(api.subscribeToExecutionEvents as any).mockReturnValue(() => {})

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Alpha Project')).toBeDefined()
    })

    fireEvent.click(screen.getByText('Open'))
    fireEvent.click(screen.getByText('Plans'))
    await waitFor(() => {
      expect(screen.getByText('Start execution')).toBeDefined()
    })
    fireEvent.click(screen.getByText('Start execution'))

    await waitFor(() => {
      expect(api.startExecution).toHaveBeenCalledWith({
        project_id: 'project-1',
        plan_path: '/workspace/alpha/plans/in-progress/demo.md',
      })
    })
  })

  it('handles logout', async () => {
    ;(api.getAuthToken as any).mockReturnValue('test-token')
    ;(api.listProjects as any).mockResolvedValue([])
    ;(api.listProjectThreads as any).mockResolvedValue({ threads: [], next_cursor: null })
    ;(api.listProjectPlans as any).mockResolvedValue([])
    ;(api.listPlanDrafts as any).mockResolvedValue([])

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Logout')).toBeDefined()
    })

    fireEvent.click(screen.getByText('Logout'))

    expect(api.clearAuthToken).toHaveBeenCalled()
  })
})
