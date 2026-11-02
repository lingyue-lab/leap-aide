#!/bin/bash

# docker-entrypoint.sh — runtime entrypoint for the leap-aide container
#
# Responsibilities:
#   1. Validate ENV_IGNORELIST / ENV_OBFUSCATE_PART
#   2. Detect WANTED_UID/WANTED_GID from mounted volumes
#   3. Swap from leap-aide-init (UID 1025) to leap-aide (UID dynamically adjusted)
#   4. Align /app ownership with the runtime UID (the tree is baked into the image)
#   5. Provision state dir, workspace dir
#   6. Locate the hermes-agent venv built at image-build time (fail fast if
#      missing — no runtime install fallback by design)
#   7. Start the leap-aide server
#
# Run as root (the Dockerfile USER is leap-aide-init which has passwordless sudo).
# Exit codes: 0 success, non-zero error.

set -e

error_exit() {
  echo -n "!! ERROR: "
  echo $*
  echo "!! Exiting script (ID: $$)"
  exit 1
}

ok_exit() {
  echo $*
  echo "++ Exiting script (ID: $$)"
  exit 0
}

## Environment variables loaded when passing environment variables from user to user
# Ignore list: variables to ignore when loading environment variables from user to user
export ENV_IGNORELIST="HOME PWD USER SHLVL TERM OLDPWD SHELL _ SUDO_COMMAND HOSTNAME LOGNAME MAIL SUDO_GID SUDO_UID SUDO_USER CHECK_NV_CUDNN_VERSION VIRTUAL_ENV VIRTUAL_ENV_PROMPT ENV_IGNORELIST ENV_OBFUSCATE_PART"
# Obfuscate part: part of the key to obfuscate when loading environment variables from user to user, ex: HF_TOKEN, ...
export ENV_OBFUSCATE_PART="TOKEN API KEY"

# Check for ENV_IGNORELIST and ENV_OBFUSCATE_PART
if [ -z "${ENV_IGNORELIST+x}" ]; then error_exit "ENV_IGNORELIST not set"; fi
if [ -z "${ENV_OBFUSCATE_PART+x}" ]; then error_exit "ENV_OBFUSCATE_PART not set"; fi

whoami=`whoami`
script_dir=$(dirname $0)
script_name=$(basename $0)
echo ""; echo ""
echo "======================================"
echo "=================== Starting script (ID: $$)"
echo "== Running ${script_name} in ${script_dir} as ${whoami}"
script_fullname=$0
echo "  - script_fullname: ${script_fullname}"
ignore_value="VALUE_TO_IGNORE"

# everyone can read our files by default
umask 0022

# Write a world-writeable file (preferably inside /tmp -- ie within the container)
write_worldtmpfile() {
  tmpfile=$1
  if [ -z "${tmpfile}" ]; then error_exit "write_worldfile: missing argument"; fi
  if [ -f $tmpfile ]; then rm -f $tmpfile; fi
  echo -n $2 > ${tmpfile}
  chmod 777 ${tmpfile}
}

itdir=/tmp/leap-aide-init
if [ ! -d $itdir ]; then mkdir $itdir; chmod 777 $itdir; fi
if [ ! -d $itdir ]; then error_exit "Failed to create $itdir"; fi

# Set user and group id
# logic: if not set and file exists, use file value, else use default. Create file for persistence when the container is re-run
# reasoning: needed when using docker compose as the file will exist in the stopped container, and changing the value from environment variables or configuration file must be propagated from leap-aide-init to leap-aide transition (those values are the only ones loaded before the environment variables dump file are loaded)
it=$itdir/leap-aide_user_uid
if [ -z "${WANTED_UID+x}" ]; then
  if [ -f $it ]; then WANTED_UID=$(cat $it); fi
