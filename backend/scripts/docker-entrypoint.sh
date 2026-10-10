#!/bin/bash

# docker-entrypoint.sh — runtime entrypoint for the leap-aide container
#
# Responsibilities:
#   1. Validate ENV_IGNORELIST / ENV_OBFUSCATE_PART
#   2. Detect WANTED_UID/WANTED_GID from mounted volumes
#   3. Swap from leap-aide-init (UID 1025) to leap-aide (UID dynamically adjusted)
#   4. Align /app ownership with the runtime UID (the tree is baked into the image)
#   5. Provision state dir, workspace dir
#   6. Install hermes-agent into a venv (from a host mount if provided, else
#      the copy baked into the image at /usr/local/lib/hermes-agent)
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
  # Priority 2: /workspace bind-mount
  if [ -d "/workspace" ]; then
    _detected_uid=$(stat -c '%u' "/workspace" 2>/dev/null || echo "")
    if [ -n "$_detected_uid" ] && [ "$_detected_uid" != "0" ]; then
      echo "-- Auto-detected workspace UID: $_detected_uid (from /workspace)"
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
  # Priority 2: /workspace bind-mount
  if [ -d "/workspace" ]; then
    _detected_gid=$(stat -c '%g' "/workspace" 2>/dev/null || echo "")
    if [ -n "$_detected_gid" ] && [ "$_detected_gid" != "0" ]; then
      echo "-- Auto-detected workspace GID: $_detected_gid (from /workspace)"
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

  # We are altering the UID/GID of the leap-aide user to the desired ones and restarting as that user
  # using usermod for the already created leap-aide user, knowing it is not already in use
  # per usermod manual: "You must make certain that the named user is not executing any processes when this command is being executed"
  sudo groupmod -o -g ${WANTED_GID} leap-aide || error_exit "Failed to set GID of leap-aide user"
  sudo usermod -o -u ${WANTED_UID} leap-aide || error_exit "Failed to set UID of leap-aide user"
  sudo chown -R ${WANTED_UID}:${WANTED_GID} /home/leap-aide || error_exit "Failed to set owner of /home/leap-aide"
  save_env /tmp/leap-aide-init_env.txt
  # restart the script as leap-aide set with the correct UID/GID this time
  echo "-- Restarting as leap-aide user with UID ${WANTED_UID} GID ${WANTED_GID}"
  sudo su leap-aide $script_fullname || error_exit "subscript failed"
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
sudo chown -R ${WANTED_UID}:${WANTED_GID} /app || error_exit "Failed to set owner of /app to ${WANTED_UID}:${WANTED_GID}"
it=/app/.testfile; touch $it || error_exit "Failed to verify /app directory"
rm -f $it || error_exit "Failed to delete test file in /app"

######## Environment variables (consume AFTER the load_env)

echo ""; echo "== Checking required environment variables for leap-aide"

echo ""; echo "-- HERMES_WEBUI_VERSION: Where to store sessions, workspaces, and other state (default: ~/.hermes/webui-mvp)"
if [ -z "${HERMES_WEBUI_STATE_DIR+x}" ]; then error_exit "HERMES_WEBUI_STATE_DIR not set"; fi;
echo "-- HERMES_WEBUI_STATE_DIR: $HERMES_WEBUI_STATE_DIR"
if [ ! -d "$HERMES_WEBUI_STATE_DIR" ]; then mkdir -p $HERMES_WEBUI_STATE_DIR || error_exit "Failed to create state directory at $HERMES_WEBUI_STATE_DIR"; fi
if [ ! -d "$HERMES_WEBUI_STATE_DIR" ]; then error_exit "HERMES_WEBUI_STATE_DIR directory does not exist at $HERMES_WEBUI_STATE_DIR"; fi
it="$HERMES_WEBUI_STATE_DIR/.testfile"; touch $it || error_exit "Failed to verify state directory at $HERMES_WEBUI_STATE_DIR"
rm -f $it || error_exit "Failed to delete test file in $HERMES_WEBUI_STATE_DIR"

echo ""; echo "-- HERMES_WEBUI_DEFAULT_WORKSPACE: Default workspace directory shown on first launch"
if [ -z "${HERMES_WEBUI_DEFAULT_WORKSPACE+x}" ]; then echo "HERMES_WEBUI_DEFAULT_WORKSPACE not set, setting to /workspace"; export HERMES_WEBUI_DEFAULT_WORKSPACE="/workspace"; fi;
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
echo ""; echo "== Creating a new virtual environment for leap-aide"

