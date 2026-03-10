---
name: ralph-execute
description: A skill for executing long-running, multi-turn, iterative development loops by relying on persistent file state and autonomous self-correction.
---

# Ralph Execution Loop

This skill guides you (the Agent) in performing an autonomous, self-referential development loop. Instead of relying heavily on conversation history or stopping for manual user intervention after every step, you will use an active cycle of implementation, verification, and feedback based on the actual codebase state.

## Core Concepts

1. **File State Over Conversational Memory:** Your definitive source of truth is the actual codebase—files, test outputs, and system state—not the chat context. Conversational memory can get stale or compacted; the codebase is always current.
2. **Autonomous Improvement:** You must act, verify those actions immediately, and use the verification output to adjust your next steps.
3. **Definitive Boundaries:** Never consider your task complete until all objective requirements (like passing tests or specific outputs) are empirically met.

## Execution Framework (The Loop)

When tasked with a complex or long-running objective, follow this strict loop:

1. **Define "Done"**: Establish clear, verifiable completion criteria upfront. If you are given a specific completion promise (e.g., `<promise>DONE</promise>`), you must emit it only when all criteria are strictly satisfied.
2. **Act (Implementation)**: Make your targeted modifications—whether writing code, fixing a bug, or adding tests.
3. **Verify**: Use rigorous checks (running test suites, linters, or compilation commands) immediately after making changes. Do not assume your code works without verification.
4. **Self-Correct (Feedback)**: If the verification step (e.g., a test) fails, carefully parse the error output, diagnose the issue, and loop back to step 2.
5. **Exit**: Conclude the loop and output the completion promise *only* when the environment matches the exact required "done" state.

## Implementation Guidelines

### 1. Actively Use TDD Concepts
Structure your problem-solving through test-driven development:
- Check for or establish failing tests for the feature.
- Make the minimum necessary code changes to allow tests to pass.
- Run the test suite.
- Analyze any errors and debug the codebase autonomously.
- Iterate until the entire specific functionality is green.

### 2. Safety and Recovery
If your changes are breaking things in succession, stop and reassess the baseline instead of endlessly patching symptoms. Use git status/diff to understand exactly what you've modified during your loop, and revert if you hit a dead end.

### 3. Keep Iterations Focused
Do not try to solve the entire prompt in a single monolithic change. Break the work down logically. Perform one clear objective (like setting up one endpoint), verify it works, and then move to the next.