fi
# Auto-detect from mounted volumes if still unset (#569, #668).
# On macOS, host UIDs start at 501. Using the wrong UID means the container
# user cannot read the bind-mounted files, making the workspace appear empty.
# In two-container setups (hermes-agent + leap-aide), the shared hermes-home
# volume may be owned by the agent container's UID — detect from there first.
if [ -z "${WANTED_UID+x}" ] || [ "${WANTED_UID}" = "1024" ]; then
  # Priority 1: hermes-home shared volume
  for _probe_dir in "/home/leap-aide/.hermes" "$HERMES_HOME" "/opt/data"; do
    if [ -d "$_probe_dir" ]; then
      _detected_uid=$(stat -c '%u' "$_probe_dir" 2>/dev/null || echo "")
      if [ -n "$_detected_uid" ] && [ "$_detected_uid" != "0" ]; then
        echo "-- Auto-detected UID: $_detected_uid (from $_probe_dir)"
        WANTED_UID=$_detected_uid
        break
      fi
    fi
  done
fi
if [ -z "${WANTED_UID+x}" ] || [ "${WANTED_UID}" = "1024" ]; then
  # Priority 2: /workspaces bind-mount
  if [ -d "/workspaces" ]; then
    _detected_uid=$(stat -c '%u' "/workspaces" 2>/dev/null || echo "")
    if [ -n "$_detected_uid" ] && [ "$_detected_uid" != "0" ]; then
      echo "-- Auto-detected workspace UID: $_detected_uid (from /workspaces)"
      WANTED_UID=$_detected_uid
    fi
  fi
fi
WANTED_UID=${WANTED_UID:-1024}
write_worldtmpfile $it "$WANTED_UID"
echo "-- WANTED_UID: \"${WANTED_UID}\""

it=$itdir/leap-aide_user_gid
if [ -z "${WANTED_GID+x}" ]; then
  if [ -f $it ]; then WANTED_GID=$(cat $it); fi
fi
# Auto-detect GID from mounted volumes to match (#569, #668)
if [ -z "${WANTED_GID+x}" ] || [ "${WANTED_GID}" = "1024" ]; then
  # Priority 1: hermes-home shared volume
  for _probe_dir in "/home/leap-aide/.hermes" "$HERMES_HOME" "/opt/data"; do
    if [ -d "$_probe_dir" ]; then
      _detected_gid=$(stat -c '%g' "$_probe_dir" 2>/dev/null || echo "")
      if [ -n "$_detected_gid" ] && [ "$_detected_gid" != "0" ]; then
        echo "-- Auto-detected GID: $_detected_gid (from $_probe_dir)"
        WANTED_GID=$_detected_gid
        break
      fi
    fi
  done
fi
if [ -z "${WANTED_GID+x}" ] || [ "${WANTED_GID}" = "1024" ]; then
  # Priority 2: /workspaces bind-mount
  if [ -d "/workspaces" ]; then
    _detected_gid=$(stat -c '%g' "/workspaces" 2>/dev/null || echo "")
    if [ -n "$_detected_gid" ] && [ "$_detected_gid" != "0" ]; then
      echo "-- Auto-detected workspace GID: $_detected_gid (from /workspaces)"
      WANTED_GID=$_detected_gid
    fi
  fi
fi
WANTED_GID=${WANTED_GID:-1024}
write_worldtmpfile $it "$WANTED_GID"
echo "-- WANTED_GID: \"${WANTED_GID}\""

echo "== Most Environment variables set"

# Check user id and group id
new_gid=`id -g`
new_uid=`id -u`
echo "== user ($whoami)"
echo "  uid: $new_uid / WANTED_UID: $WANTED_UID"
echo "  gid: $new_gid / WANTED_GID: $WANTED_GID"

save_env() {
  tosave=$1
  echo "-- Saving environment variables to $tosave"
  env | sort > "$tosave"
}

