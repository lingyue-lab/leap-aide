#!/usr/bin/env bash
# run_test.sh — leap-aide regression test runner (unified CLI entry)
#
# Runs the pytest + vitest suites against isolated environments.
# Auto-detects whether we are on the testbox; if not, transparently ssh-jumps.
#
# Usage:
#   ./scripts/run_test.sh                         # show help (no-op)
#   ./scripts/run_test.sh smoke                   # L1 smoke (< 5 min)
#   ./scripts/run_test.sh function [DOMAIN]       # L2 functional area (10-30 min)
#   ./scripts/run_test.sh full                    # L3 full regression (hours)
#   ./scripts/run_test.sh frontend [SUBCMD]       # frontend vitest (smoke | full | clean | setup)
#   ./scripts/run_test.sh manual                  # L4 browser manual — prints instructions
#   ./scripts/run_test.sh setup                   # install all test deps (Python + frontend)
#   ./scripts/run_test.sh clean                   # wipe stale test state and ports
#   ./scripts/run_test.sh help                    # show this help
#
# DOMAIN (for L2): session | model | workspace | cron | i18n | ui |
#                  streaming | auth | onboarding | all
#
# Environment overrides:
#   TESTBOX_HOST=192.168.1.102   # SSH host (default)
#   TESTBOX_PORT=2322            # SSH port (default)
#   PYTEST_EXTRA="..."           # extra flags appended to pytest invocation
#   VITEST_EXTRA="..."           # extra flags appended to vitest invocation
#   DRY_RUN=1                    # print commands without executing
#
# Exit code: passed through from the runner (0 = all green).
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTBOX_HOST="${TESTBOX_HOST:-192.168.1.102}"
TESTBOX_PORT="${TESTBOX_PORT:-2322}"
TESTBOX_DIR="/media/data/projects/leap-aide-internal"
RESULTS_DIR="${PROJECT_ROOT}/tests/.results"
FRONTEND_DIR="${PROJECT_ROOT}/frontend"
FRONTEND_RESULTS_DIR="${FRONTEND_DIR}/tests/.results"
PYTEST_EXTRA="${PYTEST_EXTRA:-}"
VITEST_EXTRA="${VITEST_EXTRA:-}"
DRY_RUN="${DRY_RUN:-0}"

# ── TTY-aware colors ──
if [ -t 1 ]; then
  B=$'\033[1m' R=$'\033[0m'
  GRN=$'\033[32m' YLW=$'\033[33m' RED=$'\033[31m' CYN=$'\033[36m'
else
  B='' R='' GRN='' YLW='' RED='' CYN=''
fi

# ── Output helpers ──
say()  { printf '%s\n' "$*"; }
ok()   { printf "${GRN}✓${R} %s\n" "$*"; }
warn() { printf "${YLW}!${R} %s\n" "$*"; }
err()  { printf "${RED}✗${R} %s\n" "$*" >&2; }
die()  { err "$*"; exit 1; }
heading() { printf "\n${B}${CYN}==> %s${R}\n" "$*"; }

# ── Help ──
cmd_help() {
  cat <<EOF
${B}Leap-Aide regression test runner${R}

${B}Usage:${R}
  $0                          # show this help (no-op, no test executed)
  $0 smoke                    # L1 smoke (< 5 min)
  $0 function [DOMAIN]        # L2 functional area (10-30 min)
  $0 full                     # L3 full regression (hours)
  $0 frontend [SUBCMD]        # frontend vitest (smoke | full | clean | setup)
  $0 manual                   # L4 browser manual — prints instructions
  $0 setup                    # install ALL test deps (Python + frontend)
  $0 clean                    # wipe stale test state dirs and ports
  $0 help                     # show this help

${B}DOMAIN values for L2:${R}
  session   model   workspace   cron   i18n
  ui        auth   onboarding   all

${B}Frontend SUBCMD:${R}
  (default)   alias for smoke
  smoke       run the existing vitest tests in frontend/tests/
  full        run vitest in --watch=false mode over the whole frontend tree
  clean       remove frontend/tests/.results/
  setup       install vitest + testing-library + jsdom into frontend/node_modules

${B}Environment:${R}
  TESTBOX_HOST=192.168.1.102   # SSH host when running remotely
  TESTBOX_PORT=2322            # SSH port
  PYTEST_EXTRA="..."           # extra pytest flags (e.g. "-x -k smoke")
  VITEST_EXTRA="..."           # extra vitest flags (e.g. "--reporter=verbose")
  DRY_RUN=1                    # print command without executing

${B}Execution location:${R}
  On the testbox (${TESTBOX_HOST})  → runs locally
  Anywhere else                     → ssh-jumps to testbox first

${B}Results:${R}
  Backend junit XML  → tests/.results/junit-<level>-<timestamp>.xml
  Frontend junit XML → frontend/tests/.results/junit-<subcmd>-<timestamp>.xml
EOF
}