# uv is baked into the image at /usr/local/bin (installed as root with
# UV_INSTALL_DIR=/usr/local/bin; TestDockerfileUvPreinstall asserts this).
# There used to be a `curl | sh` fallback here for images that lacked it. It
# was unreachable for this image, and a production entrypoint that fetches and
# executes remote code at start is a supply-chain surface — a missing uv is an
# image defect, so fail fast instead of quietly repairing it over the network.
command -v uv >/dev/null 2>&1 \
  || error_exit "uv not found on PATH — image was built without it (expected /usr/local/bin/uv)"

cd /app
if [ -f /app/venv/bin/python3 ]; then
  echo ""; echo "== Existing virtual environment found — reusing (fast restart)"
else
  echo ""; echo "== Creating new virtual environment"
  uv venv venv
fi
export VIRTUAL_ENV=/app/venv
test -d /app/venv
test -f /app/venv/bin/activate

echo "";echo "== Activating leap-aide's virtual environment"
source /app/venv/bin/activate || error_exit "Failed to activate leap-aide virtual environment"
test -x /app/venv/bin/python3

echo ""; echo "== Installing hermes-agent into leap-aide's venv"
# Backend deps (pyyaml, hindsight-client) are baked into the image at build time.
# Only hermes-agent remains a runtime install — sourced from a host mount when
# one is provided, otherwise from the copy already in the image.

if [ -f /app/venv/.agent_installed ]; then
  echo ""; echo "== hermes-agent already installed — skipping (fast restart)"
else
  echo ""; echo "== Locating hermes-agent source"
  # Ordered user-override first, image default last: a host-provided agent
  # (mounted at /home/leap-aide/.hermes/hermes-agent) wins when present, but
  # the copy baked into the image at /usr/local/lib/hermes-agent is enough on
  # its own — no HERMES_AGENT_DIR bind mount is required.
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
  if [ -n "$_agent_src" ]; then
    # Optional commit-pin consistency check (warn-only). Catches "I bumped
    # hermes-agent on host but forgot to rebuild the image". HERMES_AGENT_COMMIT
    # is baked into the image from backend/scripts/hermes-agent.env.
    if [ -n "${HERMES_AGENT_COMMIT:-}" ] && [ -d "$_agent_src/.git" ]; then
      _actual=$(git -C "$_agent_src" rev-parse HEAD 2>/dev/null || echo "")
      _expected_short="${HERMES_AGENT_COMMIT:0:12}"
      if [ -n "$_actual" ] && [ "$_actual" != "$_expected_short" ]; then
        echo "!! WARNING: hermes-agent commit mismatch: expected ${_expected_short}, got ${_actual} — continuing"
      fi
    fi
    uv pip install "$_agent_src[all]" --trusted-host pypi.org --trusted-host files.pythonhosted.org || error_exit "Failed to install hermes-agent's requirements"
    touch /app/venv/.agent_installed
  else
    echo ""
    echo "!! WARNING: hermes-agent source not found."
    for _p in "${_agent_paths[@]}"; do
      echo "!!   looked in: $_p"
    done
    echo "!! The WebUI will start with reduced functionality (no model auto-detection,"
    echo "!! no personality routing, no CLI session imports)."
    echo "!! The image normally ships it at /usr/local/lib/hermes-agent — if that is"
    echo "!! missing, the image was built without it. To override with a host copy,"
    echo "!! mount it at /home/leap-aide/.hermes/hermes-agent (optional)."
    echo ""
  fi
fi

echo ""; echo "== Running leap-aide (via scripts/ctl.sh)"

# ctl.sh sources /app/.env itself, but we need the values *here* to compute the
# log path below, so load it first. Loading twice is harmless.
if [ -f /app/.env ]; then
  echo "-- Loading /app/.env"
  set -a; . /app/.env; set +a
fi

# Pin the interpreter to the venv this script prepared. bootstrap.py's
# discover_launcher_python() would otherwise prefer agent_dir/venv/bin/python,
# or fall through to creating its own REPO_ROOT/.venv and pip-installing —
# neither of which is where we installed hermes-agent.
export HERMES_WEBUI_PYTHON="${VIRTUAL_ENV}/bin/python3"

# ctl.sh names its PID/log files from $WEBUI_PORT (note: NOT HERMES_WEBUI_PORT,
# and it is read before ctl.sh sources .env), while bootstrap.py writes its log
# to $HERMES_WEBUI_STATE_DIR/bootstrap-<port>.log. Mirror both so the path below
# is the one that actually gets written.
export WEBUI_PORT="${HERMES_WEBUI_PORT:-8787}"
_ctl=/app/scripts/ctl.sh
_log="${HERMES_WEBUI_STATE_DIR:-$HOME/.hermes/webui}/bootstrap-${WEBUI_PORT}.log"

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