load_env() {
  tocheck=$1
  overwrite_if_different=$2
  ignore_list="${ENV_IGNORELIST}"
  obfuscate_part="${ENV_OBFUSCATE_PART}"
  if [ -f "$tocheck" ]; then
    echo "-- Loading environment variables from $tocheck (overwrite existing: $overwrite_if_different) (ignorelist: $ignore_list) (obfuscate: $obfuscate_part)"
    while IFS='=' read -r key value; do
      doit=false
      # checking if the key is in the ignorelist
      for i in $ignore_list; do
        if [[ "A$key" ==  "A$i" ]]; then doit=ignore; break; fi
      done
      if [[ "A$doit" == "Aignore" ]]; then continue; fi
      rvalue=$value
      # checking if part of the key is in the obfuscate list
      doobs=false
      for i in $obfuscate_part; do
        if [[ "A$key" == *"$i"* ]]; then doobs=obfuscate; break; fi
      done
      if [[ "A$doobs" == "Aobfuscate" ]]; then rvalue="**OBFUSCATED**"; fi

      if [ -z "${!key}" ]; then
        echo "  ++ Setting environment variable $key [$rvalue]"
        doit=true
      elif [ "A$overwrite_if_different" == "Atrue" ]; then
        cvalue="${!key}"
        if [[ "A${doobs}" == "Aobfuscate" ]]; then cvalue="**OBFUSCATED**"; fi
        if [[ "A${!key}" != "A${value}" ]]; then
          echo "  @@ Overwriting environment variable $key [$cvalue] -> [$rvalue]"
          doit=true
        else
          echo "  == Environment variable $key [$rvalue] already set and value is unchanged"
        fi
      fi
      if [[ "A$doit" == "Atrue" ]]; then
        export "$key=$value"
      fi
    done < "$tocheck"
  fi
}