# ── Detect execution location ──
is_testbox() {
  # Heuristic: testbox has hermes-agent SDK installed and HERMES_HOME is /root/.hermes.
  [[ -d /usr/local/lib/hermes-agent ]] \
    && [[ "${HERMES_HOME:-/root/.hermes}" == /root/.hermes* ]]
}

# ── DOMAIN → pytest target mapping ──
domain_targets() {
  local domain="${1:-all}"
  case "$domain" in
    session)   echo "tests/test_session_*.py tests/test_sessions_api.py tests/test_gateway_sessions.py tests/test_gateway_sync.py tests/test_parallel_session_switch.py tests/test_empty_session_no_disk_write.py" ;;
    model)     echo "tests/test_provider_management.py tests/test_credential_pool_providers.py tests/test_model_resolver.py tests/test_byok_model_dropdown.py tests/test_provider_mismatch.py tests/test_minimax_provider.py" ;;
    workspace) echo "tests/test_workspace_*.py tests/test_default_workspace_fallback.py tests/test_workspace_blocked_roots_macos.py" ;;
    cron)      echo "tests/test_cron_*.py" ;;
    i18n)      echo "tests/test_chinese_locale.py" ;;
    ui)        echo "tests/test_pwa_manifest_csp.py" ;;
    auth)      echo "tests/test_auth_*.py" ;;
    onboarding) echo "tests/test_onboarding_*.py" ;;
    all)
      # Concatenate every domain except 'all' itself.
      for d in session model workspace cron i18n ui auth onboarding; do
        domain_targets "$d"
      done
      ;;
    *) die "Unknown DOMAIN: $domain. Run '$0 help' for valid values." ;;
  esac
}

# ── Build the pytest command for a given level + target string ──
build_pytest_cmd() {
  local level="$1"; shift
  # For DOMAIN=all, domain_targets emits one line per domain. Collapse newlines
  # to spaces so eval "$cmd" doesn't treat each domain as a separate command.
  local targets="${*//$'\n'/ }"
  local out="${RESULTS_DIR}/junit-${level}-$(date +%Y%m%d-%H%M%S).xml"
  local cmd=("pytest" "$targets" "--timeout=60" "-v" "--junitxml=${out}" ${PYTEST_EXTRA})
  printf '%s\n' "${cmd[*]}"
}

# ── Run pytest (locally or via ssh-jump) ──
run_pytest() {
  local level="$1"
  local targets="$2"
  mkdir -p "${RESULTS_DIR}"

  local cmd
  cmd=$(build_pytest_cmd "$level" "$targets")

  if is_testbox; then
    heading "Running L${level} locally on testbox"
    say "${B}\$ cd ${PROJECT_ROOT} && ${cmd}${R}"
    if [[ "$DRY_RUN" == "1" ]]; then return 0; fi
    ( cd "${PROJECT_ROOT}" && eval "$cmd" )
  else
    heading "Running L${level} via ssh → ${TESTBOX_HOST}:${TESTBOX_PORT}"
    # Quote the command for remote shell; use double quotes and escape $cmd.
    say "${B}\$ ssh -p ${TESTBOX_PORT} ${TESTBOX_HOST} 'cd ${TESTBOX_DIR} && ${cmd}'${R}"
    if [[ "$DRY_RUN" == "1" ]]; then return 0; fi
    ssh -p "${TESTBOX_PORT}" "${TESTBOX_HOST}" "cd ${TESTBOX_DIR} && ${cmd}"
  fi
}

