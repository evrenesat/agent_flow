export interface ProjectInfo {
  id: string
  display_name: string
  current_path: string
  historical_aliases: string[]
  detection_source: string
  linked_thread_count: number
  is_git_root: boolean
  registered_at: string
  name?: string
  path?: string
  aliases?: string[]
}

export interface PlanInfo {
  name: string
  path: string
  status: 'draft' | 'in_progress'
  checkpoint_count: number
  unchecked_count: number
  is_complete: boolean
}

export interface ThreadTurnItem {
  type?: string
  [key: string]: unknown
}

export interface ThreadUserInputText {
  type: 'text'
  text: string
  text_elements: unknown[]
}

export interface ThreadUserInputImage {
  type: 'image'
  url: string
}

export interface ThreadUserInputLocalImage {
  type: 'localImage'
  path: string
}

export interface ThreadUserInputSkill {
  type: 'skill'
  name: string
  path: string
}

export interface ThreadUserInputMention {
  type: 'mention'
  name: string
  path: string
}

export type ThreadUserInput =
  | ThreadUserInputText
  | ThreadUserInputImage
  | ThreadUserInputLocalImage
  | ThreadUserInputSkill
  | ThreadUserInputMention

export interface ThreadTurn {
  id: string
  status: string
  items: ThreadTurnItem[]
  error: Record<string, unknown> | null
}

export interface ProjectThread {
  id: string
  preview: string
  ephemeral: boolean
  model_provider: string
  created_at: string
  updated_at: string
  status: Record<string, unknown>
  path: string | null
  cwd: string
  cli_version: string
  source: string
  agent_nickname: string | null
  agent_role: string | null
  git_info: Record<string, unknown> | null
  name: string | null
  turns: ThreadTurn[]
}

export interface ProjectThreadPage {
  threads: ProjectThread[]
  next_cursor: string | null
}

export interface ThreadMutationResult {
  thread: ProjectThread
  model: string | null
  model_provider: string | null
  service_tier: string | null
  cwd: string
  approval_policy: string | null
  approvals_reviewer: Record<string, unknown>
  sandbox: Record<string, unknown>
  reasoning_effort: string | null
}

export interface ExecutionStatus {
  run_id: string
  project_id: string
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