# leap-aide-init is a specific user not existing by default on ubuntu, we can check its whoami
if [ "A${whoami}" == "Aleap-aide-init" ]; then
  echo "-- Running as leap-aide-init, will switch leap-aide to the desired UID/GID"
  # The script is started as leap-aide-init -- UID/GID 1025/1025

  # Root mode (issue #6 follow-up): WANTED_UID=0 with WANTED_GID=0 means "run as
  # root inside the container". That is the expected mapping under rootless
  # podman, where the container's uid 0 IS the invoking host user. The remap is
  # skipped on purpose: `usermod -u 0` fails without -o, and with -o it would
  # add a second uid 0 entry, making getpwuid(0) ambiguous again (issue #6).
  if [ "${WANTED_UID}" = "0" ] || [ "${WANTED_GID}" = "0" ]; then
    if [ "${WANTED_UID}" != "0" ] || [ "${WANTED_GID}" != "0" ]; then
      error_exit "WANTED_UID=0 requires WANTED_GID=0 (root mode is all-or-nothing)"
    fi
    echo "!! WARNING: the service will run as root inside the container."
    echo "!! WARNING: expected for rootless podman (container uid 0 == the host user);"
    echo "!! WARNING: under rootful podman this is a privilege regression - use a real uid."
    save_env /tmp/leap-aide-init_env.txt
    # sudo resets the environment: pass the two values explicitly. Everything
    # else (HERMES_HOME, HERMES_WEBUI_STATE_DIR, ...) is restored by load_env()
    # in the second half, which runs before those variables are consumed.
    # As PID 1 this script must forward stop signals itself: the kernel ignores
    # default-disposition signals for PID 1 and podman/docker stop only signal
    # PID 1, so a bare foreground re-exec would swallow SIGTERM, stall the
    # engine's kill grace period, and have the whole tree SIGKILLed mid-shutdown
    # — wasting the grace window on every restart and lengthening the next
    # boot's dead-port interval (issue #3).
    _leap_child=""
    _got_signal=0
    _forward_signal() {
      _got_signal=1
      echo "-- Forwarding stop signal to root re-exec (PID ${_leap_child})"
      if [ -n "$_leap_child" ]; then kill -TERM "$_leap_child" 2>/dev/null || true; fi
    }
    trap _forward_signal SIGTERM SIGINT
    sudo WANTED_UID="${WANTED_UID}" WANTED_GID="${WANTED_GID}" bash "${script_fullname}" &
    _leap_child=$!
    _rc=0
    wait $_leap_child || _rc=$?
    # `set -e`: a wait() interrupted by the forwarded signal returns 128+signum
    # while the child is still shutting down (captured via `|| _rc=$?` instead
    # of exiting) — reclaim the child's real status with a second wait.
    if [ "$_got_signal" = "1" ] && [ "$_rc" -ge 128 ]; then
      _rc=0
      wait $_leap_child || _rc=$?
    fi
    trap - SIGTERM SIGINT
    if [ "$_rc" -ne 0 ]; then error_exit "root re-exec failed (exit code ${_rc})"; fi
    ok_exit "Clean exit"
  fi

  # Issue #6: `usermod -o` created a DUPLICATE uid whenever WANTED_UID was
  # already taken (ubuntu:24.04 ships 'ubuntu' at 1000, and compose defaults
  # WANTED_UID to the host uid). getpwuid() then resolves to the FIRST
  # /etc/passwd match, so the prompt (`\u`) and `whoami` reported 'ubuntu'.
  # Free the id before remapping; refuse to duplicate one owned by anything
  # other than the distro default account.
  _uid_owner=$(getent passwd "$WANTED_UID" | grep -v '^leap-aide:' | cut -d: -f1 | head -1 || true)
  if [ -n "$_uid_owner" ]; then
    if [ "$_uid_owner" = "ubuntu" ]; then
      echo "-- Releasing UID $WANTED_UID from the distro default 'ubuntu' account"
      sudo userdel ubuntu 2>/dev/null || error_exit "Failed to delete the 'ubuntu' account holding UID $WANTED_UID"
    else
      error_exit "UID $WANTED_UID is already used by '$_uid_owner' -- set WANTED_UID to a free id (issue #6)"
    fi
  fi
  _gid_owner=$(getent group "$WANTED_GID" | grep -v '^leap-aide:' | cut -d: -f1 | head -1 || true)
  if [ -n "$_gid_owner" ]; then
    if [ "$_gid_owner" = "ubuntu" ]; then
      echo "-- Releasing GID $WANTED_GID from the distro default 'ubuntu' group"
      sudo groupdel ubuntu 2>/dev/null || error_exit "Failed to delete the 'ubuntu' group holding GID $WANTED_GID"
    else
      error_exit "GID $WANTED_GID is already used by '$_gid_owner' -- set WANTED_GID to a free id (issue #6)"
    fi
  fi

  # We are altering the UID/GID of the leap-aide user to the desired ones and restarting as that user
  # using usermod for the already created leap-aide user, knowing it is not already in use
  # per usermod manual: "You must make certain that the named user is not executing any processes when this command is being executed"
  sudo groupmod -g ${WANTED_GID} leap-aide || error_exit "Failed to set GID of leap-aide user"
  sudo usermod -u ${WANTED_UID} leap-aide || error_exit "Failed to set UID of leap-aide user"
  sudo chown -R ${WANTED_UID}:${WANTED_GID} /home/leap-aide || error_exit "Failed to set owner of /home/leap-aide"
  save_env /tmp/leap-aide-init_env.txt
  # restart the script as leap-aide set with the correct UID/GID this time
  echo "-- Restarting as leap-aide user with UID ${WANTED_UID} GID ${WANTED_GID}"
  # Run the leap-aide phase as a child and forward stop signals to it. As
  # PID 1 this script is immune to default-disposition signals (the kernel
  # ignores them for PID 1), while podman/docker stop only signal PID 1 —
  # without the forward, stop stalls the engine's kill grace period (10s)
  # and the whole tree is SIGKILLed mid-shutdown, wasting 10s on every
  # restart before the next boot starts (issue #3: lengthens the window in
  # which the port is dead and first page loads fail).
  _leap_child=""
  _got_signal=0
  _forward_signal() {
    _got_signal=1
    echo "-- Forwarding stop signal to leap-aide phase (PID ${_leap_child})"
    if [ -n "$_leap_child" ]; then kill -TERM "$_leap_child" 2>/dev/null || true; fi
  }
  trap _forward_signal SIGTERM SIGINT
  sudo su leap-aide $script_fullname &
  _leap_child=$!
  _rc=0
  wait $_leap_child || _rc=$?
  # `set -e`: a wait() interrupted by the forwarded signal returns 128+signum
  # while the child is still shutting down (captured via `|| _rc=$?` instead
  # of exiting) — reclaim the child's real status with a second wait.
  if [ "$_got_signal" = "1" ] && [ "$_rc" -ge 128 ]; then
    _rc=0
    wait $_leap_child || _rc=$?
  fi
  trap - SIGTERM SIGINT
  if [ "$_rc" -ne 0 ]; then error_exit "subscript failed (exit code ${_rc})"; fi
  ok_exit "Clean exit"
