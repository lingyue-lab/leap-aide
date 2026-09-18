#!/usr/bin/env bash
# -*- coding: utf-8 -*-
#
# leap-aide 远程消息发送脚本
# 自动管理 cookie 登录态，支持流式/同步两种模式发送消息到 session。
#
# 用法:
#   # 环境变量（可选，也可写在 ~/.hermes_sendrc 中）
#   export HERMES_URL="http://192.168.1.100:8787"
#   export HERMES_PASSWORD="your_password"
#
#   # 新建 session 并发送消息（流式）
#   ./cmd_send.sh --new "你好，帮我写一段代码"
#
#   # 向已有 session 发消息（同步模式）
#   ./cmd_send.sh --session <session_id> "你好" --sync
#
#   # 向已有 session 发消息（流式，默认）
#   ./cmd_send.sh --session <session_id> "你好"
#
#   # 指定模型
#   ./cmd_send.sh --session <session_id> --model openai/gpt-4o "你好"
#
#   # 列出所有 session
#   ./cmd_send.sh --list
#
#   # 新建空白 session
#   ./cmd_send.sh --create-session
#
#   # 仅验证登录态
#   ./cmd_send.sh --check-auth

set -euo pipefail

# ── 配置（环境变量 > 配置文件 > 默认值）───────────────────────────────────────
HERMES_URL="${HERMES_URL:-http://127.0.0.1:8787}"
HERMES_PASSWORD="${HERMES_PASSWORD:-}"
COOKIE_FILE="${COOKIE_FILE:-${HOME}/.hermes_session_cookie}"
CONFIG_FILE="${CONFIG_FILE:-${HOME}/.hermes_sendrc}"

# 尝试加载配置文件（shell 格式，可覆盖环境变量）
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
fi

# ── 颜色输出 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_debug() { echo -e "${CYAN}[DEBUG]${NC} $*"; }

# ── 帮助 ─────────────────────────────────────────────────────────────────────
usage() {
    cat <<'EOF'
用法: cmd_send.sh [选项] <消息>

选项:
  --session, -s <id>    指定 session_id（省略则需配合 --new 自动创建）
  --new, -n             自动创建新 session 后发送消息
  --model, -m <model>   指定模型（可选，默认使用 session 已有配置）
  --mode <mode>         忙时模式：interrupt|queue|steer|reject（默认：interrupt）
  --sync, -S            使用同步模式（阻塞等待完整回复，默认是流式）
  --list, -l            列出所有 session
  --create-session      仅创建一个空白 session 并输出 session_id
  --check-auth          仅验证当前登录态是否有效
  --url <url>           指定服务地址（覆盖 HERMES_URL 环境变量）
  --help, -h            显示此帮助

环境变量:
  HERMES_URL            服务地址，默认 http://127.0.0.1:8787
  HERMES_PASSWORD       登录密码（若服务开启了认证则必填）
  COOKIE_FILE           cookie 存储路径，默认 ~/.hermes_session_cookie

配置文件 (~/.hermes_sendrc):
  支持 shell 格式，示例:
    HERMES_URL="http://192.168.1.100:8787"
    HERMES_PASSWORD="my_password"

示例:
  ./cmd_send.sh --new "你好"
  ./cmd_send.sh -s abc123 "继续上面的对话"
  ./cmd_send.sh -s abc123 -m openai/gpt-4o "写代码"
  ./cmd_send.sh --list
EOF
    exit 0
}

# ── 检查依赖 ─────────────────────────────────────────────────────────────────
check_deps() {
    for dep in curl jq; do
        if ! command -v "$dep" &>/dev/null; then
            log_error "缺少依赖: $dep，请先安装。"
            exit 1
        fi
    done
}

# ── curl 包装：自动附带认证 cookie ────────────────────────────────────────────
# 所有业务 API 调用统一走这个函数，调用方无需关心 cookie 拼接。
# 注意：auth_is_enabled / cookie_is_valid / do_login 内部直接调用 curl，
# 不走 _curl，避免递归。

# 共享的 curl 基础参数（-k 用于自签名证书）
_CURL_BASE=("-sS")
[[ "${HERMES_INSECURE:-0}" == "1" ]] && _CURL_BASE+=("-k")

_curl() {
    local args=("${_CURL_BASE[@]}")
    auth_is_enabled && args+=("-b" "$COOKIE_FILE")
    curl "${args[@]}" "$@"
}

# ── Cookie 管理 ──────────────────────────────────────────────────────────────