# ── Bootstrap pip into the discovered Python ──
# Many stripped-down Linux images (incl. the testbox base) ship `python3`
# without `pip`. Try three strategies in order until one succeeds:
#   1. `python3 -m ensurepip --upgrade` (built into Python 3.4+)
#   2. `apt-get install -y python3-pip` (Debian/Ubuntu)
#   3. Download get-pip.py from bootstrap.pypa.io (last resort)
_bootstrap_pip() {
  local py="$1"
  say "  probing pip on ${py}…"

  # If pip is already importable, nothing to do.
  if "${py}" -m pip --version >/dev/null 2>&1; then
    say "  pip already available"
    return 0
  fi

  # 1. ensurepip — built into the stdlib, no network required.
  if "${py}" -m ensurepip --upgrade --default-pip >/dev/null 2>&1; then
    say "  pip bootstrapped via ensurepip"
    return 0
  fi

  # 2. apt-get — Debian/Ubuntu. Requires root; the testbox runs as root.
  if command -v apt-get >/dev/null 2>&1; then
    say "  ensurepip unavailable; trying apt-get install python3-pip"
    if apt-get update -qq >/dev/null 2>&1 \
       && apt-get install -y -qq python3-pip >/dev/null 2>&1; then
      say "  pip installed via apt-get"
      return 0
    fi
  fi

  # 3. get-pip.py — needs network access to bootstrap.pypa.io.
  if command -v curl >/dev/null 2>&1; then
    local tmp; tmp="$(mktemp)"
    if curl -sSf https://bootstrap.pypa.io/get-pip.py -o "${tmp}" 2>/dev/null \
       && "${py}" "${tmp}" --quiet --break-system-packages >/dev/null 2>&1; then
      rm -f "${tmp}"
      say "  pip installed via get-pip.py"
      return 0
    fi
    rm -f "${tmp}"
  fi

  return 1
}

