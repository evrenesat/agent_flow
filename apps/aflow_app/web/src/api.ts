import type { RepoInfo, PlanInfo, CodexSession, CodexMessage, ExecutionStatus, ExecutionEvent } from './types'

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

export async function listRepos(): Promise<RepoInfo[]> {
  return fetchJson<RepoInfo[]>(`${API_BASE}/repos`)
}

export async function addRepo(path: string, name?: string): Promise<RepoInfo> {
  return fetchJson<RepoInfo>(`${API_BASE}/repos`, {
    method: 'POST',
    body: JSON.stringify({ path, name }),
  })
}

export async function getRepo(repoId: string): Promise<RepoInfo> {
  return fetchJson<RepoInfo>(`${API_BASE}/repos/${repoId}`)
}

export async function updateRepo(repoId: string, name: string): Promise<RepoInfo> {
  return fetchJson<RepoInfo>(`${API_BASE}/repos/${repoId}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })
}

export async function removeRepo(repoId: string): Promise<void> {
  return fetchJson<void>(`${API_BASE}/repos/${repoId}`, {
    method: 'DELETE',
  })
}

export async function listPlans(repoId: string): Promise<PlanInfo[]> {
  return fetchJson<PlanInfo[]>(`${API_BASE}/repos/${repoId}/plans`)
}

export async function listCodexSessions(repoPath?: string): Promise<CodexSession[]> {
  const params = repoPath ? `?repo_path=${encodeURIComponent(repoPath)}` : ''
  return fetchJson<CodexSession[]>(`${API_BASE}/codex/sessions${params}`)
}

export async function getCodexSession(sessionId: string): Promise<CodexSession> {
  return fetchJson<CodexSession>(`${API_BASE}/codex/sessions/${sessionId}`)
}

export async function fetchCodexMessages(sessionId: string, limit?: number): Promise<CodexMessage[]> {
  const params = limit ? `?limit=${limit}` : ''
  return fetchJson<CodexMessage[]>(`${API_BASE}/codex/sessions/${sessionId}/messages${params}`)
}

export async function sendCodexMessage(sessionId: string, content: string): Promise<CodexMessage> {
  return fetchJson<CodexMessage>(`${API_BASE}/codex/sessions/${sessionId}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
}

export async function listPlanDrafts(repoId: string): Promise<string[]> {
  return fetchJson<string[]>(`${API_BASE}/codex/repos/${repoId}/plans/drafts`)
}

export async function savePlanDraft(repoId: string, filename: string, content: string): Promise<void> {
  return fetchJson<void>(`${API_BASE}/codex/repos/${repoId}/plans/drafts`, {
    method: 'POST',
    body: JSON.stringify({ filename, content }),
  })
}

export async function loadPlanDraft(repoId: string, filename: string): Promise<string> {
  const response = await fetch(`${API_BASE}/codex/repos/${repoId}/plans/drafts/${filename}`, {
    headers: getHeaders(),
  })
  if (!response.ok) {
    throw new ApiError(response.status, await response.text())
  }
  return response.text()
}

export async function promotePlanDraft(repoId: string, filename: string): Promise<void> {
  return fetchJson<void>(`${API_BASE}/codex/repos/${repoId}/plans/drafts/${filename}/promote`, {
    method: 'POST',
  })
}

export async function deletePlanDraft(repoId: string, filename: string): Promise<void> {
  return fetchJson<void>(`${API_BASE}/codex/repos/${repoId}/plans/drafts/${filename}`, {
    method: 'DELETE',
  })
}

export async function startExecution(request: {
  repo_id: string
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
