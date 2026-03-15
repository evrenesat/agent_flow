import type { Plugin } from "@opencode-ai/plugin"
import { existsSync, readFileSync, writeFileSync, unlinkSync } from "fs"
import { join } from "path"

/**
 * Ralph Wiggum Plugin for OpenCode
 *
 * Implementation of the Ralph Wiggum technique - continuous self-referential AI loops
 * for iterative development. Named after Ralph Wiggum from The Simpsons, embodying
 * the philosophy of persistent iteration despite setbacks.
 *
 * Core concept: Feed the same prompt repeatedly, letting the AI see its previous work
 * in files and git history, creating a self-referential feedback loop.
 *
 * Based on: https://ghuntley.com/ralph/
 */

interface RalphState {
  active: boolean
  iteration: number
  maxIterations: number
  completionPromise: string | null
  startedAt: string
  prompt: string
}

const STATE_FILE = "ralph-loop.local.md"

function parseRalphState(directory: string): RalphState | null {
  const statePath = join(directory, STATE_FILE)

  if (!existsSync(statePath)) {
    return null
  }

  try {
    const content = readFileSync(statePath, "utf-8")

    // Parse YAML frontmatter
    const frontmatterMatch = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/)
    if (!frontmatterMatch) {
      return null
    }

    const [, frontmatter, prompt] = frontmatterMatch

    // Parse frontmatter values
    const getValue = (key: string): string | null => {
      const match = frontmatter.match(new RegExp(`^${key}:\\s*(.*)$`, "m"))
      if (!match) return null
      // Remove surrounding quotes if present
      return match[1].replace(/^["'](.*)["']$/, "$1")
    }

    const active = getValue("active") === "true"
    const iteration = parseInt(getValue("iteration") || "1", 10)
    const maxIterations = parseInt(getValue("max_iterations") || "0", 10)
    const completionPromise = getValue("completion_promise")
    const startedAt = getValue("started_at") || new Date().toISOString()

    return {
      active,
      iteration,
      maxIterations,
      completionPromise: completionPromise === "null" ? null : completionPromise,
      startedAt,
      prompt: prompt.trim(),
    }
  } catch {
    return null
  }
}

function writeRalphState(directory: string, state: RalphState): void {
  const statePath = join(directory, STATE_FILE)



  const completionPromiseYaml =
    state.completionPromise === null ? "null" : `"${state.completionPromise}"`

  const content = `---
active: ${state.active}
iteration: ${state.iteration}
max_iterations: ${state.maxIterations}
completion_promise: ${completionPromiseYaml}
started_at: "${state.startedAt}"
---

${state.prompt}
`

  writeFileSync(statePath, content, "utf-8")
}

function deleteRalphState(directory: string): boolean {
  const statePath = join(directory, STATE_FILE)

  if (existsSync(statePath)) {
    unlinkSync(statePath)
    return true
  }
  return false
}

function checkCompletionPromise(text: string, promise: string): boolean {
  // Extract text from <promise> tags
  const promiseMatch = text.match(/<promise>([\s\S]*?)<\/promise>/)
  if (!promiseMatch) {
    return text.trim().replace(/\s+/g, " ") === promise
  }

  // Normalize whitespace and compare
  const promiseText = promiseMatch[1].trim().replace(/\s+/g, " ")
  return promiseText === promise
}

export const RalphPlugin: Plugin = async ({ directory, client }) => {
  const latestAssistantMessageId = new Map<string, string>()
  const latestAssistantText = new Map<string, string>()

  return {
    /**
     * Handle session idle event - this is when the AI has finished responding
     * and would normally wait for user input. In Ralph mode, we intercept this
     * to continue the loop.
     */
    event: async ({ event }) => {
      if (event.type === "message.updated") {
        if (event.properties.info.role === "assistant") {
          const sessionID = event.properties.info.sessionID
          const messageID = event.properties.info.id

          if (latestAssistantMessageId.get(sessionID) !== messageID) {
            latestAssistantMessageId.set(sessionID, messageID)
            latestAssistantText.set(sessionID, "")
          }
        }
        return
      }

      if (event.type === "message.part.updated") {
        if (event.properties.part.type !== "text") return

        const sessionID = event.properties.part.sessionID
        const messageID = latestAssistantMessageId.get(sessionID)

        if (messageID !== event.properties.part.messageID) {
          return
        }

        if (typeof event.properties.delta === "string") {
          latestAssistantText.set(
            sessionID,
            `${latestAssistantText.get(sessionID) ?? ""}${event.properties.delta}`,
          )
          return
        }

        latestAssistantText.set(sessionID, event.properties.part.text)
        return
      }

      if (event.type !== "session.idle") return

      const state = parseRalphState(directory)
      if (!state || !state.active) return

      // Get the last assistant message to check for completion
      // We need to check if the completion promise was output
      if (state.completionPromise) {
        const textContent = latestAssistantText.get(event.properties.sessionID) ?? ""

        if (textContent && checkCompletionPromise(textContent, state.completionPromise)) {
          deleteRalphState(directory)
          latestAssistantMessageId.delete(event.properties.sessionID)
          latestAssistantText.delete(event.properties.sessionID)
          return
        }
      }

      // Check max iterations
      if (state.maxIterations > 0 && state.iteration >= state.maxIterations) {
        deleteRalphState(directory)
        latestAssistantMessageId.delete(event.properties.sessionID)
        latestAssistantText.delete(event.properties.sessionID)
        return
      }

      // Continue the loop - increment iteration and feed prompt back
      const nextIteration = state.iteration + 1
      writeRalphState(directory, {
        ...state,
        iteration: nextIteration,
      })

      // Build the continuation message
      let systemMsg = `Ralph iteration ${nextIteration}`
      if (state.completionPromise) {
        systemMsg += ` | To stop: output <promise>${state.completionPromise}</promise> (ONLY when TRUE)`
      } else if (state.maxIterations > 0) {
        systemMsg += ` / ${state.maxIterations}`
      } else {
        systemMsg += ` | No completion promise set - loop runs until cancelled`
      }

      // Append the prompt back to continue the session
      // The prompt includes a marker showing the iteration
      const continuationPrompt = `[${systemMsg}]\n\n${state.prompt}`

      // Use session.prompt to continue the conversation with the current SDK.
      await client.session.prompt({
        path: { id: event.properties.sessionID },
        body: {
          parts: [
            {
              type: "text",
              text: continuationPrompt,
            },
          ],
        },
      })
    },
  }
}