# ── Subcommand: setup ──
cmd_setup() {
  # Discover a Python interpreter. Many modern Linux distros (incl. the testbox,
  # Debian/Ubuntu 22.04+) only ship `python3` — no `python` symlink. Fall back
  # to `python` for older systems, and finally to the literal `python3` so the
  # printed command is still useful if both are missing.
  local py_bin
  py_bin="$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)"

  heading "Ensuring pip is available for ${py_bin}"
  if is_testbox; then
    if [[ "$DRY_RUN" == "1" ]]; then
      say "${B}\$ _bootstrap_pip ${py_bin}${R}"
    else
      _bootstrap_pip "${py_bin}" || die "Failed to bootstrap pip into ${py_bin}"
    fi
  else
    say "${B}\$ ssh -p ${TESTBOX_PORT} ${TESTBOX_HOST} '_bootstrap_pip_in_remote_shell'${R}"
    # On remote, inline the bootstrap logic via ssh heredoc — keeps the remote
    # shell self-contained without requiring this script to be on PATH there.
    if [[ "$DRY_RUN" == "1" ]]; then
      say "${B}\$ (ssh) _bootstrap_pip ${py_bin}${R}"
    else
      ssh -p "${TESTBOX_PORT}" "${TESTBOX_HOST}" bash -s <<REMOTE || die "Failed to bootstrap pip on testbox"
py_bin="\$(command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)"
echo "  probing pip on \${py_bin}…"
if "\${py_bin}" -m pip --version >/dev/null 2>&1; then
  echo "  pip already available"
elif "\${py_bin}" -m ensurepip --upgrade --default-pip >/dev/null 2>&1; then
  echo "  pip bootstrapped via ensurepip"
elif command -v apt-get >/dev/null 2>&1 \
     && apt-get update -qq >/dev/null 2>&1 \
     && apt-get install -y -qq python3-pip >/dev/null 2>&1; then
  echo "  pip installed via apt-get"
elif command -v curl >/dev/null 2>&1; then
  tmp="\$(mktemp)"
  curl -sSf https://bootstrap.pypa.io/get-pip.py -o "\${tmp}" 2>/dev/null \
    && "\${py_bin}" "\${tmp}" --quiet --break-system-packages >/dev/null 2>&1
  rm -f "\${tmp}"
  echo "  pip installed via get-pip.py"
else
  echo "  FATAL: cannot bootstrap pip" >&2
  exit 1
fi
REMOTE
    fi
  fi

  heading "Installing pytest toolchain (pyyaml, pytest, pytest-timeout) via ${py_bin}"
  # `--break-system-packages` is required on PEP 668 systems (Debian/Ubuntu 22.04+,
  # the testbox base image) and a no-op on older pip versions.
  local py_cmd="${py_bin} -m pip install --quiet --break-system-packages pyyaml pytest pytest-timeout"
  if is_testbox; then
    say "${B}\$ ${py_cmd}${R}"
    [[ "$DRY_RUN" == "1" ]] || eval "$py_cmd" || die "pip install failed"
  else
    say "${B}\$ ssh -p ${TESTBOX_PORT} ${TESTBOX_HOST} '${py_cmd}'${R}"
    [[ "$DRY_RUN" == "1" ]] || ssh -p "${TESTBOX_PORT}" "${TESTBOX_HOST}" "$py_cmd" || die "pip install failed on testbox"
  fi

  heading "Installing frontend vitest toolchain (vitest, @testing-library/*, jsdom)"
  # Frontend deps are devDependencies; we install them only if frontend/package.json
  # does not already declare them, so the script is idempotent against package.json
  # edits that move them between deps and devDeps.
  #
  # Version pins (vitest@^3) are required because leap-aide pins vite@^5 and
  # vitest@4+ requires vite@^6. The npm peer-dep check would otherwise fail
  # with ERESOLVE. See https://vitest.dev/guide/migration for the matrix.
  local fe_cmd="cd frontend && npm install --save-dev --silent vitest@^3.0.0 @testing-library/react@^14.2.0 @testing-library/jest-dom@^6.4.0 jsdom@^24.0.0"
  if is_testbox; then
    say "${B}\$ ${fe_cmd}${R}"
    [[ "$DRY_RUN" == "1" ]] || (cd "${PROJECT_ROOT}" && bash -c "${fe_cmd}") || die "npm install failed"
  else
    say "${B}\$ ssh -p ${TESTBOX_PORT} ${TESTBOX_HOST} '${fe_cmd}'${R}"
    [[ "$DRY_RUN" == "1" ]] || ssh -p "${TESTBOX_PORT}" "${TESTBOX_HOST}" "${fe_cmd}" || die "npm install failed on testbox"
  fi

  ok "Setup complete (Python + frontend toolchains ready)"
}

# ── Subcommand: clean ──
cmd_clean() {
  heading "Cleaning stale test state and ports"
  local cmd='rm -rf ~/.hermes/webui-test-* 2>/dev/null; \
    for p in $(seq 20000 29999); do fuser -k "${p}/tcp" 2>/dev/null; done; \
    echo "cleaned"'
  if is_testbox; then
    say "${B}\$ ${cmd}${R}"
    [[ "$DRY_RUN" == "1" ]] && return 0
    eval "$cmd"
  else
    say "${B}\$ ssh -p ${TESTBOX_PORT} ${TESTBOX_HOST} '${cmd}'${R}"
    [[ "$DRY_RUN" == "1" ]] && return 0
    ssh -p "${TESTBOX_PORT}" "${TESTBOX_HOST}" "$cmd"
  fi
  ok "Clean complete"
}

# ── Subcommand: smoke ──
cmd_smoke() {
  heading "L1 Smoke (< 5 min)"
  local targets="tests/test_smoke.py tests/test_sprint1.py tests/test_chat_start_commands.py tests/test_regressions.py tests/test_pytest_state_isolation.py"
  run_pytest "smoke" "$targets"
}

# ── Subcommand: function ──
cmd_function() {
  local domain="${1:-all}"
  heading "L2 Functional area: ${domain} (10-30 min)"
  local targets
  targets=$(domain_targets "$domain")
  [[ -n "$targets" ]] || die "Empty target list for DOMAIN=$domain"
  run_pytest "function-${domain}" "$targets"
}

