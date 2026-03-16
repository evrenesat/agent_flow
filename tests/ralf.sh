#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RALF_SCRIPT="${REPO_ROOT}/scripts/ralf"

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

assert_eq() {
  local expected="$1"
  local actual="$2"
  local context="$3"

  if [ "$expected" != "$actual" ]; then
    fail "${context}: expected '${expected}', got '${actual}'"
  fi
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local context="$3"

  if [[ "$haystack" != *"$needle"* ]]; then
    fail "${context}: missing '${needle}'"
  fi
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local context="$3"

  if [[ "$haystack" == *"$needle"* ]]; then
    fail "${context}: unexpected '${needle}'"
  fi
}

TMP_ROOT="$(mktemp -d)"
trap '/bin/rm -rf "${TMP_ROOT}"' EXIT

FAKE_BIN="${TMP_ROOT}/bin"
LOG_DIR="${TMP_ROOT}/logs"
PROJECT_DIR="${TMP_ROOT}/project"
mkdir -p "${FAKE_BIN}" "${LOG_DIR}" "${PROJECT_DIR}"

cat > "${FAKE_BIN}/gemini" <<'EOF'
#!/bin/bash
set -euo pipefail

: "${LOG_DIR:?}"
echo "gemini" >> "${LOG_DIR}/order.log"

i=0
for arg in "$@"; do
  printf '%s' "$arg" > "${LOG_DIR}/gemini.arg.${i}"
  i=$((i + 1))
done

printf '%s' "$i" > "${LOG_DIR}/gemini.argc"
EOF

cat > "${TMP_ROOT}/setup.sh" <<'EOF'
#!/bin/bash
set -euo pipefail

: "${LOG_DIR:?}"
echo "setup" >> "${LOG_DIR}/order.log"

i=0
for arg in "$@"; do
  printf '%s' "$arg" > "${LOG_DIR}/setup.arg.${i}"
  i=$((i + 1))
done

printf '%s' "$i" > "${LOG_DIR}/setup.argc"
EOF

chmod +x "${FAKE_BIN}/gemini" "${TMP_ROOT}/setup.sh"

export PATH="${FAKE_BIN}:${PATH}"
export LOG_DIR

echo "Running Test 1: default plan path..."
(
  cd "${PROJECT_DIR}"
  printf '### [ ] Checkpoint 1\n' > IMPLEMENTATION_PLAN.md
  RALPH_SETUP_SCRIPT="${TMP_ROOT}/setup.sh" "${RALF_SCRIPT}"
)

assert_eq "setup"$'\n'"gemini" "$(cat "${LOG_DIR}/order.log")" "default run order"
assert_eq "-y" "$(cat "${LOG_DIR}/gemini.arg.0")" "gemini arg 0"
assert_eq "-p" "$(cat "${LOG_DIR}/gemini.arg.1")" "gemini arg 1"
DEFAULT_PROMPT="$(cat "${LOG_DIR}/gemini.arg.2")"
assert_contains "${DEFAULT_PROMPT}" "Read the plan file at ${PROJECT_DIR}/IMPLEMENTATION_PLAN.md." "default prompt plan path"
assert_contains "${DEFAULT_PROMPT}" "<promise>PLAN_COMPLETE</promise>" "default prompt promise"
assert_eq "${DEFAULT_PROMPT}" "$(cat "${LOG_DIR}/setup.arg.0")" "default prompt matches setup"

/bin/rm -f "${LOG_DIR}"/order.log "${LOG_DIR}"/gemini.arg.* "${LOG_DIR}"/setup.arg.*

echo "Running Test 2: explicit plan path with extra prompt..."
(
  cd "${TMP_ROOT}"
  mkdir -p nested
  printf '### [ ] Checkpoint 1\n' > nested/PLAN.md
  RALPH_SETUP_SCRIPT="${TMP_ROOT}/setup.sh" "${RALF_SCRIPT}" nested/PLAN.md continue from checkpoint 2 --skip docs
)

assert_eq "setup"$'\n'"gemini" "$(cat "${LOG_DIR}/order.log")" "explicit run order"
EXPLICIT_PROMPT="$(cat "${LOG_DIR}/gemini.arg.2")"
assert_contains "${EXPLICIT_PROMPT}" "Read the plan file at ${TMP_ROOT}/nested/PLAN.md." "explicit prompt plan path"
assert_contains "${EXPLICIT_PROMPT}" $'\n\ncontinue from checkpoint 2 --skip docs' "explicit prompt extra text"
assert_eq "${EXPLICIT_PROMPT}" "$(cat "${LOG_DIR}/setup.arg.0")" "explicit prompt matches setup"
assert_eq "--completion-promise" "$(cat "${LOG_DIR}/setup.arg.1")" "setup flag 1"
assert_eq "PLAN_COMPLETE" "$(cat "${LOG_DIR}/setup.arg.2")" "setup promise"
assert_eq "--max-iterations" "$(cat "${LOG_DIR}/setup.arg.3")" "setup flag 2"
assert_eq "20" "$(cat "${LOG_DIR}/setup.arg.4")" "setup max iterations"

/bin/rm -f "${LOG_DIR}"/order.log "${LOG_DIR}"/gemini.arg.* "${LOG_DIR}"/setup.arg.*

echo "Running Test 3: dry-run output..."
DRY_RUN_OUTPUT="$(
  cd "${TMP_ROOT}" &&
  RALPH_SETUP_SCRIPT="${TMP_ROOT}/setup.sh" "${RALF_SCRIPT}" --dry-run nested/PLAN.md continue from the next checkpoint only
)"

assert_contains "${DRY_RUN_OUTPUT}" "${TMP_ROOT}/setup.sh" "dry-run setup command"
assert_contains "${DRY_RUN_OUTPUT}" "gemini -y -p" "dry-run gemini command"
assert_contains "${DRY_RUN_OUTPUT}" "continue from the next checkpoint only" "dry-run extra prompt"
assert_not_contains "${DRY_RUN_OUTPUT}" "/ralph:loop" "dry-run should not use slash command"

echo "Running Test 4: missing setup script..."
if (
  cd "${TMP_ROOT}" &&
  RALPH_SETUP_SCRIPT="${TMP_ROOT}/missing-setup.sh" "${RALF_SCRIPT}" nested/PLAN.md >/dev/null 2>"${TMP_ROOT}/missing-setup.err"
); then
  fail "missing setup script should fail"
fi
assert_contains "$(cat "${TMP_ROOT}/missing-setup.err")" "Ralph setup script" "missing setup stderr"

echo "Running Test 5: missing plan file..."
if (
  cd "${TMP_ROOT}" &&
  RALPH_SETUP_SCRIPT="${TMP_ROOT}/setup.sh" "${RALF_SCRIPT}" missing-plan.md >/dev/null 2>"${TMP_ROOT}/missing-plan.err"
); then
  fail "missing plan should fail"
fi
assert_contains "$(cat "${TMP_ROOT}/missing-plan.err")" "Plan file 'missing-plan.md' not found." "missing plan stderr"

echo "PASS: All tests passed!"
