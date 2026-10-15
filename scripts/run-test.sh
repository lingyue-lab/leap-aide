#!/usr/bin/env bash
# run-test.sh — leap-aide regression test runner
#
# Runs the pytest + vitest suites locally. All commands assume the script is
# being executed on the host where leap-aide is installed (no ssh-jump).
#
# Usage:
#   ./scripts/run-test.sh                         # show help (no-op)
#   ./scripts/run-test.sh smoke                   # L1 smoke (< 5 min)
#   ./scripts/run-test.sh function [DOMAIN]       # L2 functional area (10-30 min)
#   ./scripts/run-test.sh full                    # L3 full regression (hours)
#   ./scripts/run-test.sh backend [SUBCMD]        # backend pytest (smoke | function | full | sprint | setup | clean)
#   ./scripts/run-test.sh frontend [SUBCMD]       # frontend vitest (smoke | full | clean | setup)
#   ./scripts/run-test.sh manual                  # L4 browser manual — prints instructions
#   ./scripts/run-test.sh setup                   # install all test deps (Python + frontend)
#   ./scripts/run-test.sh clean                   # wipe stale test state and ports
#   ./scripts/run-test.sh help                    # show this help
#
# DOMAIN (for L2): session | model | workspace | cron | i18n | ui |
#                  streaming | auth | all
#
# Environment overrides:
#   PYTEST_EXTRA="..."           # extra flags appended to pytest invocation
#   VITEST_EXTRA="..."           # extra flags appended to vitest invocation
#   DRY_RUN=1                    # print commands without executing
#
# Exit code: passed through from the runner (0 = all green).
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Ensure pytest from the project's .venv is on PATH.
# The testbox harness invokes this script with a clean shell that does not
# have .venv/bin prepended, so bare `pytest` invocations below would fail
# with "No such file or directory". Falling back silently keeps local
# development (where pytest may be installed elsewhere) working unchanged.
if [[ -x "${PROJECT_ROOT}/.venv/bin/pytest" ]]; then
  export PATH="${PROJECT_ROOT}/.venv/bin:${PATH}"
fi

RESULTS_DIR="${PROJECT_ROOT}/backend/tests/.results"
FRONTEND_DIR="${PROJECT_ROOT}/frontend"
FRONTEND_RESULTS_DIR="${FRONTEND_DIR}/tests/.results"
PYTEST_EXTRA="${PYTEST_EXTRA:-}"
VITEST_EXTRA="${VITEST_EXTRA:-}"
# PYTEST_SETUP_SHOW=1 → pass --setup-show to pytest so each fixture
# SETUP/TEARDOWN is printed. Use to verify why a session-scoped fixture
# is being re-entered mid-batch (the symptom was the test_server fixture
# firing its setup phase multiple times in a single pytest process).
PYTEST_SETUP_SHOW="${PYTEST_SETUP_SHOW:-0}"
DRY_RUN="${DRY_RUN:-0}"
# BATCH_SIZE is parsed by parse_runner_flags (CLI flag wins, then env, then 1).
# Leave empty here so parse_runner_flags can distinguish "unset" from "explicitly 1".
BATCH_SIZE="${BATCH_SIZE:-}"
# MAX_TESTS: cumulative test-case cap. 0 = no limit. Parsed by parse_runner_flags.
MAX_TESTS="${MAX_TESTS:-}"
# ONLY_BATCH: 1-indexed batch selector (run only this batch and exit).
# Empty = run all batches. Parsed by parse_runner_flags.
ONLY_BATCH="${ONLY_BATCH:-}"
# BATCH_SKIP: number of leading batches to skip before running.
# 0 = run all batches. Parsed by parse_runner_flags.
# Mutually exclusive with ONLY_BATCH (validated in parse_runner_flags).
BATCH_SKIP="${BATCH_SKIP:-}"
# FAIL_FAST: 1 = stop at the first failed batch. 0 = continue-on-failure (default).
FAIL_FAST="${FAIL_FAST:-0}"

