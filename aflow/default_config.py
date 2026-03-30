STARTER_CONFIG = """\
[aflow]
default_workflow = "simple"

[harness.opencode.profiles.default]
model = "FILL_IN_MODEL"

[harness.codex.profiles.high]
model = "FILL_IN_MODEL"
effort = "high"

[workflow.simple.steps.implement_plan]
profile = "opencode.default"
prompts = ["implementation_prompt"]
go = [
  { to = "END", when = "DONE || MAX_TURNS_REACHED" },
  { to = "implement_plan" },
]

[prompts]
implementation_prompt = "Work from the plan at {ACTIVE_PLAN_PATH}. Re-read it from disk before acting. Complete the next needed implementation work without changing the intended scope. If you need a new follow-up plan, write it to {NEW_PLAN_PATH}."

# Example multi-step review loop. Uncomment and fill models/profiles you actually use.
#
# [harness.opencode.profiles.turbo]
# model = "FILL_IN_MODEL"
#
# [harness.codex.profiles.high]
# model = "FILL_IN_MODEL"
# effort = "high"
#
# [harness.claude.profiles.opus]
# model = "FILL_IN_MODEL"
# effort = "medium"
#
# [workflow.review_loop.steps.review_plan]
# profile = "claude.opus"
# prompts = ["review_plan"]
# go = [{ to = "implement_plan" }]
#
# [workflow.review_loop.steps.implement_plan]
# profile = "opencode.turbo"
# prompts = ["implementation_prompt"]
# go = [{ to = "review_implementation" }]
#
# [workflow.review_loop.steps.review_implementation]
# profile = "codex.high"
# prompts = ["review_squash", "make_review_plan"]
# go = [
#   { to = "END", when = "DONE || MAX_TURNS_REACHED" },
#   { to = "implement_plan" },
# ]
#
# [prompts]
# review_plan = "Review the plan at {ORIGINAL_PLAN_PATH} for weak spots, ambiguity, and missing constraints before implementation."
# review_squash = "Review implementation progress against the original plan at {ORIGINAL_PLAN_PATH}. If more work is needed, write the new plan to {NEW_PLAN_PATH}."
# make_review_plan = "If changes are required, create the next plan at {NEW_PLAN_PATH}. Use {ACTIVE_PLAN_PATH} as the current working plan input when it differs from the original."
"""