# 检查 cookie 是否有效
# 返回 0 = 有效，1 = 无效或过期
cookie_is_valid() {
    if [[ ! -f "$COOKIE_FILE" ]]; then
        return 1
    fi
    local resp http_code
    resp=$(curl "${_CURL_BASE[@]}" -w "\n%{http_code}" \
        -b "$COOKIE_FILE" \
        "$HERMES_URL/api/auth/status" 2>/dev/null) || return 1
    http_code=$(echo "$resp" | tail -1)
    local body
    body=$(echo "$resp" | sed '$d')

    # 200 且 logged_in == true
    if [[ "$http_code" == "200" ]]; then
        local logged_in
        logged_in=$(echo "$body" | jq -r '.logged_in // false')
        if [[ "$logged_in" == "true" ]]; then
            return 0
        fi
    fi
    return 1
}

# 检查是否需要认证
auth_is_enabled() {
    local resp
    resp=$(curl "${_CURL_BASE[@]}" "$HERMES_URL/api/auth/status" 2>/dev/null) || return 1
    local enabled
    enabled=$(echo "$resp" | jq -r '.auth_enabled // false')
    [[ "$enabled" == "true" ]]
}

# 登录获取 cookie
do_login() {
    if [[ -z "$HERMES_PASSWORD" ]]; then
        log_error "认证已启用但未设置 HERMES_PASSWORD。"
        log_error "请设置环境变量: export HERMES_PASSWORD='your_password'"
        log_error "或写入配置文件: echo 'HERMES_PASSWORD=\"your_password\"' >> $CONFIG_FILE"
        exit 1
    fi
    log_info "正在登录 $HERMES_URL ..."
    local resp http_code
    resp=$(curl "${_CURL_BASE[@]}" -w "\n%{http_code}" \
        -X POST "$HERMES_URL/api/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"password\":\"$HERMES_PASSWORD\"}" \
        -c "$COOKIE_FILE" 2>/dev/null) || {
        log_error "登录请求失败，请检查服务是否可达: $HERMES_URL"
        exit 1
    }
    http_code=$(echo "$resp" | tail -1)
    local body
    body=$(echo "$resp" | sed '$d')

    if [[ "$http_code" == "200" ]]; then
        local ok
        ok=$(echo "$body" | jq -r '.ok // false')
        if [[ "$ok" == "true" ]]; then
            log_info "登录成功，cookie 已保存至 ${COOKIE_FILE} (有效期 24 小时)"
            return 0
        fi
    fi
    log_error "登录失败 (HTTP $http_code): $body"
    rm -f "$COOKIE_FILE"
    exit 1
}

# 确保 cookie 有效（无效则重新登录）
ensure_cookie() {
    if auth_is_enabled; then
        if cookie_is_valid; then
            log_debug "Cookie 有效，跳过登录。"
        else
            log_warn "Cookie 不存在或已过期，重新登录..."
            do_login
        fi
    else
        log_debug "认证未启用，跳过登录。"
    fi
}

# ── Session 操作 ──────────────────────────────────────────────────────────────

# 创建新 session，返回 session_id
create_session() {
    ensure_cookie
    local resp http_code
    resp=$(_curl -w "\n%{http_code}" \
        -X POST "$HERMES_URL/api/session/new" \
        -H "Content-Type: application/json" \
        -d '{}' 2>/dev/null) || {
        log_error "创建 session 失败，请检查服务是否可达。"
        exit 1
    }
    http_code=$(echo "$resp" | tail -1)
    local body
    body=$(echo "$resp" | sed '$d')

    if [[ "$http_code" == "200" ]]; then
        local sid
        sid=$(echo "$body" | jq -r '.session.session_id')
        echo "$sid"
    else
        log_error "创建 session 失败 (HTTP $http_code): $body"
        exit 1
    fi
}

# 列出所有 session
list_sessions() {
    ensure_cookie
    local resp
    resp=$(_curl "$HERMES_URL/api/sessions")
    local rc=$?
    if [[ $rc -ne 0 ]] || [[ -z "$resp" ]] || [[ "$resp" == "null" ]]; then
        log_error "获取 session 列表失败 (exit=$rc, resp='${resp:0:200}')"
        exit 1
    fi
    echo "$resp" | jq -r '
      .sessions[:20][] |
      "\(.session_id)  \(.title // "(untitled)")  [\(.model // "?" | tostring)]  \(.last_activity_at // .updated_at // 0 | todate)"
    '
}

# ── 发送消息 ─────────────────────────────────────────────────────────────────