# ── Subcommand: full ──
cmd_full() {
  heading "L3 Full regression (hours)"
  run_pytest "full" "tests/"
}

# ── Subcommand: manual ──
cmd_manual() {
  heading "L4 Browser manual / agent"
  cat <<EOF

${B}Browser-driven regression${R} (not automatable in this repo).

Steps:
  1. Open  ${B}http://${TESTBOX_HOST}:2323${R}  in a browser
  2. Login with password  ${B}lingyue-lab${R}
  3. Walk through  ${B}TESTING.md${R}  sections in order:
       Section 11 (Visual/Layout)
       Section 25 (UI Polish)
       Section 26 (Resizable Panels)
       Section 30-32 (Skill / Cron / Memory creation)
       Section 35-36 (latest sprint additions, if any)
  4. Mark each scenario PASS / FAIL / BLOCKED

Alternative: a Claude browser agent can execute TESTING.md directly — see
the "Claude Browser Agent Instructions" section (lines ~556-573 of TESTING.md).

EOF
  ok "Instructions printed — manual execution is up to the runner"
}

# ── Subcommand: frontend ──
cmd_frontend() {
  local subcmd="${1:-smoke}"
  case "$subcmd" in
    smoke|full|setup|clean) ;;
    *) die "Unknown frontend SUBCMD: $subcmd. Run '$0 help' for valid values." ;;
  esac

  mkdir -p "${FRONTEND_RESULTS_DIR}"
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  local out="${FRONTEND_RESULTS_DIR}/junit-${subcmd}-${stamp}.xml"

  case "$subcmd" in
    setup)
      cmd_setup
      return $?
      ;;
    clean)
      heading "Cleaning frontend test artifacts"
      local cmd="rm -rf ${FRONTEND_DIR}/tests/.results ${FRONTEND_DIR}/node_modules/.vite 2>/dev/null; echo cleaned"
      if is_testbox; then
        say "${B}\$ ${cmd}${R}"
        [[ "$DRY_RUN" == "1" ]] && return 0
        eval "$cmd"
      else
        say "${B}\$ ssh -p ${TESTBOX_PORT} ${TESTBOX_HOST} '${cmd}'${R}"
        [[ "$DRY_RUN" == "1" ]] && return 0
        ssh -p "${TESTBOX_PORT}" "${TESTBOX_HOST}" "$cmd"
      fi
      ok "Frontend clean complete"
      return $?
      ;;
    smoke)
      heading "Frontend vitest (smoke)"
      local targets="tests/"
      local cmd="cd frontend && npx vitest run --reporter=verbose ${targets} -- --reporter=junit --reporter-output=${out} ${VITEST_EXTRA}"
      ;;
    full)
      heading "Frontend vitest (full)"
      local targets=""
      local cmd="cd frontend && npx vitest run --reporter=verbose ${VITEST_EXTRA} -- --reporter=junit --reporter-output=${out}"
      ;;
  esac

  # smoke / full: actually run vitest
  if is_testbox; then
    say "${B}\$ ${cmd}${R}"
    [[ "$DRY_RUN" == "1" ]] && return 0
    (cd "${PROJECT_ROOT}" && bash -c "${cmd}")
  else
    say "${B}\$ ssh -p ${TESTBOX_PORT} ${TESTBOX_HOST} '${cmd}'${R}"
    [[ "$DRY_RUN" == "1" ]] && return 0
    ssh -p "${TESTBOX_PORT}" "${TESTBOX_HOST}" "${cmd}"
  fi
}

# ── Dispatch ──
# No args → show help. Each level is opt-in so users see the available
# commands instead of triggering an implicit smoke run.
if [[ $# -eq 0 ]]; then
  cmd_help
  exit 0
fi

case "${1:-help}" in
  smoke)        cmd_smoke ;;
  function)    shift; cmd_function "${1:-all}" ;;
  full)        cmd_full ;;
  frontend)    shift; cmd_frontend "${1:-smoke}" ;;
  manual)      cmd_manual ;;
  setup)       cmd_setup ;;
  clean)       cmd_clean ;;
  help|-h|--help) cmd_help ;;
  *)           err "Unknown command: $1"; cmd_help; exit 1 ;;
esac