fi

# If we are here, the script is started as another user than leap-aide-init
# because the whoami value for the leap-aide user can be any existing user, we can not check against it
# instead we check if the UID/GID are the expected ones
if [ "$WANTED_GID" != "$new_gid" ]; then error_exit "leap-aide MUST be running as UID ${WANTED_UID} GID ${WANTED_GID}, current UID ${new_uid} GID ${new_gid}"; fi
if [ "$WANTED_UID" != "$new_uid" ]; then error_exit "leap-aide MUST be running as UID ${WANTED_UID} GID ${WANTED_GID}, current UID ${new_uid} GID ${new_gid}"; fi

########## 'leap-aide' specific section below

# We are therefore running as leap-aide
echo ""; echo "== Running as leap-aide"
# Make identity mismatches visible in `docker logs` at a glance (issue #6).
echo "-- Running as $(id -un) (uid $(id -u), gid $(id -g))"

# Load environment variables one by one if they do not exist from /tmp/leap-aide-init_env.txt
it=/tmp/leap-aide-init_env.txt
if [ -f $it ]; then
  echo "-- Loading not already set environment variables from $it"
  load_env $it true
fi

##
echo ""; echo "-- Aligning /app ownership with the leap-aide user"
# /app is baked into the image and is owned by leap-aide-init. The UID is
# adapted at runtime (see WANTED_UID above) and `usermod` does not touch files
# that already exist, so re-own the tree to match — otherwise the server runs
# as a user that cannot write its own checkout.
# This replaces the old `rsync /build/ /app/`, which re-copied the whole tree
# on every start just to apply --chown.
# Recursive chown over the whole tree is one of the slowest boot steps
# (issue #3: it delays time-to-listen on first boot), so skip it when a
# stamp in the container's /tmp shows the tree was already re-owned for
# this UID:GID. /tmp resets on container recreate or image change — exactly
# when the image-layer owners matter again.
_app_stamp="$itdir/app-uid-stamp"
if [ -f "$_app_stamp" ] && [ "$(cat "$_app_stamp")" = "${WANTED_UID}:${WANTED_GID}" ]; then
  echo "-- /app already owned by ${WANTED_UID}:${WANTED_GID} (stamp match) — skipping chown"
else
  sudo chown -R ${WANTED_UID}:${WANTED_GID} /app || error_exit "Failed to set owner of /app to ${WANTED_UID}:${WANTED_GID}"
  echo -n "${WANTED_UID}:${WANTED_GID}" > "$_app_stamp" || echo "!! WARNING: could not write $_app_stamp (chown re-runs next boot)"
fi
it=/app/.testfile; touch $it || error_exit "Failed to verify /app directory"
rm -f $it || error_exit "Failed to delete test file in /app"

######## Environment variables (consume AFTER the load_env)

echo ""; echo "== Checking required environment variables for leap-aide"