# 流式发送消息（POST /api/chat/start + SSE stream）
send_streaming() {
    local session_id="$1"
    local message="$2"
    local model="${3:-}"
    local mode="${4:-}"

    ensure_cookie

    # 构造请求 body
    local req_body
    req_body=$(jq -n \
        --arg sid "$session_id" \
        --arg msg "$message" \
        --arg model "$model" \
        --arg mode "$mode" \
        '{session_id: $sid, message: $msg} +
         (if $model != "" then {model: $model} else {} end) +
         (if $mode != "" then {mode: $mode} else {} end)')

    log_info "发送消息到 session $session_id..."
    if [[ -n "$model" ]]; then
        log_debug "指定模型: $model"
    fi
    if [[ -n "$mode" ]]; then
        log_debug "忙时模式: $mode"
    fi

    # 第一步：POST /api/chat/start
    local resp
    resp=$(_curl -X POST "$HERMES_URL/api/chat/start" \
        -H "Content-Type: application/json" \
        -d "$req_body") || {
        log_error "发送消息失败。"
        exit 1
    }

    # 检查错误
    local error
    error=$(echo "$resp" | jq -r '.error // empty')
    local stream_id=""
    if [[ -n "$error" ]]; then
        local active_sid
        active_sid=$(echo "$resp" | jq -r '.active_stream_id // empty')
        if [[ -n "$active_sid" ]]; then
            # 后端返回了现有 stream_id。如果用户明确指定了 reject
            # 模式，说明不想打扰当前对话，直接退出。
            if [[ "$mode" == "reject" ]]; then
                log_error "Session 正忙，已按 reject 模式拒绝发送。"
                log_error "  活跃 stream: $active_sid"
                log_error "  提示: 去掉 --mode reject 或换成 --mode queue/steer"
                exit 1
            fi
            log_warn "Session 已有活跃流: $active_sid，连接到该流..."
            stream_id="$active_sid"
        else
            log_error "发送失败: $error"
            exit 1
        fi
    else
        stream_id=$(echo "$resp" | jq -r '.stream_id')
        log_info "流已创建: $stream_id"
    fi
    if [[ -z "$stream_id" ]]; then
        log_error "未能获取 stream_id，无法继续。"
        exit 1
    fi

    # 第二步：SSE 接收
    log_info "开始接收回复（按 Ctrl+C 中断）..."
    echo "────────────────────────────────────────────────────────────"
    _curl -N -s "$HERMES_URL/api/chat/stream?stream_id=$stream_id" | while IFS= read -r line; do
        if [[ "$line" == "event: token" ]]; then
            # 下一行是 data: {"text": "..."}
            read -r data_line
            local text
            text=$(echo "$data_line" | sed 's/^data: //' | jq -r '.text // empty' 2>/dev/null)
            if [[ -n "$text" ]]; then
                printf "%s" "$text"
            fi
        elif [[ "$line" == "event: reasoning" ]]; then
            read -r data_line
            local reasoning
            reasoning=$(echo "$data_line" | sed 's/^data: //' | jq -r '.text // empty' 2>/dev/null)
            if [[ -n "$reasoning" ]]; then
                echo -e "\n${CYAN}[思考]${NC} $reasoning"
            fi
        elif [[ "$line" == "event: tool" || "$line" =~ ^event:.*tool ]]; then
            read -r data_line
            local tool_name tool_args
            tool_name=$(echo "$data_line" | sed 's/^data: //' | jq -r '.name // "?"' 2>/dev/null)
            tool_args=$(echo "$data_line" | sed 's/^data: //' | jq -r '.preview // .args?.description // ""' 2>/dev/null)
            echo -e "\n${YELLOW}[工具调用]${NC} $tool_name $tool_args"
        elif [[ "$line" == "event: error" ]]; then
            read -r data_line
            echo -e "\n${RED}[错误]${NC} $data_line"
        elif [[ "$line" == "event: stream_end" || "$line" == "event: done" ]]; then
            echo ""
            echo "────────────────────────────────────────────────────────────"
            log_info "回复结束。"
        elif [[ "$line" == "event: title" ]]; then
            read -r data_line
            local title
            title=$(echo "$data_line" | sed 's/^data: //' | jq -r '.title // empty' 2>/dev/null)
            if [[ -n "$title" ]]; then
                log_info "会话标题: $title"
            fi
        fi
    done
}

# 同步发送消息（POST /api/chat，阻塞等完整回复）
send_sync() {
    local session_id="$1"
    local message="$2"
    local model="${3:-}"
    local mode="${4:-}"

    ensure_cookie

    local req_body
    req_body=$(jq -n \
        --arg sid "$session_id" \
        --arg msg "$message" \
        --arg model "$model" \
        --arg mode "$mode" \
        '{session_id: $sid, message: $msg} +
         (if $model != "" then {model: $model} else {} end) +
         (if $mode != "" then {mode: $mode} else {} end)')

    log_info "发送同步消息到 session ${session_id} (等待完整回复)..."
    if [[ -n "$model" ]]; then
        log_debug "指定模型: $model"
    fi
    if [[ -n "$mode" ]]; then
        log_debug "忙时模式: $mode"
    fi

    local resp
    resp=$(_curl -X POST "$HERMES_URL/api/chat" \
        -H "Content-Type: application/json" \
        -d "$req_body") || {
        log_error "发送消息失败。"
        exit 1
    }

    local error
    error=$(echo "$resp" | jq -r '.error // empty')
    if [[ -n "$error" ]]; then
        log_error "发送失败: $error"
        exit 1
    fi

    local answer
    answer=$(echo "$resp" | jq -r '.answer // empty')
    local status
    status=$(echo "$resp" | jq -r '.status // "unknown"')

    echo "────────────────────────────────────────────────────────────"
    echo "$answer"
    echo ""
    echo "────────────────────────────────────────────────────────────"
    log_info "状态: $status"
}

# ── 主流程 ────────────────────────────────────────────────────────────────────
main() {
    check_deps

    local op_mode=""        # streaming (default) | sync | list | create | check
    local session_id=""
    local message=""
    local model=""
    local busy_mode=""      # interrupt | queue | steer | reject

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h) usage ;;
            --session|-s)
                session_id="${2:-}"
                [[ -z "$session_id" ]] && { log_error "--session 需要参数"; exit 1; }
                shift 2 ;;
            --new|-n)
                op_mode="new"
                shift ;;
            --model|-m)
                model="${2:-}"
                [[ -z "$model" ]] && { log_error "--model 需要参数"; exit 1; }
                shift 2 ;;
            --mode)
                busy_mode="${2:-}"
                [[ -z "$busy_mode" ]] && { log_error "--mode 需要参数"; exit 1; }
                if [[ "$busy_mode" != "interrupt" && "$busy_mode" != "queue" && "$busy_mode" != "steer" && "$busy_mode" != "reject" ]]; then
                    log_error "--mode 必须为 interrupt、queue、steer 或 reject"
                    exit 1
                fi
                shift 2 ;;
            --sync|-S)
                op_mode="sync"
                shift ;;
            --list|-l)
                op_mode="list"
                shift ;;
            --create-session)
                op_mode="create"
                shift ;;
            --check-auth)
                op_mode="check"
                shift ;;
            --url)
                HERMES_URL="${2:-}"
                [[ -z "$HERMES_URL" ]] && { log_error "--url 需要参数"; exit 1; }
                shift 2 ;;
            -*)
                log_error "未知选项: $1"
                usage ;;
            *)
                message="$1"
                shift ;;
        esac
    done

    # 去掉 URL 末尾的 /
    HERMES_URL="${HERMES_URL%/}"

    # ── 仅列举 / 创建 / 检查模式 ──
    case "$op_mode" in
        list)
            list_sessions
            exit 0 ;;
        create)
            create_session
            exit 0 ;;
        check)
            ensure_cookie
            if auth_is_enabled; then
                if cookie_is_valid; then
                    log_info "已登录，cookie 有效（${COOKIE_FILE}）"
                else
                    log_error "未登录或 cookie 已过期"
                    exit 1
                fi
            else
                log_info "认证未启用，无需登录。"
            fi
            exit 0 ;;
    esac

    # ── 需要 message 的模式 ──
    if [[ -z "$message" ]]; then
        log_error "请提供消息内容。"
        echo ""
        usage
    fi

    # --new 模式：自动创建 session
    if [[ "$op_mode" == "new" ]]; then
        log_info "创建新 session..."
        session_id=$(create_session)
        log_info "新 session: $session_id"
    fi

    if [[ -z "$session_id" ]]; then
        log_error "请指定 --session <id> 或使用 --new 自动创建。"
        exit 1
    fi

    # 发送消息
    if [[ "$op_mode" == "sync" ]]; then
        send_sync "$session_id" "$message" "$model" "$busy_mode"
    else
        send_streaming "$session_id" "$message" "$model" "$busy_mode"
    fi
}

main "$@"
