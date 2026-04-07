import type {
  ExecutionEvent,
  ExecutionStatus,
  PlanInfo,
  ProjectInfo,
  ProjectThread,
  ProjectThreadPage,
  ThreadMutationResult,
  ThreadUserInput,
} from './types'

const API_BASE = '/api'

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

let authToken: string | null = null

export function setAuthToken(token: string) {
  authToken = token
  if (typeof window !== 'undefined') {
    localStorage.setItem('aflow_auth_token', token)
  }
}

export function getAuthToken(): string | null {
  if (authToken) return authToken
  if (typeof window !== 'undefined') {
    authToken = localStorage.getItem('aflow_auth_token')
  }
  return authToken
}

export function clearAuthToken() {
  authToken = null
  if (typeof window !== 'undefined') {
    localStorage.removeItem('aflow_auth_token')
  }
}

function getHeaders(): HeadersInit {
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  const token = getAuthToken()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  return headers
}

async function fetchJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...getHeaders(),
      ...options.headers,
    },
  })

  if (!response.ok) {
    const text = await response.text()
    let message = text
    try {
      const json = JSON.parse(text)
      message = json.detail || json.message || text
    } catch {
      // Use text as-is
    }
    throw new ApiError(response.status, message)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json()
}

function buildQuery(params: Record<string, string | number | boolean | string[] | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue
    if (Array.isArray(value)) {
      for (const item of value) {
        search.append(key, item)
      }
      continue
    }
    search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

export async function listProjects(): Promise<ProjectInfo[]> {
  return fetchJson<ProjectInfo[]>(`${API_BASE}/projects`)
}

export async function getProject(projectId: string): Promise<ProjectInfo> {
  return fetchJson<ProjectInfo>(`${API_BASE}/projects/${projectId}`)
}

export async function updateProject(
  projectId: string,
  request: { display_name?: string | null; current_path?: string | null; alias?: string | null }
): Promise<ProjectInfo> {
  return fetchJson<ProjectInfo>(`${API_BASE}/projects/${projectId}`, {
    method: 'PATCH',
    body: JSON.stringify(request),
  })
}

export async function listProjectPlans(projectId: string): Promise<PlanInfo[]> {
  return fetchJson<PlanInfo[]>(`${API_BASE}/projects/${projectId}/plans`)
}

export async function listProjectThreads(
  projectId: string,
  request: {
    cwd?: string
    search_term?: string
    limit?: number
    cursor?: string
    source_kinds?: string[]
    archived?: boolean
  } = {}
): Promise<ProjectThreadPage> {
  const query = buildQuery(request)
  return fetchJson<ProjectThreadPage>(`${API_BASE}/projects/${projectId}/threads${query}`)
}

export async function getProjectThread(
  projectId: string,
  threadId: string,
  includeTurns = true
): Promise<ProjectThread> {
  const query = buildQuery({ include_turns: includeTurns })
  return fetchJson<ProjectThread>(`${API_BASE}/projects/${projectId}/threads/${threadId}${query}`)
}

export async function startProjectThread(
  projectId: string,
  request: {
    cwd?: string
    model?: string
    model_provider?: string
    service_tier?: string
    approval_policy?: string
    experimental_raw_events?: boolean
    persist_extended_history?: boolean
  } = {}
): Promise<ThreadMutationResult> {
  return fetchJson<ThreadMutationResult>(`${API_BASE}/projects/${projectId}/threads`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function resumeProjectThread(
  projectId: string,
  threadId: string,
  request: {
    cwd?: string
    model?: string
    model_provider?: string
    service_tier?: string
    approval_policy?: string
    persist_extended_history?: boolean
  } = {}
): Promise<ThreadMutationResult> {
  return fetchJson<ThreadMutationResult>(`${API_BASE}/projects/${projectId}/threads/${threadId}/resume`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function forkProjectThread(
  projectId: string,
  threadId: string,
  request: {
    cwd?: string
    model?: string
    model_provider?: string
    service_tier?: string
    approval_policy?: string
    persist_extended_history?: boolean
  } = {}
): Promise<ThreadMutationResult> {
  return fetchJson<ThreadMutationResult>(`${API_BASE}/projects/${projectId}/threads/${threadId}/fork`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function setProjectThreadName(projectId: string, threadId: string, name: string): Promise<void> {
  return fetchJson<void>(`${API_BASE}/projects/${projectId}/threads/${threadId}/name`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })
}

export async function startProjectTurn(
  projectId: string,
  threadId: string,
  request: {
    input: ThreadUserInput[]
    cwd?: string
    approval_policy?: string
    model?: string
    service_tier?: string
    effort?: string
    summary?: string
    personality?: string
  }
): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>(`${API_BASE}/projects/${projectId}/threads/${threadId}/turns`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function listPlanDrafts(projectId: string): Promise<string[]> {
  return fetchJson<string[]>(`${API_BASE}/projects/${projectId}/plans/drafts`)
}

export async function savePlanDraft(
  projectId: string,
  request: { name: string; content: string }
): Promise<{ name: string; path: string; status: 'draft' }> {
  return fetchJson<{ name: string; path: string; status: 'draft' }>(`${API_BASE}/projects/${projectId}/plans/drafts`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function loadPlanDraft(projectId: string, name: string): Promise<{ name: string; content: string }> {
  return fetchJson<{ name: string; content: string }>(`${API_BASE}/projects/${projectId}/plans/drafts/${name}`)
}

export async function promotePlanDraft(
  projectId: string,
  request: { draft_name: string; target_name?: string | null }
): Promise<{ name: string; path: string; status: 'in_progress' }> {
  return fetchJson<{ name: string; path: string; status: 'in_progress' }>(`${API_BASE}/projects/${projectId}/plans/promote`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function deletePlanDraft(projectId: string, name: string): Promise<void> {
  return fetchJson<void>(`${API_BASE}/projects/${projectId}/plans/drafts/${name}`, {
    method: 'DELETE',
  })
}

export async function startExecution(request: {
  project_id: string
  plan_path: string
  workflow_name?: string
  team?: string
  start_step?: string
  max_turns?: number
  extra_instructions?: string
}): Promise<{ run_id: string }> {
  return fetchJson<{ run_id: string }>(`${API_BASE}/executions`, {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function getExecutionStatus(runId: string): Promise<ExecutionStatus> {
  return fetchJson<ExecutionStatus>(`${API_BASE}/executions/${runId}`)
}

export function subscribeToExecutionEvents(
  runId: string,
  onEvent: (event: ExecutionEvent) => void,
  onError?: (error: Error) => void
): () => void {
  const token = getAuthToken()
  const url = `${API_BASE}/executions/${runId}/events${token ? `?token=${encodeURIComponent(token)}` : ''}`
  const eventSource = new EventSource(url)

  eventSource.onmessage = (e) => {
    try {
      const event = JSON.parse(e.data) as ExecutionEvent
      onEvent(event)
    } catch (err) {
      console.error('Failed to parse event:', err)
    }
  }

  eventSource.onerror = (err) => {
    console.error('SSE error:', err)
    if (onError) {
      onError(new Error('Connection lost'))
    }
    eventSource.close()
  }

  return () => eventSource.close()
}

export async function checkHealth(): Promise<{ status: string }> {
  const response = await fetch('/health')
  if (!response.ok) {
    throw new Error('Health check failed')
  }
  return response.json()
}

export async function transcribeAudio(audioFile: File): Promise<{ text: string }> {
  const formData = new FormData()
  formData.append('file', audioFile)

  const token = getAuthToken()
  const headers: HeadersInit = {}
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE}/transcribe`, {
    method: 'POST',
    headers,
    body: formData,
  })

  if (!response.ok) {
    const text = await response.text()
    let message = text
    try {
      const json = JSON.parse(text)
      message = json.detail || json.message || text
    } catch {
      // Use text as-is
    }
    throw new ApiError(response.status, message)
  }

  return response.json()
}
