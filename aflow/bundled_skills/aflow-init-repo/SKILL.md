---
name: aflow-init-repo
description: "Initialize a local git repository and create an initial commit for an aflow lifecycle bootstrap. Operates local-only, writes only README.md, and emits AFLOW_STOP for ambiguous or failed states."
---

# AFlow Init Repo

Use this skill only when the aflow engine invokes it as the pre-lifecycle repo bootstrap handoff. The engine supplies the repo root, branch name, README title, and README body in the prompt. Do not invoke this skill manually unless replicating the engine's exact handoff call.

## Rules

- Operate only on the local filesystem. Do not run `git fetch`, `git pull`, `git push`, or any command that contacts a remote. Do not add, remove, or configure remotes.
- Write only `README.md`. Do not stage or commit any other pre-existing files.
- If the repository already has commits, emit `AFLOW_STOP: repository already has commits — bootstrap must not rewrite history` and stop immediately.
- If `main_branch` is empty or unset in the prompt, emit `AFLOW_STOP: main_branch is required for repo bootstrap`.

## Bootstrap Steps

1. Check whether `.git/` exists in the repo root:
   - If no `.git/` directory: run `git init -b <main_branch>` to initialize a new repository. Configure git user identity: check `git config user.email` and `git config user.name`; if either is empty, set them to `"aflow-bootstrap@local"` and `"aflow bootstrap"` respectively.
   - If `.git/` exists but HEAD points to a different unborn branch: run `git symbolic-ref HEAD refs/heads/<main_branch>` to repoint HEAD to the correct branch without creating a commit. Do not use `git checkout -b`, which fails on an unborn branch with no commits.
   - If `.git/` exists and HEAD already points to `<main_branch>` as an unborn branch: no branch setup is needed, proceed to the next step.
2. Verify the repository has no commits yet. Run `git rev-parse --verify HEAD`. If it succeeds (exit code 0), emit `AFLOW_STOP: repository already has commits — bootstrap must not rewrite history`.
3. Write `README.md` in the repo root with the provided title and body. The format is:
   ```
   # <readme_title>

   <readme_body>
   ```
   If `README.md` already exists, overwrite it — the engine has already verified zero commits so no user history is lost.
4. Stage only `README.md`:
   ```
   git add README.md
   ```
5. Create the initial commit:
   ```
   git commit -m "Initial commit"
   ```
6. Verify the commit succeeded:
   - `git rev-parse --verify HEAD` must succeed.
   - `git symbolic-ref --short HEAD` must equal `<main_branch>`.
7. Report a brief summary: repo root, branch, and the final HEAD SHA.

## Stop And Escalate If

- The repo root path does not exist or is not accessible.
- `git init` fails for any reason.
- `git add README.md` fails.
- `git commit` fails (e.g., no identity configured and auto-config above did not help).
- Any verification step fails after a seemingly successful operation.
- The prompt is missing required fields (`main_branch`, `readme_title`, or `readme_body`).

Emit `AFLOW_STOP: <reason>` on its own line. The engine detects this and fails the run immediately.