# ── Runner flag parser ──
# Consumes --batch-size=N / --batch-size N / -b N (BATCH_SIZE),
#         --max-tests=N / --max-tests N (MAX_TESTS, 0 = no limit),
#         --only-batch=N / --only-batch N (ONLY_BATCH, 1-indexed, run only this batch),
#         --batch-skip=N / --batch-skip N (BATCH_SKIP, 0 = no skip; mutually exclusive with ONLY_BATCH),
#         --fail-fast (FAIL_FAST=1, abort on first failed batch)
# from $@, validates each value, and stores the remaining args in the GLOBAL
# array `_REMAINING_ARGS`. Bash functions cannot mutate the caller's `$@`
# (subshell-style scope), so the caller must follow up with
# `set -- "${_REMAINING_ARGS[@]}"` to apply the rewrites.
# Precedence: CLI flag > env var > default.
parse_runner_flags() {
  _REMAINING_ARGS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --batch-size=*)
        BATCH_SIZE="${1#--batch-size=}"
        shift
        ;;
      --batch-size)
        [[ $# -ge 2 ]] || die "--batch-size requires a value"
        BATCH_SIZE="$2"
        shift 2
        ;;
      -b)
        [[ $# -ge 2 ]] || die "-b requires a value"
        BATCH_SIZE="$2"
        shift 2
        ;;
      --max-tests=*)
        MAX_TESTS="${1#--max-tests=}"
        shift
        ;;
      --max-tests)
        [[ $# -ge 2 ]] || die "--max-tests requires a value"
        MAX_TESTS="$2"
        shift 2
        ;;
      --only-batch=*)
        ONLY_BATCH="${1#--only-batch=}"
        shift
        ;;
      --only-batch)
        [[ $# -ge 2 ]] || die "--only-batch requires a value"
        ONLY_BATCH="$2"
        shift 2
        ;;
      --batch-skip=*)
        BATCH_SKIP="${1#--batch-skip=}"
        shift
        ;;
      --batch-skip)
        [[ $# -ge 2 ]] || die "--batch-skip requires a value"
        BATCH_SKIP="$2"
        shift 2
        ;;
      --fail-fast)
        FAIL_FAST=1
        shift
        ;;
      --)
        shift
        while [[ $# -gt 0 ]]; do _REMAINING_ARGS+=("$1"); shift; done
        break
        ;;
      *)
        _REMAINING_ARGS+=("$1")
        shift
        ;;
    esac
  done
  BATCH_SIZE="${BATCH_SIZE:-100}"
  MAX_TESTS="${MAX_TESTS:-0}"
  ONLY_BATCH="${ONLY_BATCH:-}"
  BATCH_SKIP="${BATCH_SKIP:-0}"
  FAIL_FAST="${FAIL_FAST:-0}"

  if ! [[ "$BATCH_SIZE" =~ ^[0-9]+$ ]] || [[ "$BATCH_SIZE" -lt 1 ]]; then
    die "--batch-size must be a positive integer (got: '${BATCH_SIZE}')"
  fi
  if ! [[ "${MAX_TESTS}" =~ ^[0-9]+$ ]]; then
    die "--max-tests must be a non-negative integer (got: '${MAX_TESTS}')"
  fi
  if [[ -n "$ONLY_BATCH" ]]; then
    if ! [[ "$ONLY_BATCH" =~ ^[0-9]+$ ]] || [[ "$ONLY_BATCH" -lt 1 ]]; then
      die "--only-batch must be a positive integer (got: '${ONLY_BATCH}')"
    fi
  fi
  if ! [[ "$BATCH_SKIP" =~ ^[0-9]+$ ]]; then
    die "--batch-skip must be a non-negative integer (got: '${BATCH_SKIP}')"
  fi
  if [[ -n "$ONLY_BATCH" && "$BATCH_SKIP" -gt 0 ]]; then
    die "--only-batch and --batch-skip cannot be used together (got --only-batch=${ONLY_BATCH} --batch-skip=${BATCH_SKIP})"
  fi
  if ! [[ "$FAIL_FAST" =~ ^[01]$ ]]; then
    die "--fail-fast expects no value (env FAIL_FAST must be 0 or 1, got: '${FAIL_FAST}')"
  fi
}

# ── set -e carve-out ──
# set -euo pipefail is enabled at the top. The ONE exception is inside
# run_pytest_batched() and run_pytest_batch(): the per-batch pytest call
# runs under `&` + `wait` with explicit `$?` capture, so a batch failure
# never aborts the parent. The script-level exit code reflects whether
# ANY batch failed (see run_pytest_batched's final `[[ $fail -eq 0 ]]`).
# Any non-zero exit OUTSIDE that carve-out is a real bug.

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
  $0                          # show help (no-op, no test executed)
  $0 smoke                    # L1 smoke (< 5 min)
  $0 function [DOMAIN]        # L2 functional area (10-30 min)
  $0 full                     # L3 full regression (hours)
  $0 sprint N                 # run a single sprint file (e.g. "sprint 29", "sprint 20b")
  $0 backend [SUBCMD]         # backend pytest (smoke | function | full | sprint | setup | clean)
  $0 frontend [SUBCMD]        # frontend vitest (smoke | full | clean | setup)
  $0 manual                   # L4 browser manual — prints instructions
  $0 setup                    # install ALL test deps (Python + frontend)
  $0 clean                    # wipe stale test state dirs and ports
  $0 help                     # show this help

${B}DOMAIN values for L2:${R}
  session   model   workspace   cron   i18n
  ui        auth   all

${B}Backend SUBCMD:${R}
  (default)           alias for smoke
  smoke               L1 smoke (< 5 min)
  function [DOMAIN]   L2 functional area (10-30 min)
  full                L3 full regression (hours)
  sprint N            run a single sprint file (e.g. "sprint 29")
  setup               install ALL test deps (Python + frontend)
  clean               wipe stale test state dirs and ports

${B}Frontend SUBCMD:${R}
  (default)   alias for smoke
  smoke       run the existing vitest tests in frontend/tests/
  full        run vitest in --watch=false mode over the whole frontend tree
  clean       remove frontend/tests/.results/
  setup       install vitest + testing-library + jsdom into frontend/node_modules

${B}Environment:${R}
  PYTEST_EXTRA="..."           # extra pytest flags (e.g. "-x -k smoke")
  VITEST_EXTRA="..."           # extra vitest flags (e.g. "--reporter=verbose")
  PYTEST_SETUP_SHOW=1          # pass --setup-show to pytest (debug fixture lifecycle)
  DRY_RUN=1                    # print command without executing
  BATCH_SIZE=N                 # tests per pytest invocation (default 1)
  MAX_TESTS=N                  # cumulative test-case cap; 0 = no limit (default)
  ONLY_BATCH=N                 # 1-indexed; run only this batch and exit
  BATCH_SKIP=N                 # skip first N batches; run all remaining (default 0)
  FAIL_FAST=1                  # 1 = stop at first failed batch (default 0)

${B}Batching (per-file pytest invocations):${R}
  BATCH_SIZE=N             # env var: tests per pytest invocation (default 1)
  --batch-size=N           # CLI flag (overrides env var)
  -b N                     # short alias
  Must be a positive integer >= 1. --batch-size=0 / =abc / =-1 are rejected.

${B}Selective run flags:${R}
  --max-tests=N / MAX_TESTS=N
      Cap on cumulative test cases (parsed from each per-batch junit XML's
      <testsuite tests="...">). Checked AFTER each batch finishes — within
      a single batch, all collected tests run. Defaults to 0 (no limit).
  --only-batch=N / ONLY_BATCH=N
      Run only batch N (1-indexed) and exit. Out-of-range values die with
      a clear error. Useful for re-running a single failing batch.
  --batch-skip=N / BATCH_SKIP=N
      Skip the first N batches and run all remaining ones. 1-indexed in
      batch units (NOT file units): with BATCH_SIZE=10 and 50 files,
      --batch-skip=2 skips the first 20 files / 2 batches, then runs
      batches 3..5. Out-of-range values die with a clear error (refuses
      to skip every batch). Mutually exclusive with --only-batch.
      Defaults to 0 (no skip).
  --fail-fast / FAIL_FAST=1
      Abort on the first failed batch. Default is continue-on-failure
      (all batches run, summary reflects total failures). FAIL_FAST must
      be 0 or 1; any other value is rejected.

${B}Per-batch output files:${R}
  backend/tests/.results/junit-<level>-batchNN-<timestamp>.xml   # one per batch
  Final aggregated →       backend/tests/.results/junit-<level>-<timestamp>.xml
  (NN zero-padded batch index, e.g. batch01, batch02.)

${B}Behavior on batch failure:${R}
  Per-batch junit XML is always written. The orchestrator continues
  with remaining batches (unless --only-batch / --max-tests / --fail-fast stops it earlier).
  Final script exit code = 1 iff any batch failed.

${B}Note:${R}
  Each batch is a fresh pytest invocation, which re-spawns the
  session-scoped test_server fixture (~3-5s/file). For BATCH_SIZE=1
  on a 99-file suite, expect ~5-8 min of overhead vs. the legacy
  single-invocation path. Cross-file state in state.db is reset
  between batches (conftest wipes TEST_STATE_DIR on each entry).

${B}Results:${R}
  Backend junit XML  → backend/tests/.results/junit-<level>-<timestamp>.xml
  Frontend junit XML → frontend/tests/.results/junit-<subcmd>-<timestamp>.xml
EOF
}

