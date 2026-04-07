export interface RepoInfo {
  id: string
  name: string
  path: string
  is_git_root: boolean
  registered_at: string
}

export interface PlanInfo {
  name: string
  path: string
  status: 'draft' | 'in_progress'
  checkpoint_count: number
  unchecked_count: number
  is_complete: boolean
}

export interface CodexSession {
  id: string
  name: string
  repo_path: string | null
  created_at: string
  updated_at: string
}

export interface CodexMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

export interface ExecutionStatus {
  run_id: string
  repo_id: string
  plan_path: string
  workflow_name: string | null
  status: string
  turns_completed: number
  current_step: string | null
  started_at: string
  error: string | null
}

export interface ExecutionEvent {
  type: 'run_started' | 'turn_started' | 'turn_finished' | 'status_update' | 'run_completed' | 'run_failed'
  data: Record<string, unknown>
  timestamp: string
}