echo ""; echo "-- HERMES_WEBUI_STATE_DIR: Where to store sessions, workspaces, and other state (default: ~/.hermes/webui)"
# Not required from compose/env: default to ~/.hermes/webui — the same default
# bootstrap.py, ctl.sh and api/config.py use. The docker-compose volume mounts
# persistent state at /home/leap-aide/.hermes, so the default lands on the
# mounted volume without an explicit variable. Set HERMES_WEBUI_STATE_DIR to
# override.
if [ -z "${HERMES_WEBUI_STATE_DIR:-}" ]; then
  export HERMES_WEBUI_STATE_DIR="$HOME/.hermes/webui"
  echo "-- HERMES_WEBUI_STATE_DIR not set — defaulting to $HERMES_WEBUI_STATE_DIR"
fi
echo "-- HERMES_WEBUI_STATE_DIR: $HERMES_WEBUI_STATE_DIR"
if [ ! -d "$HERMES_WEBUI_STATE_DIR" ]; then mkdir -p $HERMES_WEBUI_STATE_DIR || error_exit "Failed to create state directory at $HERMES_WEBUI_STATE_DIR"; fi
if [ ! -d "$HERMES_WEBUI_STATE_DIR" ]; then error_exit "HERMES_WEBUI_STATE_DIR directory does not exist at $HERMES_WEBUI_STATE_DIR"; fi
it="$HERMES_WEBUI_STATE_DIR/.testfile"; touch $it || error_exit "Failed to verify state directory at $HERMES_WEBUI_STATE_DIR"
rm -f $it || error_exit "Failed to delete test file in $HERMES_WEBUI_STATE_DIR"

echo ""; echo "-- HERMES_HOME: hermes-agent state dir (default: ~/.hermes)"
# Data dir for hermes-agent (sessions, logs, memories, config.yaml, .env,
# SOUL.md, skills, state.db). NOT the code checkout — that lives at
# /usr/local/lib/hermes-agent (HERMES_WEBUI_AGENT_DIR). docker-compose mounts
# the persistent state at /home/leap-aide/.hermes, so the default lands on the
# mounted volume. Set HERMES_HOME to override.
if [ -z "${HERMES_HOME:-}" ]; then
  export HERMES_HOME="$HOME/.hermes"
  echo "-- HERMES_HOME not set — defaulting to $HERMES_HOME"
fi
echo "-- HERMES_HOME: $HERMES_HOME"