# ── Scanner: enumerate backend/tests/test_*.py under a directory ──
# Emit one absolute path per line, sorted. -maxdepth 1 keeps us at the top
# of backend/tests/ (no recursion into helpers); -name 'test_*.py' excludes
# conftest.py / _pytest_port.py / __init__.py.
scan_test_files() {
  local root_dir="$1"
  find "${root_dir}" -maxdepth 1 -type f -name 'test_*.py' \
    | LC_ALL=C sort
}

# ── Target resolver: canonical file list per subcommand ──
# Output: one PROJECT_ROOT-relative path per line, ready for the orchestrator.
# Glob patterns (e.g. backend/tests/test_session_*.py from domain_targets) are
# expanded HERE so the orchestrator can pass literal paths to pytest.
# nullglob is intentionally NOT set: an unresolved pattern must surface
# as a literal token so pytest reports the missing file clearly.
resolve_targets() {
  local cmd_name="$1"; local arg="${2:-}"
  case "$cmd_name" in
    smoke)
      printf '%s\n' \
        backend/tests/test_smoke.py \
        backend/tests/test_api_sessions_crud.py \
        backend/tests/test_chat_start_commands.py \
        backend/tests/test_regressions.py \
        backend/tests/test_pytest_state_isolation.py
      ;;
    function)
      # domain_targets emits space/newline-separated words (with globs).
      # Iterate word-by-word, expand each glob, dedupe, sort.
      local pat f
      for pat in $(domain_targets "${arg:-all}"); do
        # If $pat is a literal (no glob chars), the inner loop runs once
        # and prints it as-is. If it contains *, the shell expands.
        for f in $pat; do printf '%s\n' "$f"; done
      done | LC_ALL=C sort -u
      ;;
    full)
      scan_test_files "${PROJECT_ROOT}/backend/tests" \
        | sed "s#^${PROJECT_ROOT}/##"
      ;;
    sprint)
      cmd_sprint_resolve "$arg"
      ;;
    *)
      die "resolve_targets: unknown cmd '${cmd_name}'"
      ;;
  esac
}

