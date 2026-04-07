import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { PlanPanel } from './PlanPanel'
import { ThreadPanel } from './ThreadPanel'
import * as api from '../api'

vi.mock('../api', () => ({
  listProjectPlans: vi.fn(),
  listPlanDrafts: vi.fn(),
  loadPlanDraft: vi.fn(),
  promotePlanDraft: vi.fn(),
  deletePlanDraft: vi.fn(),
  listProjectThreads: vi.fn(),
  getProjectThread: vi.fn(),
  startProjectTurn: vi.fn(),
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

const draftContent = '# Draft Plan\n\n## Summary\nShip the update.'

const threadMarkdown = '# Thread Plan\n\n## Summary\nShip the update.'

const baseThread = {
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

const inProgressThread = {
  ...baseThread,
  updated_at: '2024-01-01T00:01:00Z',
  turns: [
    ...baseThread.turns,
    {
      id: 'turn-2',
      status: 'inProgress',
      items: [{ type: 'text', text: 'Working on it', text_elements: [] }],
      error: null,
    },
  ],
}

const completedThread = {
  ...baseThread,
  updated_at: '2024-01-01T00:02:00Z',
  turns: [
    ...baseThread.turns,
    {
      id: 'turn-2',
      status: 'completed',
      items: [{ type: 'text', text: 'Done', text_elements: [] }],
      error: null,
    },
    {
      id: 'turn-3',
      status: 'completed',
      items: [{ type: 'text', text: 'Assistant reply', text_elements: [] }],
      error: null,
    },
  ],
}

describe('PlanPanel and ThreadPanel interactions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders draft content read-only while keeping the promote target editable', async () => {
    ;(api.listProjectPlans as any).mockResolvedValue([])
    ;(api.listPlanDrafts as any).mockResolvedValue(['draft-1'])
    ;(api.loadPlanDraft as any).mockResolvedValue({ name: 'draft-1', content: draftContent })

    const { container } = render(<PlanPanel project={project} onStartExecution={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('draft-1')).toBeDefined()
    })

    fireEvent.click(screen.getByText('draft-1'))

    await waitFor(() => {
      expect(screen.getByText('Draft content')).toBeDefined()
    })

    expect(container.querySelectorAll('textarea')).toHaveLength(0)
    expect(screen.getByDisplayValue('draft-1')).toBeDefined()
    expect(
      screen.getByText((_, element) => element?.tagName.toLowerCase() === 'pre' && element.textContent?.includes('# Draft Plan') === true)
    ).toBeDefined()
  })

  it('shows an in-progress turn immediately and updates from polling once it completes', async () => {
    ;(api.listProjectThreads as any).mockResolvedValue({ threads: [baseThread], next_cursor: null })
    ;(api.getProjectThread as any)
      .mockResolvedValueOnce(baseThread)
      .mockResolvedValueOnce(inProgressThread)
      .mockResolvedValueOnce(completedThread)
    ;(api.startProjectTurn as any).mockResolvedValue({
      id: 'turn-2',
      status: 'inProgress',
      items: [{ type: 'text', text: 'Working on it', text_elements: [] }],
      error: null,
    })

    render(<ThreadPanel project={project} onSavePlanDraft={vi.fn()} />)

    await waitFor(() => {
      expect(screen.getByText('Planning thread')).toBeDefined()
    })
    await waitFor(() => {
      expect(screen.getByPlaceholderText('Send a new turn to this thread')).toBeDefined()
    })

    vi.useFakeTimers()
    fireEvent.change(screen.getByPlaceholderText('Send a new turn to this thread'), {
      target: { value: 'Please continue the plan' },
    })
    fireEvent.click(screen.getByText('Send turn'))

    await act(async () => {
      await Promise.resolve()
    })

    expect(api.startProjectTurn).toHaveBeenCalledWith('project-1', 'thread-1', {
      input: [
        {
          type: 'text',
          text: 'Please continue the plan',
          text_elements: [],
        },
      ],
      cwd: '/workspace/alpha',
    })

    expect(screen.getByText('Turn 2 • inProgress')).toBeDefined()
    expect(screen.getByText('Working on it')).toBeDefined()

    await act(async () => {
      await vi.runAllTimersAsync()
    })

    expect(screen.getByText('Turn 2 • completed')).toBeDefined()
    expect(screen.getByText('Assistant reply')).toBeDefined()
  })
})