# First-run seed: copy the image's hermes-agent state seed into HERMES_HOME.
# The compose bind-mount shadows anything baked at /home/leap-aide/.hermes, so
# the installer-produced seed (config.yaml with display.show_reasoning=true,
# .env, SOUL.md, skills/, managed bin/) lives at HERMES_SEED_DIR and is copied
# in once. NEVER overwrite existing entries — the host ~/.hermes may be shared
# with a host hermes install.
_seed="${HERMES_SEED_DIR:-/var/lib/leap-aide/hermes-seed}"
sudo mkdir -p "$HERMES_HOME" || error_exit "Failed to create HERMES_HOME at $HERMES_HOME"
if [ -d "$_seed" ]; then
  echo "-- Seeding HERMES_HOME from $_seed (existing entries are never overwritten)"
  # -a preserves modes/symlinks (browser-use launchers in bin/ are symlinks
  # into the image's uv tool store).
  for _e in "$_seed"/* "$_seed"/.[!.]*; do
    [ -e "$_e" ] || continue
    _n=$(basename "$_e")
    [ -e "$HERMES_HOME/$_n" ] && continue
    sudo cp -a "$_e" "$HERMES_HOME/$_n" || echo "!! WARNING: could not seed $_n (continuing)"
  done
else
  echo "-- No seed dir at $_seed — skipping seed copy"
fi
# State scaffolding the SDK expects (idempotent; covers seeds that predate a dir).
for _d in sessions logs memories image_cache audio_cache cron hooks pairing skills; do
  sudo mkdir -p "$HERMES_HOME/$_d"
done
# LeapAide-managed agent skills (issue #9): merge image /app/skills into
# $HERMES_HOME/skills. Idempotent; respects user-owned (unmanaged) dirs.
# sudo resets the environment, so HERMES_HOME must be passed explicitly.
# Runs before the chown below so ownership converges with the rest of the tree.
if [ -d /app/skills ]; then
  echo "-- Installing LeapAide-managed skills from /app/skills"
  sudo env HERMES_HOME="$HERMES_HOME" bash /app/backend/scripts/install-skills.sh \
    --source /app/skills || echo "!! WARNING: install-skills.sh failed (continuing)"
fi
# Credential hygiene on the runtime copy (the seed .env is world-readable so
# the cicd stage can import env_loader without root; the runtime copy must not be).
[ -f "$HERMES_HOME/.env" ] && sudo chmod 600 "$HERMES_HOME/.env"
# Ownership policy: always align the state tree to WANTED_UID:WANTED_GID.
# WANTED_UID/GID are auto-detected FROM the volume owner (lines 82-125) or set
# explicitly (compose defaults to the host UID), so this converges the tree to
# the UID the container runs as and fixes the root-owned .env PermissionError
# class. A no-op when ownership already matches. Note: a deliberately
# foreign-owned volume WILL be reassigned.
sudo chown -R "${WANTED_UID}:${WANTED_GID}" "$HERMES_HOME" \
  || error_exit "Failed to set owner of $HERMES_HOME to ${WANTED_UID}:${WANTED_GID}"
it="$HERMES_HOME/.testfile"; touch $it || error_exit "Failed to verify HERMES_HOME at $HERMES_HOME"
rm -f $it || error_exit "Failed to delete test file in $HERMES_HOME"

echo ""; echo "-- HERMES_WEBUI_DEFAULT_WORKSPACE: Default workspace directory shown on first launch"
if [ -z "${HERMES_WEBUI_DEFAULT_WORKSPACE+x}" ]; then echo "HERMES_WEBUI_DEFAULT_WORKSPACE not set, setting to /workspaces"; export HERMES_WEBUI_DEFAULT_WORKSPACE="/workspaces"; fi;
echo "-- HERMES_WEBUI_DEFAULT_WORKSPACE: $HERMES_WEBUI_DEFAULT_WORKSPACE"
# Use sudo for mkdir — Docker may auto-create bind-mount directories as root (#357).
# Skip mkdir if the directory already exists (e.g. a read-only mount — #670).
if [ ! -d "$HERMES_WEBUI_DEFAULT_WORKSPACE" ]; then
  sudo mkdir -p "$HERMES_WEBUI_DEFAULT_WORKSPACE" || error_exit "Failed to create default workspace at $HERMES_WEBUI_DEFAULT_WORKSPACE"
fi
if [ ! -d "$HERMES_WEBUI_DEFAULT_WORKSPACE" ]; then error_exit "HERMES_WEBUI_DEFAULT_WORKSPACE directory does not exist at $HERMES_WEBUI_DEFAULT_WORKSPACE"; fi
# Only chown and write-test if the workspace is writable. Read-only bind-mounts
# (:ro) are valid — the workspace is used for browsing, not writing by the server.
if [ -w "$HERMES_WEBUI_DEFAULT_WORKSPACE" ]; then
  sudo chown leap-aide:leap-aide "$HERMES_WEBUI_DEFAULT_WORKSPACE" || echo "!! WARNING: Could not chown $HERMES_WEBUI_DEFAULT_WORKSPACE (continuing)"
  it="$HERMES_WEBUI_DEFAULT_WORKSPACE/.testfile"; touch $it && rm -f $it || echo "!! WARNING: Could not write to $HERMES_WEBUI_DEFAULT_WORKSPACE (continuing)"
else
  echo "-- HERMES_WEBUI_DEFAULT_WORKSPACE is read-only — skipping chown/write check (read-only workspace is supported)"
fi

echo ""; echo "==================="
echo ""; echo "== Locating hermes-agent environment"

# The hermes-agent installer owns the Python dependency environment and builds
# it at image-build time ($AGENT_DIR/venv: created and synced by the installer;
# fails the build if it is missing). There is intentionally NO runtime install
# fallback here: a missing venv is an image defect, so fail fast instead of
# silently building a second environment at container start. The WebUI imports
# the SDK modules themselves dynamically (sys.path via api/config.py) — the
# venv only provides the third-party deps the server interpreter needs.
_agent_paths=(
  "/usr/local/lib/hermes-agent"
)
_agent_src=""
for _p in "${_agent_paths[@]}"; do
  if [ -d "$_p" ] && [ -f "$_p/pyproject.toml" ]; then
    _agent_src="$_p"
    break
  fi
done
if [ -z "$_agent_src" ]; then
  error_exit "hermes-agent source not found (looked in: ${_agent_paths[*]}) — image was built without it"
fi

# Optional commit-pin consistency check (warn-only). Catches "I bumped
# hermes-agent on host but forgot to rebuild the image". HERMES_AGENT_COMMIT
# is baked into the image from .build.env at the repo root. Skipped entirely
# when no pin was set (an unpinned build tracks the SDK's default branch).
if [ -n "${HERMES_AGENT_COMMIT:-}" ] && [ -d "$_agent_src/.git" ]; then
  _actual=$(git -C "$_agent_src" rev-parse HEAD 2>/dev/null || echo "")
  # Prefix match via `case`: the pin may be a short SHA. Comparing the full
  # 40-char HEAD against a truncated 12-char pin could never match, so this
  # check used to warn on every correctly pinned image.
  if [ -n "$_actual" ]; then
    case "$_actual" in
      "${HERMES_AGENT_COMMIT}"*) ;;
      *) echo "!! WARNING: hermes-agent commit mismatch: expected ${HERMES_AGENT_COMMIT}, got ${_actual} — continuing" ;;
    esac
  fi
fi

_agent_python="$_agent_src/venv/bin/python"
if [ ! -x "$_agent_python" ]; then
  error_exit "hermes-agent venv missing at $_agent_python — the installer's dependency stage did not complete (check the image build log); no runtime install fallback by design"
fi
echo "-- hermes-agent venv: $_agent_python"

echo ""; echo "== Running leap-aide (via scripts/ctl.sh)"

# Pin the interpreter to the agent venv (dev parity: bootstrap.py's
# discover_launcher_python() prefers agent_dir/venv/bin/python). WebUI deps
# (pyyaml, hindsight-client) were installed into the same venv at build time
# (scripts/install.sh), so bootstrap's `import yaml` check passes and it never
# falls back to creating a REPO_ROOT/.venv.
export HERMES_WEBUI_PYTHON="$_agent_python"

# ctl.sh owns .env loading and resolves the server log path (it mirrors
# bootstrap.py's $HERMES_WEBUI_STATE_DIR/bootstrap-<port>.log naming), so ask
# it instead of loading /app/.env a second time here.
_ctl=/app/scripts/ctl.sh
_log=$(bash "$_ctl" log-path)

bash "$_ctl" start || error_exit "ctl.sh start failed"

# ctl.sh is not a foreground supervisor: bootstrap.py Popen()s the server with
# start_new_session=True and returns once it is healthy. Keep PID 1 alive and
# surface the server log on stdout so `docker logs` works. `tail -F` (not -f)
# survives ctl.sh's log rotation into old_logs/.
_graceful_shutdown() {
  echo "[docker] Received SIGTERM, stopping via ctl.sh..."
  bash "$_ctl" stop || true
  exit 0
}
trap _graceful_shutdown SIGTERM SIGINT

echo "-- Following server log: $_log"
tail -n +1 -F "$_log" &
wait

ok_exit "Clean exit"