# ── Sprint target resolver ──
# Extracted from the original cmd_sprint. Emits one PROJECT_ROOT-relative
# path per line. Glob expansion happens here (not in the orchestrator).
# Validation lives in cmd_sprint (main shell) — calling this via process
# substitution (`< <(...)`) would silently swallow any `die` here.
cmd_sprint_resolve() {
  local arg="$1"

  local primary="backend/tests/test_sprint${arg}.py"
  local b_suffix="backend/tests/test_sprint${arg}b.py"
  # backend/tests/test_sprint${arg}_*.py — e.g. _ui_polish, _title_autogen.
  local -a underscore_files=()
  local f
  for f in "${PROJECT_ROOT}"/backend/tests/test_sprint${arg}_*.py; do
    [[ -f "$f" && "$f" != *"*"* ]] && underscore_files+=( "${f#${PROJECT_ROOT}/}" )
  done
  if [[ ${#underscore_files[@]} -gt 0 ]]; then
    local IFS=$'\n'
    underscore_files=( $(printf '%s\n' "${underscore_files[@]}" | LC_ALL=C sort) )
    unset IFS
  fi

  [[ -f "${PROJECT_ROOT}/${primary}" ]]  && printf '%s\n' "$primary"
  [[ -f "${PROJECT_ROOT}/${b_suffix}" ]] && printf '%s\n' "$b_suffix"
  local f2
  for f2 in "${underscore_files[@]}"; do printf '%s\n' "$f2"; done
}

# ── JUnit XML aggregator (stdlib only, no new deps) ──
# Merges N per-batch junit XMLs into one. Each input's <testsuites> children
# are renamed to batchNN-<original_name> to avoid testcase-name collisions
# across batches, then re-rooted under a single <testsuites> with summed
# attributes (tests / errors / failures / skipped / time).
aggregate_junit_xml() {
  local inputs=("$@")
  local output="${inputs[-1]}"
  unset 'inputs[-1]'
  local p
  for p in "${inputs[@]}"; do
    [[ -s "$p" ]] || { err "Missing/empty junit XML: $p"; return 1; }
  done
  python3 - "$output" "${inputs[@]}" <<'PYEOF'
import sys, xml.etree.ElementTree as ET

output = sys.argv[1]
inputs = sys.argv[2:]

agg = ET.Element('testsuites')
agg.set('tests', '0')
agg.set('errors', '0')
agg.set('failures', '0')
agg.set('skipped', '0')
agg.set('time', '0.0')

def add(attr, val):
    agg.set(attr, str(float(agg.get(attr, '0')) + float(val)))

for idx, p in enumerate(inputs):
    tree = ET.parse(p)
    for ts in list(tree.getroot()):
        original_name = ts.get('name', f'batch{idx}')
        ts.set('name', f'batch{idx + 1}-{original_name}')
        for k in ('tests', 'errors', 'failures', 'skipped'):
            if k in ts.attrib:
                add(k, ts.attrib[k])
        if 'time' in ts.attrib:
            add('time', ts.attrib['time'])
        agg.append(ts)

ET.ElementTree(agg).write(output, encoding='utf-8', xml_declaration=True)
PYEOF
}

# ── Orchestrator: scan → batched pytest → aggregate XML ──
# run_pytest_batched <level> <target_array_name>
# Second arg is the NAME of a caller bash array (passed by nameref).
# Batches the array into chunks of $BATCH_SIZE files, invokes pytest once
# per chunk, aggregates the per-batch junit XMLs into the final
# `junit-<level>-<stamp>.xml` (or copies when only one batch).
#
# set -e carve-out: this function uses `&` + `wait` + explicit `$?` capture
# around each pytest call so a failing batch never aborts the parent. The
# final `[[ $fail -eq 0 ]]` decides the script-level exit code.
run_pytest_batched() {
  local level="$1"
  local -n files_ref="$2"   # bash 4.3+ nameref
  local batch_size="${BATCH_SIZE}"
  local max_tests="${MAX_TESTS}"
  local only_batch="${ONLY_BATCH:-}"
  local batch_skip="${BATCH_SKIP:-0}"
  local total="${#files_ref[@]}"

  if [[ $total -eq 0 ]]; then
    die "run_pytest_batched: empty target list for level=${level}"
  fi

  mkdir -p "${RESULTS_DIR}"
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)"
  local num_batches=$(( (total + batch_size - 1) / batch_size ))
  say "Scanned ${total} test files; running in ${num_batches} batch(es) of size ${batch_size}."

  # Validate ONLY_BATCH against the actual batch count.
  if [[ -n "$only_batch" ]]; then
    if [[ "$only_batch" -gt "$num_batches" ]]; then
      die "--only-batch=${only_batch} out of range (only ${num_batches} batch(es) at BATCH_SIZE=${batch_size})"
    fi
    say "Only-batch mode: skipping to batch ${only_batch} of ${num_batches}."
  fi
  # Validate BATCH_SKIP against the actual batch count. Mutually exclusive
  # with ONLY_BATCH (enforced in parse_runner_flags), so this branch only
  # runs when only_batch is empty.
  if [[ "$batch_skip" -gt 0 ]]; then
    if [[ "$batch_skip" -ge "$num_batches" ]]; then
      die "--batch-skip=${batch_skip} out of range (only ${num_batches} batch(es) at BATCH_SIZE=${batch_size}; refusing to skip every batch)"
    fi
    say "Batch-skip mode: skipping first ${batch_skip} batch(es); running batches $((batch_skip + 1))..${num_batches}."
  fi
  if [[ "$max_tests" -gt 0 ]]; then
    say "Max-tests mode: stopping after cumulative ${max_tests} test case(s)."
  fi

  local -a per_batch_xmls=()
  local -a per_batch_rcs=()
  local cumulative_tests=0
  local stop_reason=""
  local i=0 batch_idx=0

  # When ONLY_BATCH is set, jump straight to the target slice; we break
  # out of the loop immediately after running it (see below).
  if [[ -n "$only_batch" ]]; then
    i=$(( (only_batch - 1) * batch_size ))
    [[ $i -gt $total ]] && i=$total
    batch_idx=$((only_batch - 1))
  fi

  # When BATCH_SKIP is set, advance past the first N batches; the loop
  # then continues normally with the remaining batches (no early break).
  # Mutually exclusive with ONLY_BATCH, so this block only runs alone.
  if [[ "$batch_skip" -gt 0 ]]; then
    i=$(( batch_skip * batch_size ))
    [[ $i -gt $total ]] && i=$total
    batch_idx=$batch_skip
  fi

  while [[ $i -lt $total ]]; do
    batch_idx=$((batch_idx + 1))
    local end=$(( i + batch_size ))
    [[ $end -gt $total ]] && end=$total

    local -a batch=("${files_ref[@]:i:end-i}")

    local batch_xml="${RESULTS_DIR}/junit-${level}-batch$(printf '%02d' "${batch_idx}")-${stamp}.xml"
    # PYTHONUNBUFFERED=1 forces line-buffered stdout/stderr so pytest's
    # summary line ("=== N passed, M failed in Ts ===") survives even when
    # wrapped in `nohup ... &` / detached shells. Deliberately no global
    # --timeout=60: pytest-timeout's os._exit(1) tears down the session-
    # scoped test_server fixture mid-run; use `@pytest.mark.timeout(N)`
    # on individual tests for per-test timeouts.
    local cmd=(
      "env" "PYTHONUNBUFFERED=1"
      "pytest" "${batch[@]}"
      "-v" "--junitxml=${batch_xml}"
      $([[ "${PYTEST_SETUP_SHOW}" == "1" ]] && echo "--setup-show")
      ${PYTEST_EXTRA}
    )

    heading "Batch ${batch_idx}/${num_batches}"
    say "${B}\$ cd ${PROJECT_ROOT} && ${cmd[*]}${R}"

    local rc=0
    if [[ "${DRY_RUN}" != "1" ]]; then
      _run_test_log_entry "pytest_batch_start idx=${batch_idx} level=${level}"
      cd "${PROJECT_ROOT}"
      "${cmd[@]}" &
      _PYTEST_CHILD_PID=$!
      # `|| rc=$?` is REQUIRED under `set -e`: a bare `wait $pid; rc=$?`
      # aborts the script because the failing `wait` isn't part of any
      # exception construct. Putting `wait` on the LHS of `||` makes it
      # part of a `||` list, exempting it from the -e exit rule.
      wait "${_PYTEST_CHILD_PID}" || rc=$?
      _PYTEST_CHILD_PID=""
      _run_test_log_entry "pytest_batch_done idx=${batch_idx} rc=${rc}"
    fi
    per_batch_xmls+=("${batch_xml}")
    per_batch_rcs+=("${rc}")
    if [[ $rc -eq 0 ]]; then
      ok "Batch ${batch_idx}/${num_batches} PASS"
    else
      err "Batch ${batch_idx}/${num_batches} FAIL (rc=${rc}); see ${batch_xml}"
      if [[ "${FAIL_FAST}" == "1" ]]; then
        stop_reason="fail-fast: batch ${batch_idx} failed (rc=${rc})"
        i=$end   # advance so loop exits cleanly
        break
      fi
    fi

    # Count tests in this batch (skip in DRY_RUN — XML doesn't exist).
    # Pytest writes <testsuite tests="N"> even on failure, so we can rely
    # on this even when rc != 0.
    if [[ "$max_tests" -gt 0 && "${DRY_RUN}" != "1" && -f "${batch_xml}" ]]; then
      # Don't use `local batch_tests=$(...)` — bash's `local` builtin with
      # command substitution has assignment-timing quirks. Declare then
      # assign (same pattern as `local stamp; stamp=...`).
      local batch_tests
      batch_tests=$(python3 -c "
import xml.etree.ElementTree as ET
t = ET.parse('${batch_xml}')
print(sum(int(ts.get('tests', 0)) for ts in t.getroot()))
" 2>/dev/null || echo 0)
      cumulative_tests=$((cumulative_tests + batch_tests))
      if [[ $cumulative_tests -ge $max_tests ]]; then
        stop_reason="max-tests reached (${cumulative_tests}/${max_tests})"
        i=$end   # advance so loop exits cleanly
        break
      fi
    fi
    i=$end

    # ONLY_BATCH: after the target batch finishes, exit immediately.
    if [[ -n "$only_batch" ]]; then
      stop_reason="--only-batch=${only_batch} requested"
      break
    fi
  done

  # Aggregate (skip in DRY_RUN — per-batch XMLs don't exist)
  local final_xml="${RESULTS_DIR}/junit-${level}-${stamp}.xml"
  if [[ "${DRY_RUN}" != "1" ]]; then
    if [[ ${#per_batch_xmls[@]} -eq 1 ]]; then
      cp -f "${per_batch_xmls[0]}" "${final_xml}"
    else
      aggregate_junit_xml "${per_batch_xmls[@]}" "${final_xml}"
    fi
  fi

  # Tally
  local pass=0 fail=0
  for rc in "${per_batch_rcs[@]}"; do
    [[ $rc -eq 0 ]] && pass=$((pass+1)) || fail=$((fail+1))
  done
  if [[ $fail -eq 0 ]]; then
    ok "All ${pass} batch(es) passed."
    [[ "${DRY_RUN}" != "1" ]] && say "  Aggregated XML at ${final_xml}"
  else
    err "${fail}/${num_batches} batch(es) failed, ${pass} passed."
    [[ "${DRY_RUN}" != "1" ]] && say "  Aggregated XML at ${final_xml}"
  fi
  if [[ -n "$stop_reason" ]]; then
    warn "Stopped early: ${stop_reason}"
  fi
  [[ $fail -eq 0 ]]
}

# ── DOMAIN → pytest target mapping ──
domain_targets() {
  local domain="${1:-all}"
  case "$domain" in
    session)   echo "backend/tests/test_session_*.py backend/tests/test_sessions_api.py backend/tests/test_gateway_sessions.py backend/tests/test_gateway_sync.py backend/tests/test_parallel_session_switch.py backend/tests/test_empty_session_no_disk_write.py" ;;
    model)     echo "backend/tests/test_provider_management.py backend/tests/test_credential_pool_providers.py backend/tests/test_model_resolver.py backend/tests/test_byok_model_dropdown.py backend/tests/test_provider_mismatch.py backend/tests/test_minimax_provider.py" ;;
    workspace) echo "backend/tests/test_workspace_*.py backend/tests/test_default_workspace_fallback.py backend/tests/test_workspace_blocked_roots_macos.py" ;;
    cron)      echo "backend/tests/test_cron_*.py" ;;
    i18n)      echo "backend/tests/test_chinese_locale.py" ;;
    ui)        echo "backend/tests/test_pwa_manifest_csp.py" ;;
    auth)      echo "backend/tests/test_auth_*.py" ;;
    all)
      # Concatenate every domain except 'all' itself.
      for d in session model workspace cron i18n ui auth; do
        domain_targets "$d"
      done
      ;;
    *) die "Unknown DOMAIN: $domain. Run '$0 help' for valid values." ;;
  esac
}

# ── Run pytest locally ──
# Note: deliberately NO `( ... )` subshell. Bash subshells inherit the
# parent's argv, so `ps` shows them as duplicate "bash run-test.sh full"
# processes during the run, which is confusing and looks like a recursive
# invocation. Running cd + eval in the current shell keeps ps output clean.
#
# LOOP-REENTRY LOG: each top-level invocation of run-test.sh writes a line to
# /tmp/leap-aide-run-test-events.log with PID/PPID/start-time/caller argv.
# If you see this log show run-test.sh being invoked many times back-to-back,
# the loop is being driven by an external wrapper (cron, nohup restart loop,
# test-harness watchdog) — pytest itself never re-enters its session lifecycle
# from inside one process (see conftest.py fixture event log for the matching
# symptoms on the pytest side).
_RUN_TEST_EVENT_LOG="/tmp/leap-aide-run-test-events.log"

# Read /proc/<pid>/cmdline into a variable named by $2. NUL-separated argv
# entries are joined with single spaces and truncated to 200 chars to match
# the legacy `ps -o args= … | cut -c1-200` behavior. No fork — bash builtins
# only (read -d '' requires bash 4.4+; Ubuntu 22.04+ ships bash 5+).
# Avoids the `ps | tr | cut` pipeline that otherwise shows up as a
# transient `bash run-test.sh …` subshell in `ps` between batches.
_read_cmdline_into() {
  local pid="$1" var_name="$2"
  local cmdline="?"
  if [[ -r "/proc/${pid}/cmdline" ]]; then
    local -a parts=()
    # shellcheck disable=SC2207
    while IFS= read -r -d '' part; do parts+=("$part"); done \
      < "/proc/${pid}/cmdline" 2>/dev/null || true
    if [[ ${#parts[@]} -gt 0 ]]; then
      cmdline="${parts[*]}"
    fi
  fi
  printf -v "${var_name}" '%s' "${cmdline:0:200}"
}

_run_test_log_entry() {
  local reason="${1:-enter}"
  local ppid="${PPID:-?}"
  local self_pid="$$"
  local start_epoch="${_RUN_TEST_START_EPOCH:-$(date +%s)}"
  local caller_argv self_argv
  _read_cmdline_into "${ppid}"  caller_argv
  _read_cmdline_into "${self_pid}" self_argv
  local now
  now="$(date '+%Y-%m-%d %H:%M:%S')"
  {
    printf '[RUN_TEST %s] reason=%s pid=%s ppid=%s start_epoch=%s now=%s\n' \
      "${now}" "${reason}" "${self_pid}" "${ppid}" "${start_epoch}" "${now}"
    printf '  self_argv  : %s\n' "${self_argv}"
    printf '  parent_argv: %s\n' "${caller_argv}"
  } >> "${_RUN_TEST_EVENT_LOG}" 2>/dev/null || true
}
_RUN_TEST_START_EPOCH="$(date +%s)"
_run_test_log_entry "script_start"

# If something external SIGTERMs this script while pytest is running, the
# trap fires, logs the reason, and tries to clean up the pytest child so we
# don't leave a half-dead server fixture around.
_run_test_on_exit() {
  local rc=$?
  local sig="${_RUN_TEST_LAST_SIG:-none}"
  _run_test_log_entry "script_exit rc=${rc} sig=${sig}"
  # Best-effort: kill the pytest child process group if it's still alive.
  if [[ -n "${_PYTEST_CHILD_PID:-}" ]] && kill -0 "${_PYTEST_CHILD_PID}" 2>/dev/null; then
    kill -TERM "-${_PYTEST_CHILD_PID}" 2>/dev/null || kill -TERM "${_PYTEST_CHILD_PID}" 2>/dev/null || true
  fi
  return "${rc}"
}
trap '_RUN_TEST_LAST_SIG=EXIT; _run_test_on_exit' EXIT
trap '_RUN_TEST_LAST_SIG=INT; exit 130' INT
trap '_RUN_TEST_LAST_SIG=TERM; exit 143' TERM
trap '_RUN_TEST_LAST_SIG=HUP; exit 129' HUP

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
  if [[ "$DRY_RUN" == "1" ]]; then
    say "${B}\$ _bootstrap_pip ${py_bin}${R}"
  else
    _bootstrap_pip "${py_bin}" || die "Failed to bootstrap pip into ${py_bin}"
  fi

  heading "Installing pytest toolchain (pyyaml, pytest, pytest-timeout) via ${py_bin}"
  # `--break-system-packages` is required on PEP 668 systems (Debian/Ubuntu 22.04+,
  # the testbox base image) and a no-op on older pip versions.
  #
  # pytest-timeout is installed so individual tests can opt in via the
  # `@pytest.mark.timeout(N)` mark, but the runner does NOT pass a global
  # --timeout flag — see run_pytest_batched for why a blanket wall tears
  # down the session-scoped test_server fixture.
  local py_cmd="${py_bin} -m pip install --quiet --break-system-packages pyyaml pytest pytest-timeout"
  say "${B}\$ ${py_cmd}${R}"
  [[ "$DRY_RUN" == "1" ]] || eval "$py_cmd" || die "pip install failed"

  heading "Installing frontend vitest toolchain (vitest, @testing-library/*, jsdom)"
  # Frontend deps are devDependencies; we install them only if frontend/package.json
  # does not already declare them, so the script is idempotent against package.json
  # edits that move them between deps and devDeps.
  #
  # Version pins (vitest@^3) are required because leap-aide pins vite@^5 and
  # vitest@4+ requires vite@^6. The npm peer-dep check would otherwise fail
  # with ERESOLVE. See https://vitest.dev/guide/migration for the matrix.
  local fe_cmd="cd frontend && npm install --save-dev --silent vitest@^3.0.0 @testing-library/react@^14.2.0 @testing-library/jest-dom@^6.4.0 jsdom@^24.0.0"
  say "${B}\$ ${fe_cmd}${R}"
  [[ "$DRY_RUN" == "1" ]] || (cd "${PROJECT_ROOT}" && bash -c "${fe_cmd}") || die "npm install failed"

  ok "Setup complete (Python + frontend toolchains ready)"
}

# ── Subcommand: clean ──
cmd_clean() {
  heading "Cleaning stale test state and ports"
  local cmd='rm -rf ~/.hermes/webui-test-* 2>/dev/null; \
    for p in $(seq 20000 29999); do fuser -k "${p}/tcp" 2>/dev/null; done; \
    echo "cleaned"'
  say "${B}\$ ${cmd}${R}"
  [[ "$DRY_RUN" == "1" ]] && return 0
  eval "$cmd"
  ok "Clean complete"
}

# ── Subcommand: smoke ──
cmd_smoke() {
  heading "L1 Smoke (< 5 min)"
  local -a targets=()
  mapfile -t targets < <(resolve_targets smoke)
  run_pytest_batched "smoke" targets
}

# ── Subcommand: function ──
cmd_function() {
  local domain="${1:-all}"
  heading "L2 Functional area: ${domain} (10-30 min)"
  local -a targets=()
  mapfile -t targets < <(resolve_targets function "$domain")
  [[ ${#targets[@]} -gt 0 ]] || die "Empty target list for DOMAIN=$domain"
  run_pytest_batched "function-${domain}" targets
}

# ── Subcommand: full ──
cmd_full() {
  heading "L3 Full regression (hours)"
  local -a targets=()
  mapfile -t targets < <(resolve_targets full)
  [[ ${#targets[@]} -gt 0 ]] || die "No test files found under backend/tests/"
  run_pytest_batched "full" targets
}

# ── Subcommand: manual ──
cmd_manual() {
  heading "L4 Browser manual / agent"
  cat <<EOF

${B}Browser-driven regression${R} (not automatable in this repo).

Steps:
  1. Open the leap-aide URL in a browser
  2. Login with the configured password
  3. Walk through  ${B}docs/testing.md${R}  sections in order:
       Section 11 (Visual/Layout)
       Section 25 (UI Polish)
       Section 26 (Resizable Panels)
       Section 30-32 (Skill / Cron / Memory creation)
       Section 35-36 (latest sprint additions, if any)
  4. Mark each scenario PASS / FAIL / BLOCKED

Alternative: a Claude browser agent can execute docs/testing.md directly — see
the "Claude Browser Agent Instructions" section (lines ~556-573 of docs/testing.md).

EOF
  ok "Instructions printed — manual execution is up to the runner"
}

# ── Subcommand: sprint ──
# Run a single backend/tests/test_sprintNN.py file (or its sibling `b` variant).
# Sprint 1 is also covered by `smoke`; this command is the only way to run
# any of the other sprints individually without dragging in the full suite.
cmd_sprint() {
  local arg="${1:-}"
  # Validate up-front in the main shell. cmd_sprint_resolve runs in a
  # process-substitution subshell (`< <(...)`), so its `die` would only
  # exit that subshell — the parent would silently continue with an empty
  # target list.
  [[ -n "$arg" ]] || die "Usage: $0 sprint N    (e.g. 'sprint 29' or 'sprint 20b')"
  if ! [[ "$arg" =~ ^[0-9]+[a-zA-Z]?$ ]]; then
    die "Invalid sprint id: '$arg'. Expected digits with optional one-letter suffix (e.g. 29, 20b)."
  fi
  local -a targets=()
  mapfile -t targets < <(cmd_sprint_resolve "$arg")
  if [[ ${#targets[@]} -eq 0 ]]; then
    die "No test file matches sprint '$arg' (looked for backend/tests/test_sprint${arg}.py${arg:+ / backend/tests/test_sprint${arg}b.py} or backend/tests/test_sprint${arg}_*.py)"
  fi
  heading "Sprint ${arg} (${#targets[@]} file(s): ${targets[*]})"
  run_pytest_batched "sprint-${arg}" targets
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
      say "${B}\$ ${cmd}${R}"
      [[ "$DRY_RUN" == "1" ]] && return 0
      eval "$cmd"
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
  say "${B}\$ ${cmd}${R}"
  [[ "$DRY_RUN" == "1" ]] && return 0
  (cd "${PROJECT_ROOT}" && bash -c "${cmd}")
}

# ── Subcommand: backend ──
# Namespaced alias for the backend (pytest) levels so a caller that drives both
# halves picks them the same way:
#   ./scripts/run-test.sh backend full      # pytest suites
#   ./scripts/run-test.sh frontend full     # vitest
# It forwards to the existing top-level commands untouched — bare `smoke` /
# `full` / `function` / `sprint` / `setup` / `clean` stay canonical (docs,
# README and the lab scripts all call them), so this only *adds* a namespace.
# Default is `smoke`, mirroring cmd_frontend: `full` runs for hours and must be
# asked for explicitly.
#
# Sub-arguments are read from $2 rather than shifted: under `set -euo pipefail`
# a bare `shift` on an empty positional list returns 1 and aborts the script.
cmd_backend() {
  local subcmd="${1:-smoke}"
  case "$subcmd" in
    smoke)    cmd_smoke ;;
    function) cmd_function "${2:-all}" ;;
    full)     cmd_full ;;
    sprint)   cmd_sprint "${2:-}" ;;
    setup)    cmd_setup ;;
    clean)    cmd_clean ;;
    *)        err "Unknown backend SUBCMD: ${subcmd}"; cmd_help; exit 1 ;;
  esac
}

# ── Dispatch ──
# Parse --batch-size / --max-tests / --only-batch first so the dispatch case
# below doesn't see them. CLI flag > env var > default.
parse_runner_flags "$@"
# Apply the remaining args (parse_runner_flags writes to a global because
# bash functions don't propagate `$@` mutations back to the caller).
set -- "${_REMAINING_ARGS[@]}"

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
  sprint)      shift; cmd_sprint "${1:-}" ;;
  backend)     shift; cmd_backend "$@" ;;
  frontend)    shift; cmd_frontend "${1:-smoke}" ;;
  manual)      cmd_manual ;;
  setup)       cmd_setup ;;
  clean)       cmd_clean ;;
  help|-h|--help) cmd_help ;;
  *)           err "Unknown command: $1"; cmd_help; exit 1 ;;
esac