"""
Hermes Web UI -- SSE streaming engine and agent thread runner.
Includes Sprint 10 cancel support via CANCEL_FLAGS.
"""
import contextlib
import json
import logging
import os
import queue
import re
import threading
import time
import traceback
import copy
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from api.config import (
    STREAMS, STREAMS_LOCK, CANCEL_FLAGS, AGENT_INSTANCES, STREAM_PARTIAL_TEXT,
    LOCK, SESSIONS, SESSION_DIR,
    _get_session_agent_lock, _set_thread_env, _clear_thread_env,
    SESSION_AGENT_LOCKS, SESSION_AGENT_LOCKS_LOCK,
    resolve_model_provider, ReplayableStream,
)
from api.helpers import redact_session_data
from api.stream_manager import stream_manager
from api.metering import meter

# Global lock for os.environ writes. Per-session locks (_agent_lock) prevent
# concurrent runs of the SAME session, but two DIFFERENT sessions can still
# interleave their os.environ writes. This global lock serializes the env
# save/restore around the entire agent run.
_ENV_LOCK = threading.Lock()
_REASONING_SEP_MARK = "<|reasoning_sep|>"

# Maps session_id → threading.Event, set when cancel_stream completes.
# _resolve_busy_handling in routes.py spin-waits on this event so a new
# /api/chat/start arriving mid-cancel waits for the cancel to finish
# instead of getting a 409 "session busy" error.
_CANCEL_EVENTS: dict = {}


# Sentinel CDP URL written to os.environ when a SessionDisplay-managed Chrome
# is available.  agent-browser-wrapper.sh detects this value and resolves the
# real CDP URL from ~/.hermes/webui/sessions_display/<sid>.json.  Using a
# constant sentinel makes the env-var immune to cross-session races: every
# SessionDisplay session writes the same value, so overwriting is harmless.
CDP_URL_SENTINEL = "http://127.0.0.1:19999"

# Lazy import to avoid circular deps -- hermes-agent is on sys.path via api/config.py
try:
    from run_agent import AIAgent
except ImportError:
    AIAgent = None

def _get_or_stub_session(session_id: str):
    """Return a session object for *session_id*, or a lightweight stub if the
    session is not yet known to the WebUI (e.g. a child session created by the
    hermes-agent SDK whose metadata hasn't been synced to the WebUI store).

    Checks SESSIONS in-memory cache first (for placeholder sessions created
    during subagent.start), then falls back to DB via get_session().
    """
    from api.models import SESSIONS, LOCK
    # Check in-memory cache first (includes placeholder sessions)
    with LOCK:
        cached = SESSIONS.get(session_id)
    if cached is not None:
        return cached
    try:
        return get_session(session_id)
    except (KeyError, Exception):
        import types
        return types.SimpleNamespace(session_id=session_id)


def _get_ai_agent():
    """Return AIAgent class, retrying the import if the initial attempt failed.

    auto_install_agent_deps() in server.py may install missing packages after
    this module is first imported (common in Docker with a volume-mounted agent).
    Re-attempting the import here picks up the newly installed packages without
    requiring a server restart.
    """
    global AIAgent
    if AIAgent is None:
        try:
            from run_agent import AIAgent as _cls  # noqa: PLC0415
            AIAgent = _cls
        except ImportError:
            pass
    return AIAgent
from api.models import get_session, title_from
from api.workspace import set_last_workspace

# Fields that are safe to send to LLM provider APIs.
# Everything else (attachments, timestamp, _ts, etc.) is display-only


def _strip_thinking_markup(text: str) -> str:
    """Remove common reasoning/thinking wrappers from model text."""
    if not text:
        return ''
    s = str(text)
    s = re.sub(r'<think>.*?</think>', ' ', s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r'<\|channel\|>thought.*?<channel\|>', ' ', s, flags=re.IGNORECASE | re.DOTALL)
    s = re.sub(r'<\|turn\|>thinking\n.*?<turn\|>', ' ', s, flags=re.IGNORECASE | re.DOTALL)  # Gemma 4
    s = re.sub(r'^\s*(the|ther)\s+user\s+is\s+asking.*$', ' ', s, flags=re.IGNORECASE | re.MULTILINE)
    # Strip plain-text thinking preambles from models that don't use <think> tags (e.g. Qwen3).
    # These appear as the very first sentence of the assistant response and are not useful as titles.
    s = re.sub(
        r"^\s*(?:here(?:'s| is) (?:a |my )?(?:thinking|thought) (?:process|trace|through)\b[^\n]*\n?"
        r"|let me (?:think|work|reason|analyze|walk) (?:through|about|this|step)\b[^\n]*\n?"
        r"|i(?:'ll| will) (?:think|work|reason|analyze|break this down)\b[^\n]*\n?"
        r"|(?:okay|alright|sure|of course),?\s+let me\b[^\n]*\n?)",
        ' ', s, flags=re.IGNORECASE
    )
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _strip_xml_tool_calls(text: str) -> str:
    """Strip XML-style function_calls blocks that DeepSeek and similar models
    emit in their raw response text.  These blocks are processed separately as
    tool calls; leaving them in the assistant content causes them to render
    visibly in the chat bubble.

    Handles both complete blocks (<function_calls>…</function_calls>) and
    partial/orphaned opening tags that may appear at the tail of a stream.
    Also handles variants like <｜DSML｜function_calls> from DeepSeek on Bedrock.
    """
    if not text:
        return text
    s = str(text)
    # Check if contains any function_calls/DSML marker (case-insensitive)
    _lo = s.lower()
    if 'function_calls' not in _lo and 'dsml' not in _lo:
        return text
    
    _dsml_prefix = r'(?:\s*｜\s*DSML\s*[｜|]\s*)?'
    open_tag = rf'<{_dsml_prefix}function_calls'
    close_tag = rf'</{_dsml_prefix}function_calls>'
    # Strip complete blocks for both <function_calls> and <｜DSML｜function_calls>.
    s = re.sub(
        rf'{open_tag}>.*?{close_tag}',
        '',
        s,
        flags=re.IGNORECASE | re.DOTALL
    )
    # Strip orphaned/truncated opening tags, including missing ">" at stream tail.
    s = re.sub(
        rf'{open_tag}(?:>|$).*$',
        '',
        s,
        flags=re.IGNORECASE | re.DOTALL
    )
    # Remove malformed DSML fragments like "<｜DSML |" that can leak in tokens.
    s = re.sub(r'<\s*｜\s*DSML\s*[｜|]\s*', '', s, flags=re.IGNORECASE)
    return s.strip()


def _sanitize_generated_title(text: str) -> str:
    """Sanitize LLM-generated title text before persisting to session."""
    s = _strip_thinking_markup(text or '')
    s = re.sub(
        r'^\s*(?:[*_`~]+\s*)?(?:session\s+title|title)\s*:\s*(?:[*_`~]+\s*)?',
        '',
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r'^\s*title\s*:\s*', '', s, flags=re.IGNORECASE)
    s = s.strip(" \t\r\n\"'`*_~")
    s = re.sub(r'\s+', ' ', s).strip()
    # Guard against chain-of-thought leakage and meta-reasoning patterns.
    if _looks_invalid_generated_title(s):
        return ''
    return s[:80]


def _looks_invalid_generated_title(text: str) -> bool:
    s = str(text or '')
    if not s.strip():
        return True
    return bool(
        re.search(r'<think>|<\|channel\|>thought|<\|turn\|>thinking', s, flags=re.IGNORECASE)
        or re.search(r'^\s*(the|ther)\s+user\s+', s, flags=re.IGNORECASE)
        or re.search(r'^\s*user\s+\w+\s+', s, flags=re.IGNORECASE)
        or re.search(r'\b(they|user)\s+want(s)?\s+me\s+to\b', s, flags=re.IGNORECASE)
        or re.search(r'^\s*(i|we)\s+(should|need to|will|can)\b', s, flags=re.IGNORECASE)
        or re.search(r'^\s*let me\b', s, flags=re.IGNORECASE)
        or re.search(r"^\s*here(?:'s| is) (?:a |my )?(?:thinking|thought)", s, flags=re.IGNORECASE)
        or re.search(r'^\s*(ok|okay|done|all set|complete|completed|finished)\b[\s.!?]*$', s, flags=re.IGNORECASE)
    )


def _message_text(value) -> str:
    """Extract plain text from mixed message content payloads."""
    if isinstance(value, list):
        parts = []
        for p in value:
            if not isinstance(p, dict):
                continue
            ptype = str(p.get('type') or '').lower()
            if ptype in ('', 'text', 'input_text', 'output_text'):
                parts.append(str(p.get('text') or p.get('content') or ''))
        return _strip_thinking_markup('\n'.join(parts).strip())
    return _strip_thinking_markup(str(value or '').strip())


def _first_exchange_snippets(messages):
    """Return (first_user_text, first_assistant_text) snippets for title generation.

    Prefer the first substantive assistant answer in the opening exchange,
    skipping empty placeholders and assistant tool-call preambles.
    """
    user_text = ''
    asst_text = ''
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get('role')
        if role == 'user':
            candidate = _message_text(m.get('content'))
            if not user_text and candidate:
                user_text = candidate
                continue
            if user_text and candidate:
                break
        elif role == 'assistant' and user_text:
            candidate = _message_text(m.get('content'))
            # Skip tool-call preambles *only* when content is empty or looks
            # like meta-reasoning ("Let me check my memory first.", "The user
            # is asking...", etc.). Assistant rows that carry tool_calls but
            # also contain a substantive answer text are kept — those are
            # agentic first-turn plans that are legitimate title candidates.
            if m.get('tool_calls') and (not candidate or _looks_invalid_generated_title(candidate)):
                continue
            if candidate:
                asst_text = candidate
        if user_text and asst_text:
            break
    return user_text[:500], asst_text[:500]


def _latest_exchange_snippets(messages):
    """Return (last_user_text, last_assistant_text) snippets for title refresh.

    Walks the message list backwards to find the last user+assistant pair,
    skipping empty or tool-call-only assistant messages.
    """
    user_text = ''
    asst_text = ''
    for m in reversed(messages or []):
        if not isinstance(m, dict):
            continue
        role = m.get('role')
        if role == 'assistant' and not asst_text:
            candidate = _message_text(m.get('content'))
            # Skip tool-call-only preambles
            if m.get('tool_calls') and (not candidate or _looks_invalid_generated_title(candidate)):
                continue
            if candidate:
                asst_text = candidate
        elif role == 'user' and not user_text:
            candidate = _message_text(m.get('content'))
            if candidate:
                user_text = candidate
        if user_text and asst_text:
            break
    return user_text[:500], asst_text[:500]


def _count_exchanges(messages):
    """Count the number of user messages (rough exchange count)."""
    count = 0
    for m in messages or []:
        if isinstance(m, dict) and m.get('role') == 'user':
            content = m.get('content', '')
            if isinstance(content, list):
                content = ' '.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text')
            if str(content).strip():
                count += 1
    return count


def _get_title_refresh_interval() -> int:
    """Read the auto_title_refresh_every setting (0 = disabled)."""
    try:
        from api.config import load_settings
        settings = load_settings()
        val = settings.get('auto_title_refresh_every', '0')
        return int(val) if str(val).strip().isdigit() and int(val) > 0 else 0
    except Exception:
        return 0


def _is_provisional_title(current_title: str, messages) -> bool:
    """Heuristic: title equals first-message substring placeholder."""
    derived = title_from(messages, '') or ''
    if not derived:
        return False
    current = re.sub(r'\s+', ' ', str(current_title or '')).strip()
    candidate = re.sub(r'\s+', ' ', str(derived[:64] or '')).strip()
    if not current or not candidate:
        return False
    return current == candidate or candidate.startswith(current)


def _title_prompts(user_text: str, assistant_text: str) -> tuple[str, list[str]]:
    qa = f"User question:\n{user_text[:500]}\n\nAssistant answer:\n{assistant_text[:500]}"
    prompts = [
        (
            "Generate a short session title from this conversation start.\n"
            "Use BOTH the user's question and the assistant's visible answer.\n"
            "Return only the title text, 3-8 words, as a topic label.\n"
            "Do not use markdown, bullets, labels, or prefixes like Session Title:.\n"
            "Do not output a full sentence.\n"
            "Do not output acknowledgements or completion phrases like OK, done, or all set.\n"
            "Do not describe internal reasoning.\n"
            "Bad: The user is asking..., OK, all set.\n"
            "Good: Title Generation Test, Clarify Dialog Layout, GitHub Issue Triage"
        ),
        (
            "Rewrite this conversation start as a concise noun-phrase title.\n"
            "Use the actual topic, not the task outcome.\n"
            "Return title text only.\n"
            "Do not use markdown, bullets, labels, or prefixes like Session Title:.\n"
            "Never output acknowledgements, completion status, or meta commentary."
        ),
    ]
    return qa, prompts


def _is_minimax_route(provider: str = '', model: str = '', base_url: str = '') -> bool:
    text = ' '.join([
        str(provider or '').lower(),
        str(model or '').lower(),
        str(base_url or '').lower(),
    ])
    return 'minimax' in text or 'minimaxi.com' in text


def _aux_title_configured() -> bool:
    """Return True when any auxiliary title_generation config field is meaningfully set."""
    try:
        from agent.auxiliary_client import _get_auxiliary_task_config
        tg = _get_auxiliary_task_config('title_generation')
        provider = tg.get('provider', '') or ''
        model = tg.get('model', '') or ''
        base_url = tg.get('base_url', '') or ''
        return bool(model or base_url or (provider and provider.lower() != 'auto'))
    except Exception:
        return False

def _aux_title_timeout(default: float = 15.0) -> float:
    """Return the configured timeout (seconds) for auxiliary title generation.

    Only accepts positive numeric values.  Falls back to *default* when the
    value is ``None``, non-numeric, zero, or negative, and emits a debug log
    so mis-configurations are visible in server output.
    """
    try:
        from agent.auxiliary_client import _get_auxiliary_task_config
        tg = _get_auxiliary_task_config('title_generation')
        raw = tg.get('timeout')
        if raw is None:
            return default
        try:
            value = float(raw)
        except (ValueError, TypeError):
            logger.debug("aux title timeout: non-numeric value %r, falling back to %s", raw, default)
            return default
        if value > 0:
            return value
        logger.debug("aux title timeout: non-positive value %s, falling back to %s", value, default)
        return default
    except Exception:
        return default

def _title_completion_budget(provider: str = '', model: str = '', base_url: str = '') -> int:
    # Title generation is a small auxiliary task, but reasoning models may
    # spend a surprising amount of the completion budget before emitting final
    # content.  Keep the budget high enough for MiniMax/Kimi-style reasoning
    # responses without making title generation depend on provider-specific
    # one-off branches.
    return 512


def _title_retry_completion_budget(provider: str = '', model: str = '', base_url: str = '') -> int:
    return max(1024, _title_completion_budget(provider, model, base_url) * 2)


def _title_retry_status(status: str) -> bool:
    return status in {
        'llm_length',
        'llm_length_aux',
        'llm_empty_reasoning',
        'llm_empty_reasoning_aux',
    }


def _safe_obj_value(obj, key: str):
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    value = getattr(obj, key, None)
    # Missing MagicMock attrs stringify as mock reprs and look truthy.  Treat
    # them as absent so tests model real provider objects accurately.
    if value.__class__.__module__.startswith('unittest.mock'):
        return None
    return value


def _safe_text_value(value) -> str:
    if value is None:
        return ''
    if value.__class__.__module__.startswith('unittest.mock'):
        return ''
    return str(value or '').strip()


def _extract_title_response(resp, *, aux: bool = False) -> tuple[str, str]:
    """Return (content, empty_status) from an OpenAI-compatible response."""
    suffix = '_aux' if aux else ''
    try:
        choices = _safe_obj_value(resp, 'choices') or []
        choice = choices[0] if choices else None
        message = _safe_obj_value(choice, 'message')
        content = _safe_text_value(_safe_obj_value(message, 'content'))
        if content:
            return content, ''
        finish_reason = _safe_text_value(_safe_obj_value(choice, 'finish_reason')).lower()
        reasoning = (
            _safe_text_value(_safe_obj_value(message, 'reasoning'))
            or _safe_text_value(_safe_obj_value(message, 'reasoning_content'))
            or _safe_text_value(_safe_obj_value(message, 'thinking'))
        )
        if finish_reason == 'length':
            return '', f'llm_length{suffix}'
        if reasoning:
            return '', f'llm_empty_reasoning{suffix}'
        return '', f'llm_empty{suffix}'
    except Exception:
        return '', f'llm_empty{suffix}'


def generate_title_raw_via_aux(
    user_text: str,
    assistant_text: str,
    provider: str = '',
    model: str = '',
    base_url: str = '',
) -> tuple[Optional[str], str]:
    """Return (raw_text, status) via auxiliary LLM route."""
    if not user_text or not assistant_text:
        return None, 'missing_exchange'
    qa, prompts = _title_prompts(user_text, assistant_text)
    base_max_tokens = _title_completion_budget(provider, model, base_url)
    reasoning_extra = {"reasoning": {"enabled": False}}
    if _is_minimax_route(provider, model, base_url):
        reasoning_extra["reasoning_split"] = True
    try:
        _timeout = _aux_title_timeout()
        from agent.auxiliary_client import call_llm
        last_status = 'llm_error_aux'
        for idx, prompt in enumerate(prompts):
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": qa},
            ]
            budgets = [base_max_tokens]
            try:
                for budget_idx, max_tokens in enumerate(budgets):
                    resp = call_llm(
                        task='title_generation',
                        provider=provider or None,
                        model=model or None,
                        base_url=base_url or None,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=0.2,
                        timeout=_timeout,
                        extra_body=reasoning_extra,
                    )
                    raw, empty_status = _extract_title_response(resp, aux=True)
                    if raw:
                        return raw, ('llm_aux' if idx == 0 and budget_idx == 0 else 'llm_aux_retry')
                    last_status = empty_status or 'llm_empty_aux'
                    if budget_idx == 0 and _title_retry_status(last_status):
                        budgets.append(_title_retry_completion_budget(provider, model, base_url))
            except Exception as e:
                last_status = 'llm_error_aux'
                logger.debug("Aux title generation attempt %s failed: %s", idx + 1, e)
        return None, last_status
    except Exception as e:
        logger.debug("Aux title generation failed: %s", e)
        return None, 'llm_error_aux'


def generate_title_raw_via_agent(agent, user_text: str, assistant_text: str) -> tuple[Optional[str], str]:
    """Return (raw_text, status) via active-agent route."""
    if not user_text or not assistant_text:
        return None, 'missing_exchange'
    if agent is None:
        return None, 'missing_agent'

    qa, prompts = _title_prompts(user_text, assistant_text)
    base_max_tokens = _title_completion_budget(
        getattr(agent, 'provider', ''),
        getattr(agent, 'model', ''),
        getattr(agent, 'base_url', ''),
    )
    disabled_reasoning = {"enabled": False}
    prev_reasoning = getattr(agent, 'reasoning_config', None)
    try:
        agent.reasoning_config = disabled_reasoning
        for idx, prompt in enumerate(prompts):
            api_messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": qa},
            ]
            budgets = [base_max_tokens]
            try:
                last_status = 'llm_empty'
                for budget_idx, max_tokens in enumerate(budgets):
                    raw = ""
                    empty_status = ''
                    if getattr(agent, 'api_mode', '') == 'codex_responses':
                        codex_kwargs = agent._build_api_kwargs(api_messages)
                        codex_kwargs.pop('tools', None)
                        if 'max_output_tokens' in codex_kwargs:
                            codex_kwargs['max_output_tokens'] = max_tokens
                        resp = agent._run_codex_stream(codex_kwargs)
                        assistant_message, _ = agent._normalize_codex_response(resp)
                        raw = (assistant_message.content or '') if assistant_message else ''
                        if not raw:
                            empty_status = 'llm_empty'
                    elif getattr(agent, 'api_mode', '') == 'anthropic_messages':
                        from agent.anthropic_adapter import build_anthropic_kwargs, normalize_anthropic_response
                        ant_kwargs = build_anthropic_kwargs(
                            model=agent.model,
                            messages=api_messages,
                            tools=None,
                            max_tokens=max_tokens,
                            reasoning_config=disabled_reasoning,
                            is_oauth=getattr(agent, '_is_anthropic_oauth', False),
                            preserve_dots=agent._anthropic_preserve_dots(),
                            base_url=getattr(agent, '_anthropic_base_url', None),
                        )
                        resp = agent._anthropic_messages_create(ant_kwargs)
                        assistant_message, _ = normalize_anthropic_response(
                            resp, strip_tool_prefix=getattr(agent, '_is_anthropic_oauth', False)
                        )
                        raw = (assistant_message.content or '') if assistant_message else ''
                        if not raw:
                            empty_status = 'llm_empty'
                    else:
                        api_kwargs = agent._build_api_kwargs(api_messages)
                        api_kwargs.pop('tools', None)
                        api_kwargs['temperature'] = 0.1
                        api_kwargs['timeout'] = 15.0
                        if _is_minimax_route(getattr(agent, 'provider', ''), getattr(agent, 'model', ''), getattr(agent, 'base_url', '')):
                            extra_body = dict(api_kwargs.get('extra_body') or {})
                            extra_body['reasoning_split'] = True
                            api_kwargs['extra_body'] = extra_body
                        if 'max_completion_tokens' in api_kwargs:
                            api_kwargs['max_completion_tokens'] = max_tokens
                        else:
                            api_kwargs['max_tokens'] = max_tokens
                        resp = agent._ensure_primary_openai_client(reason='title_generation').chat.completions.create(
                            **api_kwargs,
                        )
                        raw, empty_status = _extract_title_response(resp)
                    raw = str(raw or '').strip()
                    if raw:
                        return raw, ('llm' if idx == 0 and budget_idx == 0 else 'llm_retry')
                    last_status = empty_status or 'llm_empty'
                    if budget_idx == 0 and _title_retry_status(last_status):
                        budgets.append(_title_retry_completion_budget(
                            getattr(agent, 'provider', ''),
                            getattr(agent, 'model', ''),
                            getattr(agent, 'base_url', ''),
                        ))
            except Exception as e:
                last_status = 'llm_error'
                logger.debug(
                    "Agent title generation attempt %s failed: provider=%s model=%s error=%s",
                    idx + 1,
                    getattr(agent, 'provider', None),
                    getattr(agent, 'model', None),
                    e,
                )
        return None, last_status
    except Exception as e:
        logger.debug("Agent title generation failed: %s", e)
        return None, 'llm_error'
    finally:
        agent.reasoning_config = prev_reasoning


def _generate_llm_session_title_for_agent(agent, user_text: str, assistant_text: str) -> tuple[Optional[str], str, str]:
    """Generate a title via active-agent route, then sanitize/validate result."""
    raw, status = generate_title_raw_via_agent(agent, user_text, assistant_text)
    if not raw:
        return None, status, ''
    title = _sanitize_generated_title(raw)
    if title:
        return title, status, ''
    return None, 'llm_invalid', str(raw)[:120]


def _generate_llm_session_title_via_aux(user_text: str, assistant_text: str, agent=None, *, use_agent_model: bool = False) -> tuple[Optional[str], str, str]:
    """Generate a title via dedicated auxiliary LLM route, then sanitize/validate result.

    When use_agent_model is False (default), the auxiliary client resolves
    provider/model/base_url from config.yaml auxiliary.title_generation, which
    prevents the session's chat model (e.g. a Chinese model) from overriding
    the dedicated title model.  When True, the agent's attrs are passed through
    (legacy fallback behaviour).
    """
    if use_agent_model and agent:
        provider = getattr(agent, 'provider', '')
        model = getattr(agent, 'model', '')
        base_url = getattr(agent, 'base_url', '')
    else:
        provider = ''
        model = ''
        base_url = ''
    raw, status = generate_title_raw_via_aux(
        user_text,
        assistant_text,
        provider=provider,
        model=model,
        base_url=base_url,
    )
    if not raw:
        return None, status, ''
    title = _sanitize_generated_title(raw)
    if title:
        return title, status, ''
    return None, 'llm_invalid_aux', str(raw)[:120]


def _put_title_status(put_event, session_id: str, status: str, reason: str = '', title: str = '', raw_preview: str = '') -> None:
    payload = {'session_id': session_id, 'status': status}
    if reason:
        payload['reason'] = reason
    if title:
        payload['title'] = title
    if raw_preview:
        payload['raw_preview'] = raw_preview
    put_event('title_status', payload)
    logger.info(
        "title_status session=%s status=%s reason=%s title=%r raw_preview=%r",
        session_id,
        status,
        reason or '-',
        title or '',
        (raw_preview or '')[:120],
    )


def _fallback_title_from_exchange(user_text: str, assistant_text: str) -> Optional[str]:
    """Generate a readable local fallback title when LLM title generation fails."""
    user_text = (user_text or '').strip()
    assistant_text = _strip_thinking_markup(assistant_text or '').strip()
    if not user_text:
        return None
    user_text = re.sub(r'^\[Workspace:[^\]]+\]\s*', '', user_text)
    user_text = re.sub(r'\s+', ' ', user_text).strip()
    assistant_text = re.sub(r'\s+', ' ', assistant_text).strip()
    combined = f"{user_text} {assistant_text}".strip().lower()
    combined_raw = f"{user_text} {assistant_text}".strip()

    def _contains_latin(text: str) -> bool:
        return bool(re.search(r'[A-Za-z]', text or ''))

    def _extract_named_topic(text: str) -> str:
        m = re.search(r'"([^"\n]{2,24})"', text)
        if m:
            return (m.group(1) or '').strip()
        m = re.search(r'“([^”\n]{2,24})”', text)
        if m:
            return (m.group(1) or '').strip()
        return ''

    topic_name = _extract_named_topic(combined_raw)
    if topic_name:
        if not _contains_latin(topic_name):
            if any(k in combined for k in ('time', 'schedule', 'efficiency', 'manage', 'fitness', 'singing', 'calligraphy')):
                return 'Time management discussion'
            if any(k in combined for k in ('hermes', 'codex', 'ai')):
                return 'AI productivity discussion'
            return 'Conversation topic'
        if any(k in combined for k in ('time', 'schedule', 'efficiency', 'manage', 'fitness', 'singing', 'calligraphy')):
            return f'{topic_name} time management'
        if any(k in combined for k in ('hermes', 'codex', 'ai')):
            return f'{topic_name} AI productivity'
        return f'{topic_name} discussion'

    if any(k in combined for k in ('title', 'session title')) and any(k in combined for k in ('summary', 'summar', 'short title')):
        if any(k in combined for k in ('test', 'ok', 'reply ok')):
            return 'Session title auto-summary test'
        return 'Session title auto-summary'
    if any(k in combined for k in ('clarify', 'clarification')) and any(k in combined for k in ('dialog', 'card')):
        return 'Clarify dialog card'
    if any(k in combined for k in ('issue', 'github', 'pr')) and any(k in combined for k in ('triage', 'bug', 'review')):
        return 'GitHub Issue Triage'

    head = re.split(r'[.!?\n]', user_text)[0].strip()
    if not head:
        return None

    stop_en = {
        'the', 'this', 'that', 'with', 'from', 'into', 'just', 'reply', 'please',
        'need', 'needs', 'want', 'wants', 'user', 'assistant', 'could', 'would',
        'should', 'about', 'there', 'here', 'test', 'testing', 'title', 'summary',
    }
    tokens = re.findall(r'[A-Za-z0-9][A-Za-z0-9_./+-]*', head)
    if not tokens:
        return 'Conversation topic'

    picked = []
    for tok in tokens:
        lower_tok = tok.lower()
        if lower_tok in stop_en or len(lower_tok) < 3:
            continue
        if tok not in picked:
            picked.append(tok)
        if len(picked) >= 4:
            break

    if picked:
        return ' '.join(picked)[:60]
    return 'Conversation topic'


def _is_generic_fallback_title(title: str) -> bool:
    """Return True for low-information fallback labels that should not be persisted."""
    return str(title or '').strip().lower() in {'conversation topic'}


def _run_title_update(session_id: str, user_text: str, assistant_text: str, placeholder_title: str, put_event, agent=None):
    """Generate and publish a better title synchronously.

    Called before ``done`` so the frontend sees the final title when it
    reloads the session list on the done event.
    """
    print(f'[title-sync-backend] title update START: session={session_id[:8]} placeholder={placeholder_title!r}', flush=True)
    try:
        try:
            s = get_session(session_id)
        except KeyError:
            print(f'[title-sync-backend] title SKIP: session={session_id[:8]} reason=missing_session', flush=True)
            _put_title_status(put_event, session_id, 'skipped', 'missing_session')
            return
        # Allow self-heal when a previously generated title leaked thinking text.
        _invalid_existing = _looks_invalid_generated_title(s.title)
        if getattr(s, 'llm_title_generated', False) and not _invalid_existing:
            print(f'[title-sync-backend] title SKIP: session={session_id[:8]} reason=already_generated llm_title={getattr(s, "llm_title_generated", False)!r}', flush=True)
            _put_title_status(put_event, session_id, 'skipped', 'already_generated', str(s.title or ''))
            return
        current = str(s.title or '').strip()
        still_auto = (
            current == placeholder_title
            or current in ('Untitled', 'New Chat', '')
            or _is_provisional_title(current, s.messages)
            or _invalid_existing
        )
        if not still_auto:
            _put_title_status(put_event, session_id, 'skipped', 'manual_title', current)
            return
        aux_title_configured = _aux_title_configured()
        if agent and not aux_title_configured:
            next_title, llm_status, raw_preview = _generate_llm_session_title_for_agent(agent, user_text, assistant_text)
            if not next_title and llm_status in ('llm_error', 'llm_invalid'):
                next_title, llm_status, raw_preview = _generate_llm_session_title_via_aux(user_text, assistant_text, agent=agent, use_agent_model=True)
        else:
            next_title, llm_status, raw_preview = _generate_llm_session_title_via_aux(user_text, assistant_text)
            if not next_title and agent and llm_status in ('llm_error_aux', 'llm_invalid_aux'):
                next_title, llm_status, raw_preview = _generate_llm_session_title_for_agent(agent, user_text, assistant_text)
        source = llm_status
        if not next_title:
            fallback_title = _fallback_title_from_exchange(user_text, assistant_text)
            if fallback_title and not _is_generic_fallback_title(fallback_title):
                logger.debug("Using local fallback for session title generation")
                next_title = fallback_title
                source = 'fallback'
            elif fallback_title:
                logger.debug("Skipping generic local fallback for session title generation: %r", fallback_title)
        fallback_reason = (
            f'local_summary:{llm_status}'
            if source == 'fallback' and llm_status
            else 'local_summary'
        )
        wrote_title = False
        effective_title = current
        if next_title:
            with _get_session_agent_lock(session_id):
                with LOCK:
                    s = SESSIONS.get(session_id, s)
                    effective_title = str(s.title or '').strip()
                    invalid_existing_now = _looks_invalid_generated_title(s.title)
                    still_auto = (
                        effective_title == placeholder_title
                        or effective_title in ('Untitled', 'New Chat', '')
                        or _is_provisional_title(effective_title, s.messages)
                        or invalid_existing_now
                    )
                if not still_auto:
                    _put_title_status(put_event, session_id, 'skipped', 'manual_title', effective_title)
                    return
                if next_title != effective_title:
                    s.title = next_title
                    s.llm_title_generated = True
                    # Keep chronological ordering stable in the sidebar.
                    s.sync_webui_metadata(touch_updated_at=False)
                    effective_title = s.title
                    wrote_title = True

        if wrote_title:
            if source == 'fallback':
                _put_title_status(put_event, session_id, source, fallback_reason, effective_title, raw_preview)
            else:
                _put_title_status(put_event, session_id, source, llm_status, effective_title, raw_preview)
            put_event('title', {'session_id': session_id, 'title': effective_title})
            print(f'[title-sync-backend] title event SENT: session={session_id[:8]} title={effective_title!r}', flush=True)
        else:
            _put_title_status(put_event, session_id, 'skipped', source or 'unchanged', effective_title, raw_preview)
    except Exception:
        logger.warning("title update failed for %s", session_id, exc_info=True)


def _run_background_title_refresh(session_id: str, user_text: str, assistant_text: str, current_title: str, put_event, agent=None):
    """Refresh an existing LLM-generated title using the latest exchange text.

    Unlike _run_title_update, this does NOT guard on
    llm_title_generated — it assumes the title was already LLM-generated
    and the session has progressed enough to warrant a refresh.
    It does NOT emit stream_end (the caller already did).
    """
    try:
        try:
            s = get_session(session_id)
        except KeyError:
            return
        # Safety: skip if user manually renamed since the check
        effective = str(s.title or '').strip()
        if effective != current_title:
            _put_title_status(put_event, session_id, 'skipped', 'manual_title', effective)
            return
        if not effective or effective in ('Untitled', 'New Chat'):
            return
        aux_title_configured = _aux_title_configured()
        if agent and not aux_title_configured:
            next_title, llm_status, raw_preview = _generate_llm_session_title_for_agent(agent, user_text, assistant_text)
            if not next_title and llm_status in ('llm_error', 'llm_invalid'):
                next_title, llm_status, raw_preview = _generate_llm_session_title_via_aux(user_text, assistant_text, agent=agent, use_agent_model=True)
        else:
            next_title, llm_status, raw_preview = _generate_llm_session_title_via_aux(user_text, assistant_text)
            if not next_title and agent and llm_status in ('llm_error_aux', 'llm_invalid_aux'):
                next_title, llm_status, raw_preview = _generate_llm_session_title_for_agent(agent, user_text, assistant_text)
        if not next_title:
            _put_title_status(put_event, session_id, 'refresh_skipped', llm_status or 'empty', effective, raw_preview)
            return
        # Skip if the new title is essentially the same (after normalization)
        normalized_current = re.sub(r'\s+', ' ', effective).strip().lower()
        normalized_new = re.sub(r'\s+', ' ', next_title).strip().lower()
        if normalized_current == normalized_new:
            _put_title_status(put_event, session_id, 'refresh_skipped', 'same_title', effective, raw_preview)
            return
        with _get_session_agent_lock(session_id):
            with LOCK:
                s = SESSIONS.get(session_id, s)
                # Re-check: user may have renamed while we were generating
                if str(s.title or '').strip() != current_title:
                    _put_title_status(put_event, session_id, 'skipped', 'manual_title', str(s.title or '').strip())
                    return
                s.title = next_title
                s.llm_title_generated = True
                s.sync_webui_metadata(touch_updated_at=False)
                effective_title = s.title
        _put_title_status(put_event, session_id, 'refreshed', llm_status, effective_title, raw_preview)
        put_event('title', {'session_id': session_id, 'title': effective_title})
        logger.info("Adaptive title refresh: session=%s new_title=%r", session_id, effective_title)
    except Exception:
        logger.debug("Background title refresh failed for session %s", session_id, exc_info=True)


def _maybe_schedule_title_refresh(session, put_event, agent):
    """Check if the session is due for an adaptive title refresh and schedule it."""
    refresh_interval = _get_title_refresh_interval()
    if refresh_interval <= 0:
        return
    current_title = str(session.title or '').strip()
    if not current_title or current_title in ('Untitled', 'New Chat'):
        return
    if not getattr(session, 'llm_title_generated', False):
        return
    exchange_count = _count_exchanges(session.messages)
    if exchange_count <= 0 or exchange_count % refresh_interval != 0:
        return
    last_u, last_a = _latest_exchange_snippets(session.messages)
    if not last_u and not last_a:
        return
    threading.Thread(
        target=_run_background_title_refresh,
        args=(session.session_id, last_u, last_a, current_title, put_event, agent),
        daemon=True,
    ).start()


def _sanitize_messages_for_api(messages):
    """Return a copy of messages safe for LLM API consumption.

    Keeps all message fields (including webui metadata like oc_webui and
    timestamp — these are harmless to providers). Only removes:

    - Error messages (_error=True)
    - Orphaned tool results (tool_call_id not matching any assistant tool_calls)
    - Consecutive user messages (merged to maintain role alternation)
    """
    # First pass: collect all tool_call_ids declared by assistant messages.
    # Handles both OpenAI ('id') and Anthropic ('call_id') field names.
    valid_tool_call_ids: set = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get('role') == 'assistant':
            for tc in msg.get('tool_calls') or []:
                if isinstance(tc, dict):
                    tid = tc.get('id') or tc.get('call_id') or ''
                    if tid:
                        valid_tool_call_ids.add(tid)

    # Second pass: build the sanitized list, dropping orphaned tool messages.
    clean = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        # Skip persisted error markers — never send them to the LLM as prior context.
        if msg.get('_error'):
            continue
        role = msg.get('role')
        if role == 'tool':
            tid = msg.get('tool_call_id') or ''
            if not tid or tid not in valid_tool_call_ids:
                # Orphaned tool result — skip to avoid 400 from strict providers.
                continue
        # Shallow copy — keep webui fields (oc_webui, timestamp, etc.)
        # that were unnecessarily stripped by the old _API_SAFE_MSG_KEYS
        # whitelist.  Only harmful fields (_error) are skipped above.
        sanitized = dict(msg)
        # Strip empty tool_calls — some providers (DeepSeek) reject []
        if isinstance(sanitized.get('tool_calls'), list) and not sanitized['tool_calls']:
            del sanitized['tool_calls']
        if sanitized.get('role'):
            clean.append(sanitized)

    # Merge consecutive user messages — strictly-conformant providers (Anthropic, etc.)
    # require strict role alternation (user/assistant/user/...).  A dangling user message
    # at the end of a session (e.g. from a crashed agent turn) followed by a new user
    # message would produce two adjacent user roles and trigger a 400 Bad Request.
    # We merge them by concatenating their content rather than dropping either message.
    # Note: merging only affects the copy sent to the LLM API; the original messages in
    # the session JSON are not modified, so the UI continues to display them separately.
    merged: list = []
    for msg in clean:
        if merged and merged[-1]['role'] == 'user' and msg['role'] == 'user':
            prev = merged[-1]
            prev_c = prev['content']
            cur_c = msg['content']
            # Normalise both sides to list-of-blocks for uniform handling.
            if isinstance(prev_c, str):
                prev_c = [{'type': 'text', 'text': prev_c}]
            if isinstance(cur_c, str):
                cur_c = [{'type': 'text', 'text': cur_c}]
            if not isinstance(prev_c, list):
                prev_c = [{'type': 'text', 'text': str(prev_c)}]
            if not isinstance(cur_c, list):
                cur_c = [{'type': 'text', 'text': str(cur_c)}]
            # [dedup-diagnostic] Log consecutive user message merging
            _prev_snip = str(prev.get('content', ''))[:80]
            _cur_snip = str(msg.get('content', ''))[:80]
            logger.debug(
                "[dedup-diagnostic] _sanitize_messages_for_api MERGE consecutive users | "
                "merged_pos=%d | prev_snip=%r | cur_snip=%r",
                len(merged) - 1, _prev_snip, _cur_snip,
            )
            print(
                f'[dedup] SANITIZE_MERGE pos={len(merged) - 1} '
                f'prev={_prev_snip!r} cur={_cur_snip!r}',
                flush=True,
            )
            prev['content'] = prev_c + cur_c
        else:
            merged.append(msg)

    # [dedup-diagnostic] Log final roles after sanitization
    _before_roles = [m.get('role', '?') for m in messages if isinstance(m, dict)]
    _after_roles = [m.get('role', '?') for m in merged]
    _has_merge = len(merged) < len(clean)
    if _has_merge or len(messages) > 6:
        logger.debug(
            "[dedup-diagnostic] _sanitize_messages_for_api done | "
            "before=%d after=%d | before_roles=%s | after_roles=%s | merged=%s",
            len(messages), len(merged), _before_roles, _after_roles, _has_merge,
        )
    return merged


def _distribute_inline_reasoning(messages):
    """Extract ``<think>…</think>`` blocks from each assistant message's
    content and copy them into the message's ``reasoning`` field.

    Models like DeepSeek, QwQ, and MiniMax embed chain-of-thought directly
    in the response text.  This function pulls those blocks out into the
    structured ``reasoning`` field so the frontend can render them as
    collapsible reasoning cards on each individual message.

    The ``content`` text is **not** modified — the ``<think>`` tags stay
    in place so they continue to participate in API context for subsequent
    turns.  The frontend already strips them at render time via
    ``strippedContent`` / ``extractThinkingText``.

    Already-populated ``reasoning`` fields (e.g. from the agent's native
    ``reasoning_content`` delta path) are left untouched.
    """
    import re as _re
    if not messages:
        return
    _THINK_RE = _re.compile(r'\s*<think>([\s\S]*?)</think>\s*', _re.IGNORECASE)
    for _m in messages:
        if not isinstance(_m, dict) or _m.get('role') != 'assistant':
            continue
        if _m.get('reasoning'):
            # reasoning already set by another path (e.g. reasoning=high)
            continue
        _content = _m.get('content')
        if not isinstance(_content, str) or not _content:
            continue
        _think_matches = list(_THINK_RE.finditer(_content))
        if not _think_matches:
            continue
        _parts = [_match.group(1).strip() for _match in _think_matches]
        _m['reasoning'] = '\n<|reasoning_sep|>\n'.join(
            p for p in _parts if p
        )


def _tool_result_snippet(raw) -> str:
    """Extract a compact result preview from a stored tool message payload."""
    text = str(raw or '')
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return str(data.get('output') or data.get('result') or data.get('error') or text)[:200]
    except Exception:
        pass
    return text[:200]


def _truncate_tool_args(args, limit: int = 6) -> dict:
    """Truncate tool args for compact session persistence."""
    out = {}
    if not isinstance(args, dict):
        return out
    for k, v in list(args.items())[:limit]:
        s = str(v)
        out[k] = s[:120] + ('...' if len(s) > 120 else '')
    return out


def _nearest_assistant_msg_idx(messages, msg_idx: int) -> int:
    """Find the closest preceding assistant message index for a tool result."""
    for idx in range(msg_idx - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, dict) and msg.get('role') == 'assistant':
            return idx
    return -1


def _extract_tool_calls_from_messages(messages, live_tool_calls=None):
    """Build persisted tool-call summaries from final messages plus live progress fallback."""
    tool_calls = []
    pending_names = {}
    pending_args = {}
    pending_asst_idx = {}
    tool_msg_sequence = []

    for msg_idx, m in enumerate(messages or []):
        if not isinstance(m, dict):
            continue
        role = m.get('role')
        if role == 'assistant':
            content = m.get('content', '')
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'tool_use':
                        tid = part.get('id', '')
                        if tid:
                            pending_names[tid] = part.get('name', '')
                            pending_args[tid] = part.get('input', {})
                            pending_asst_idx[tid] = msg_idx
            for tc in (m.get('tool_calls') or []):
                if not isinstance(tc, dict):
                    continue
                tid = tc.get('id', '') or tc.get('call_id', '')
                fn = tc.get('function', {})
                name = fn.get('name', '')
                try:
                    args = json.loads(fn.get('arguments', '{}') or '{}')
                except Exception:
                    args = {}
                if tid and name:
                    pending_names[tid] = name
                    pending_args[tid] = args
                    pending_asst_idx[tid] = msg_idx
        elif role == 'tool':
            tid = m.get('tool_call_id') or m.get('tool_use_id', '')
            raw = m.get('content', '')
            seq = {'msg_idx': msg_idx, 'raw': raw, 'resolved': False}
            if tid:
                name = pending_names.get(tid, '')
                if name and name != 'tool':
                    tool_calls.append({
                        'name': name,
                        'snippet': _tool_result_snippet(raw),
                        'tid': tid,
                        'assistant_msg_idx': pending_asst_idx.get(tid, -1),
                        'args': _truncate_tool_args(pending_args.get(tid, {})),
                    })
                    seq['resolved'] = True
            tool_msg_sequence.append(seq)

    live = [tc for tc in (live_tool_calls or []) if isinstance(tc, dict) and tc.get('name') and tc.get('name') != 'clarify']
    if live:
        for seq_idx, seq in enumerate(tool_msg_sequence):
            if seq.get('resolved'):
                continue
            if seq_idx >= len(live):
                break
            live_tc = live[seq_idx]
            tool_calls.append({
                'name': live_tc.get('name', 'tool'),
                'snippet': _tool_result_snippet(seq.get('raw', '')),
                'tid': live_tc.get('tid', '') or '',
                'assistant_msg_idx': _nearest_assistant_msg_idx(messages, seq.get('msg_idx', -1)),
                'args': _truncate_tool_args(live_tc.get('args', {}), limit=4),
            })

    return tool_calls


def _sse(handler, event, data):
    """Write one SSE event to the response stream."""
    payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    handler.wfile.write(payload.encode('utf-8'))
    handler.wfile.flush()


def _build_session_skills_block(skill_names: list) -> str:
    """Build the content inside <available_skills> for a session-bound skill list.

    Reads each skill's SKILL.md to extract the frontmatter name and description,
    matching the format produced by build_skills_system_prompt().
    Returns an empty string when skill_names is empty or all skills are missing.
    """
    if not skill_names:
        return ''
    try:
        from tools.skills_tool import SKILLS_DIR
        from agent.skill_utils import parse_frontmatter, extract_skill_description
    except ImportError:
        logger.debug('[webui] skill_utils not available for session skill binding')
        return ''

    lines = []
    for name in skill_names:
        try:
            matches = list(SKILLS_DIR.rglob(f'{name}/SKILL.md'))
            if not matches:
                logger.warning('[webui] skill not found for session binding: %s', name)
                continue
            skill_md = matches[0]
            raw = skill_md.read_text(encoding='utf-8')
            fm, _ = parse_frontmatter(raw)
            desc = extract_skill_description(fm)
            frontmatter_name = fm.get('name', name)
            if desc:
                lines.append(f'    - {frontmatter_name}: {desc}')
            else:
                lines.append(f'    - {frontmatter_name}')
        except Exception as _e:
            logger.debug('[webui] Failed to read skill %s for session binding: %s', name, _e)
            lines.append(f'    - {name}')

    if not lines:
        return ''
    return '  session-skills:\n' + '\n'.join(lines)


def _resolve_session_cdp_port(session_id: str) -> int | None:
    """Return the CDP port for a session's SessionDisplay, or None if not running."""
    from api.session_display import SessionDisplay
    info = SessionDisplay.get_info(session_id)
    if info:
        return info.get("cdp_port")
    return None


def _resolve_personality_prompt(config_dict, personality_name):
    """Resolve a personality prompt from config.

    Args:
        config_dict: The full hermes config dict (as returned by get_config()).
        personality_name: Name of the personality to resolve, or None.

    Returns:
        The resolved prompt string, or None if not found / not configured.
    """
    if not personality_name:
        return None
    agent_cfg = config_dict.get('agent', {}) if isinstance(config_dict, dict) else {}
    personalities = agent_cfg.get('personalities', {})
    if not isinstance(personalities, dict) or personality_name not in personalities:
        return None
    pval = personalities[personality_name]
    if isinstance(pval, dict):
        parts = [pval.get('system_prompt', '') or pval.get('prompt', '')]
        if pval.get('tone'):
            parts.append('Tone: ' + pval['tone'])
        if pval.get('style'):
            parts.append('Style: ' + pval['style'])
        return '\n'.join(p for p in parts if p) or None
    return str(pval)


# ═══════════════════════════════════════════════════════════════════════════════
# Message post-processing helpers (extracted from _run_agent_streaming)
# ═══════════════════════════════════════════════════════════════════════════════


def _strip_xml_tool_calls_from_messages(messages):
    """Strip <hermes-tool-call> blocks from all assistant messages in-place."""
    for _m in messages:
        if isinstance(_m, dict) and _m.get('role') == 'assistant':
            _raw_content = _m.get('content')
            if isinstance(_raw_content, str):
                _cleaned = _strip_xml_tool_calls(_raw_content)
                if _cleaned != _raw_content:
                    _m['content'] = _cleaned
            elif isinstance(_raw_content, list):
                for _part in _raw_content:
                    if isinstance(_part, dict) and isinstance(_part.get('text'), str):
                        _part['text'] = _strip_xml_tool_calls(_part['text'])


def _stamp_timestamps_forward_backward(messages, start_idx, all_message_ts):
    """Stamp ``timestamp__webui`` using collected timestamps + backward-fill.

    *start_idx* limits stamping to ``messages[start_idx:]``.
    Messages that already have a truthy ``timestamp__webui`` are skipped
    (idempotent guard for re-application).
    """
    _ts_iter = iter(all_message_ts) if all_message_ts else iter([])
    _current_ts = None
    _new_range = range(start_idx, len(messages))
    # Forward pass: consume timestamps for user / assistant / tool messages
    for _i in _new_range:
        _m = messages[_i]
        if not isinstance(_m, dict):
            continue
        if _m.get('timestamp__webui'):
            continue  # already stamped
        if _m.get('role') in ('user', 'assistant', 'tool'):
            try:
                _current_ts = next(_ts_iter)
            except StopIteration:
                _current_ts = None
        if _current_ts is not None:
            _m['timestamp__webui'] = _current_ts
    # Backward-fill: any message still missing a timestamp inherits from
    # the nearest preceding message that has one.
    for _i in _new_range:
        _m = messages[_i]
        if not isinstance(_m, dict):
            continue
        if _m.get('timestamp__webui'):
            continue
        for _j in range(_i - 1, start_idx - 1, -1):
            _prev = messages[_j]
            if not isinstance(_prev, dict):
                continue
            _prev_ts = _prev.get('timestamp__webui')
            if _prev_ts and _prev_ts > 0:
                _m['timestamp__webui'] = _prev_ts
                break


def _tag_attachments(messages, msg_text, attachments):
    """Tag the matching user message with attachment filenames."""
    if not attachments or not msg_text:
        return
    for m in reversed(messages):
        if m.get('role') == 'user':
            content = str(m.get('content', ''))
            base_text = (
                msg_text.split('\n\n[Attached files:')[0].strip()
                if '\n\n[Attached files:' in msg_text
                else msg_text
            )
            if base_text[:60] in content or content[:60] in msg_text:
                m['attachments'] = attachments
                break


def _postprocess_messages(messages, *, start_idx=0,
                          msg_text=None, attachments=None,
                          all_message_ts=None, reasoning_text=None):
    """Apply all webui post-processing to a message list.

    *start_idx* restricts timestamp stamping to ``messages[start_idx:]``.
    XML stripping and reasoning distribution always operate globally.
    """
    # 1. Strip XML tool-call blocks (global — DB content is always raw)
    _strip_xml_tool_calls_from_messages(messages)

    # 2. Stamp timestamps (only messages[start_idx:])
    if all_message_ts is not None:
        _stamp_timestamps_forward_backward(messages, start_idx, all_message_ts)

    # 3. Distribute inline reasoning (global)
    _distribute_inline_reasoning(messages)

    # 4. Fallback reasoning text
    # Only attach the accumulated trace when NO assistant message already
    # carries per-message reasoning.  DeepSeek V4 etc. stream reasoning on each
    # tool-calling assistant via reasoning_content, which is copied to
    # `reasoning` before this runs — so `reasoning_text` (the whole-turn trace)
    # would otherwise duplicate that reasoning onto the last assistant message
    # (e.g. the final answer, which has no reasoning of its own).
    if reasoning_text and messages:
        _any_reasoning = any(
            isinstance(_rm, dict) and _rm.get('role') == 'assistant' and _rm.get('reasoning')
            for _rm in messages
        )
        if not _any_reasoning:
            for _rm in reversed(messages):
                if isinstance(_rm, dict) and _rm.get('role') == 'assistant':
                    _rm['reasoning'] = reasoning_text
                    break

    # 5. Tag attachments
    if attachments and msg_text:
        _tag_attachments(messages, msg_text, attachments)


def _ensure_process_loop(session, session_id):
    """Ensure the session has exactly one active process loop."""
    if hasattr(session, '_process_loop_thread') and session._process_loop_thread and session._process_loop_thread.is_alive():
        session._process_loop_last_active = time.time()
        logger.debug("[_ensure_process_loop] already alive for session=%s", session_id[:12])
        return
    logger.debug("[_ensure_process_loop] creating new process loop for session=%s", session_id[:12])

    session._process_loop_stop.clear()
    session._process_loop_last_active = time.time()

    def _process_loop():
        """Session-level main loop, only responsible for draining completion events."""
        logger.debug("[process_loop] started for session=%s", session_id[:12])
        _poll_count = 0
        while not session._process_loop_stop.wait(timeout=1.0):
            try:
                from api.config import get_idle_timeout
                if time.time() - session._process_loop_last_active > get_idle_timeout():
                    logger.debug("[process_loop] idle timeout for session=%s", session_id[:12])
                    break
                _poll_count += 1
                if _poll_count % 30 == 1:  # log every 30s
                    from tools.process_registry import process_registry
                    logger.debug("[process_loop] polling session=%s count=%d queue=%d", session_id[:12], _poll_count, process_registry.completion_queue.qsize())
                _drain_process_notifications_for_session(session, session_id)
            except Exception as e:
                logger.warning("[process_loop] error for session=%s: %s", session_id[:12], e)
        logger.debug("[process_loop] exited for session=%s", session_id[:12])

    session._process_loop_thread = threading.Thread(
        target=_process_loop, daemon=True,
        name=f"process-{session_id[:8]}",
    )
    session._process_loop_thread.start()


def _drain_process_notifications_for_session(session, session_id):
    """Drain completion events, spawn new agent thread for each."""
    try:
        from tools.process_registry import process_registry
        from tools.async_delegation import claim_event_delivery, complete_event_delivery

        session_key = f"agent:main:webui:dm:{session_id}"

        def _owns_current_session_event(evt):
            """Exact-match events for the current session."""
            sk = str(evt.get("session_key") or "")
            origin_ui = str(evt.get("origin_ui_session_id") or "")
            target_sid = origin_ui
            if not target_sid and sk.startswith("agent:main:webui:dm:"):
                target_sid = sk.split(":")[-1]
            return target_sid == session_id

        queue_size = process_registry.completion_queue.qsize()
        if queue_size > 0:
            logger.debug("[drain] session=%s queue_size=%d", session_id[:12], queue_size)

        for event, synthetic_message in process_registry.drain_notifications(
            session_key=session_key,
            owns_event=_owns_current_session_event,
        ):
            evt_type = event.get("type", "?")
            evt_sid = event.get("session_id", "?")[:12]
            logger.debug("[drain] event type=%s child=%s session=%s", evt_type, evt_sid, session_id[:12])
            claim = claim_event_delivery(event, "webui-process-loop")
            if claim is None:
                logger.debug("[drain] claim failed, skipping")
                continue
            # Start stream via StreamManager
            sid = stream_manager.begin_stream(session)
            if not sid:
                logger.debug("[drain] begin_stream failed, skipping")
                continue
            logger.debug("[drain] spawning _run_agent_streaming for session=%s new_stream=%s", session_id[:12], sid[:8])
            # Normalize model — same logic as _handle_chat_start.
            # Bare names (e.g. "deepseek-v4-flash") lose the provider hint,
            # causing resolve_runtime_provider to fall back to OpenRouter.
            _drain_model = session.model
            try:
                from api.routes import _resolve_compatible_session_model
                from api.models import Session as _DrainSession
                _drain_model, _ = _resolve_compatible_session_model(_drain_model)
                _drain_model = _DrainSession._normalize_model(_drain_model)
            except Exception:
                pass
            thr = threading.Thread(
                target=_run_agent_streaming,
                args=(session_id, synthetic_message, _drain_model, session.workspace, sid, None, True),
                daemon=True,
            )
            thr.start()
            session._process_loop_last_active = time.time()
            complete_event_delivery(event, claim)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("[drain] error: %s", e)


def _run_agent_streaming(session_id, msg_text, model, workspace, stream_id, attachments=None, drain_message=False, *, ephemeral=False):
    """Run agent in background thread, writing SSE events to stream_manager.

    When ephemeral=True, session mutations are skipped — used by /btw to get
    a streaming answer without persisting to the parent session.
    """
    q = stream_manager.get_stream_by_id(stream_id)
    if q is None:
        return
    s = None
    _rt = {}
    old_cwd = None
    old_exec_ask = None
    old_gateway_session = None
    old_hermes_home = None

    # ── MCP Server Discovery (lazy import, idempotent) ──
    # discover_mcp_tools() is called here (rather than at server startup) so that
    # the hermes-agent package is fully initialized before we try to connect.
    # It is safe to call multiple times — already-connected servers are skipped.
    try:
        from tools.mcp_tool import discover_mcp_tools
        discover_mcp_tools()
    except Exception:
        pass  # MCP not available or not configured — non-fatal

    # Sprint 10: create a cancel event for this stream
    cancel_event = threading.Event()
    with STREAMS_LOCK:
        CANCEL_FLAGS[stream_id] = cancel_event
        STREAM_PARTIAL_TEXT[stream_id] = ''  # start accumulating partial text (#893)

    # Register this stream with the global streaming meter
    meter().begin_session(stream_id)

    # Metering ticker — emits a metering event at 1 Hz while sessions are active.
    # When get_interval() returns >= 10.0 (no active sessions), the ticker exits
    # so no idle readings are emitted and the SSE consumer sees nothing.
    _metering_stop = threading.Event()

    def _metering_ticker():
        while True:
            interval = meter().get_interval()
            if interval >= 10.0:
                break  # nothing active — stop the ticker
            if _metering_stop.wait(interval):
                break  # stream was cancelled or ended — exit
            stats = meter().get_stats()
            stats['session_id'] = stream_id
            put('metering', stats)

    _metering_thread = threading.Thread(target=_metering_ticker, daemon=True)
    _metering_thread.start()

    def put(event, data):
        # If cancelled, drop all further events except the cancel event itself
        if cancel_event.is_set() and event not in ('cancel', 'error'):
            return
        try:
            q.put(event, data)  # ReplayableStream.put() writes to history + fans out to subscribers
        except Exception:
            logger.debug("Failed to put event to queue")

    # Initialised here (before any code that may raise) so the outer `finally`
    # block can safely check `if _checkpoint_stop is not None` even when an
    # exception fires before the checkpoint thread is created (Issue #765).
    _checkpoint_stop = None
    _ckpt_thread = None
    _agent_lock = None
    try:
        s = get_session(session_id)

        # 确保 session 有活跃的主循环（对齐 CLI 的 process_loop）
        _ensure_process_loop(s, session_id)

        put('phase', {'stage': 'initializing'})

        # For drain-loop messages (async delegation completions), send a
        # user_message event so the frontend can display the synthetic user
        # message bubble.  Regular user messages are optimistically added by
        # the frontend before startChat, so they don't need this event.
        if drain_message:
            put('user_message', {
                'text': msg_text,
                'ts': time.time(),
            })
        s.workspace = str(Path(workspace).expanduser().resolve())
        s.model = model

        _agent_lock = _get_session_agent_lock(session_id)
        # TD1: set thread-local env context so concurrent sessions don't clobber globals

        # Resolve profile home for this agent run — use the session's own profile
        # (stamped at new_session() time from the client's S.activeProfile) so that
        # two concurrent tabs on different profiles don't clobber each other via the
        # process-level active-profile global.  Falls back gracefully.
        try:
            from api.profiles import get_hermes_home_for_profile
            _profile_home = str(get_hermes_home_for_profile(getattr(s, 'profile', None)))
        except ImportError:
            _profile_home = os.environ.get('HERMES_HOME', '')

        # Resolve CDP port early — needed for both TLS overrides and os.environ.
        # _cdp_port being non-None means SessionDisplay manages a Chrome for this
        # session and the agent should connect to it.
        _cdp_port = _resolve_session_cdp_port(session_id)

        # ── Concurrency-safe session context (ContextVar, per-thread) ──
        # set_session_vars uses ContextVar so each _run_agent_streaming thread
        # has its own HERMES_SESSION_KEY. This prevents child processes from
        # inheriting a wrong session_key when multiple agents run concurrently.
        from gateway.session_context import set_session_vars, clear_session_vars
        _session_ctx_tokens = set_session_vars(
            session_key=f"agent:main:webui:dm:{session_id}",
            session_id=session_id,
            ui_session_id=session_id,
            source="webui",
            cwd=str(s.workspace),
            cron_session="",
        )

        _set_thread_env(
            TERMINAL_CWD=str(s.workspace),
            HERMES_EXEC_ASK='1',
            HERMES_SESSION_KEY=f"agent:main:webui:dm:{session_id}",
            HERMES_UI_SESSION_ID=session_id,
            HERMES_HOME=_profile_home,
            HERMES_GATEWAY_SESSION='1',
            BROWSER_CDP_URL=(CDP_URL_SENTINEL if _cdp_port else ''),
        )
        # ── os.environ mutation (under global lock) ──────────────────────
        # _IsolatedPopen (activated by _set_thread_env above) protects
        # subprocesses from cross-session env races via thread-local
        # overrides.  We still write to os.environ for two reasons:
        #   1. Python in-process consumers (browser_tool._get_cdp_override)
        #      that read os.environ directly.
        #   2. Subprocesses spawned by code that constructs an explicit
        #      env=... dict from os.environ *before* calling Popen — those
        #      snapshots happen after our writes, so the values must be
        #      correct here.
        with _ENV_LOCK:
            old_cwd = os.environ.get('TERMINAL_CWD')
            old_exec_ask = os.environ.get('HERMES_EXEC_ASK')
            old_gateway_session = os.environ.get('HERMES_GATEWAY_SESSION')
            old_hermes_home = os.environ.get('HERMES_HOME')
            old_cdp_url = os.environ.get('BROWSER_CDP_URL')
            old_http_proxy = os.environ.get('HTTP_PROXY')
            old_https_proxy = os.environ.get('HTTPS_PROXY')
            os.environ['TERMINAL_CWD'] = str(s.workspace)
            os.environ['HERMES_EXEC_ASK'] = '1'
            os.environ['HERMES_GATEWAY_SESSION'] = '1'
            # BROWSER_CDP_URL: use sentinel so agent-browser-wrapper.sh resolves
            # the real port from sessions_display/<sid>.json.  If a global CDP
            # URL is already set (e.g. via CLI /browser connect), keep it —
            # the sentinel must not overwrite a real user-configured value.
            # agent-browser's HTTP client ignores NO_PROXY and routes ALL
            # connections through HTTP_PROXY, including 127.0.0.1 CDP. Clear
            # proxy vars so agent-browser reaches CDP directly.
            _existing_cdp = os.environ.get('BROWSER_CDP_URL', '').strip()
            if _cdp_port:
                if not _existing_cdp or _existing_cdp == CDP_URL_SENTINEL:
                    os.environ['BROWSER_CDP_URL'] = CDP_URL_SENTINEL
                    old_http_proxy = os.environ.pop('HTTP_PROXY', None)
                    old_https_proxy = os.environ.pop('HTTPS_PROXY', None)
                    os.environ.pop('http_proxy', None)
                    os.environ.pop('https_proxy', None)
                # else: global real CDP URL exists — keep it, don't overwrite
            elif _existing_cdp == CDP_URL_SENTINEL:
                # SessionDisplay gone but sentinel lingers from a prior run
                del os.environ['BROWSER_CDP_URL']
            if _profile_home:
                os.environ['HERMES_HOME'] = _profile_home
        # Lock released — agent runs without holding it
        # Register a gateway-style notify callback so the approval system can
        # push the `approval` SSE event the moment a dangerous command is
        # detected, without waiting for the next on_tool() poll cycle.
        # Without this, the agent thread blocks inside the terminal tool
        # waiting for approval that the UI never knew to ask for, leaving
        # the chat stuck in "Thinking…" forever.
        _approval_registered = False
        _unreg_notify = None
        try:
            from tools.approval import (
                register_gateway_notify as _reg_notify,
                unregister_gateway_notify as _unreg_notify,
            )
            def _approval_notify_cb(approval_data):
                put('approval', approval_data)
            _reg_notify(session_id, _approval_notify_cb)
            _approval_registered = True
        except ImportError:
            logger.debug("Approval module not available, falling back to polling")

        _clarify_registered = False
        _unreg_clarify_notify = None
        try:
            from api.clarify import (
                register_gateway_notify as _reg_clarify_notify,
                unregister_gateway_notify as _unreg_clarify_notify,
            )

            def _clarify_notify_cb(clarify_data):
                put('clarify', clarify_data)

            _reg_clarify_notify(session_id, _clarify_notify_cb)
            _clarify_registered = True
        except ImportError:
            logger.debug("Clarify module not available, falling back to polling")

        def _clarify_callback_impl(question, choices, sid, cancel_evt, put_event):
            """Bridge Hermes clarify prompts to the WebUI."""
            timeout = 120
            choices_list = [str(choice) for choice in (choices or [])]
            data = {
                'question': str(question or ''),
                'choices_offered': choices_list,
                'session_id': sid,
                'kind': 'clarify',
                'clarify_id': uuid.uuid4().hex,
                'requested_at': time.time(),
            }
            try:
                from api.clarify import submit_pending as _submit_clarify_pending, clear_pending as _clear_clarify_pending
            except ImportError:
                return (
                    "The user did not provide a response within the time limit. "
                    "Use your best judgement to make the choice and proceed."
                )

            entry = _submit_clarify_pending(sid, data)
            deadline = time.monotonic() + timeout
            while True:
                if cancel_evt.is_set():
                    _clear_clarify_pending(sid)
                    return (
                        "The user did not provide a response within the time limit. "
                        "Use your best judgement to make the choice and proceed."
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _clear_clarify_pending(sid)
                    return (
                        "The user did not provide a response within the time limit. "
                        "Use your best judgement to make the choice and proceed."
                    )
                if entry.event.wait(timeout=min(1.0, remaining)):
                    response = str(entry.result or "").strip()
                    return (
                        response
                        or "The user did not provide a response within the time limit. "
                           "Use your best judgement to make the choice and proceed."
                    )

        try:
            _token_sent = False  # tracks whether any streamed tokens were sent
            _reasoning_text = ''  # accumulates reasoning/thinking trace for persistence
            _live_tool_calls = []  # tool progress fallback when final messages omit tool IDs
            _message_started = False  # tracks whether current assistant message row has been started
            _all_message_ts = []  # [user_msg_ts?, msg_start_ts1, msg_start_ts2, ...]

            # ── Turn-boundary tracking for multi-tool sub-turn detection ──────
            # When the model produces multiple tool_use blocks in a single
            # assistant response, the SDK calls tool_progress_callback for each
            # tool sequentially.  We must emit only ONE message_start per
            # assistant turn so the frontend doesn't create spurious extra
            # assistant rows (which then mismatch on reload).
            #
            # Strategy:
            #   stream_delta_callback(None) fires once per assistant turn
            #   (after model output, before tool execution) for ALL turns
            #   with tool calls — including tool-only turns where content
            #   is empty.  on_token(None) handles this and increments
            #   _turn_id.  This is more reliable than _thinking (which
            #   only fires when assistant_message.content is non-empty).
            #
            #   on_token (real tokens) uses _on_token_turn_id != _turn_id
            #   to detect "first token of a new turn".
            #
            #   tool.started uses _tool_batch_id to ensure only the first
            #   tool in a batch emits message_start.
            _turn_id = 0              # incremented by on_token(None)
            _on_token_turn_id = -1    # _turn_id when on_token last emitted
            _text_emitted_this_turn = False
            _tool_batch_id = -1       # _turn_id of last tool.started batch

            # ── Seed with user message timestamp (if available) ────────────
            # _pending_user_message_ts is set by _handle_chat_start and
            # surfaces the time when the user submitted the message.
            _user_ts = getattr(s, '_pending_user_message_ts', None)
            if _user_ts:
                _all_message_ts.append(_user_ts)
                # Keep _pending_user_message_ts on the session — reconnect
                # during streaming needs it via /api/session.  It will be
                # overwritten by the next _handle_chat_start call.

            # Throttle: emit metering events at most every 100 ms so the TPS label
            # feels live during fast token streams without flooding the SSE channel.
            _metering_last_emit = [time.monotonic() - 1]  # fire immediately on first token

            def _append_reasoning_text(text):
                nonlocal _reasoning_text
                _reasoning_text += str(text)

            def _emit_metering():
                now = time.monotonic()
                if now - _metering_last_emit[0] < 0.1:
                    return
                _metering_last_emit[0] = now
                stats = meter().get_stats()
                stats['session_id'] = stream_id
                put('metering', stats)

            def on_token(text):
                try:
                    nonlocal _token_sent, _message_started, _all_message_ts
                    nonlocal _text_emitted_this_turn, _on_token_turn_id
                    if text is None:
                        # ── End-of-stream sentinel ────────────────────
                        # The SDK calls stream_delta_callback(None) once
                        # per assistant turn, AFTER the model response
                        # and BEFORE tool execution, for ALL turns with
                        # tool calls — including tool-only turns where
                        # no real tokens were emitted.  This is the most
                        # reliable turn-boundary signal.
                        nonlocal _turn_id, _tool_batch_id
                        _turn_id += 1
                        if not _text_emitted_this_turn:
                            # Tool-only turn: no token/reasoning was
                            # emitted → reset so the first tool.started
                            # emits message_start.
                            _message_started = False
                        _text_emitted_this_turn = False  # reset for tool batch
                        return
                    # First token of a new turn: _on_token_turn_id
                    # hasn't caught up with _turn_id yet.
                    if _on_token_turn_id != _turn_id:
                        _on_token_turn_id = _turn_id
                        _message_started = False
                    if not _message_started:
                        _message_started = True
                        _text_emitted_this_turn = True
                        _ts = time.time()
                        _all_message_ts.append(_ts)
                        put('message_start', {'ts': _ts})
                    _token_sent = True
                    # Accumulate partial text so cancel_stream() can persist it (#893)
                    if stream_id in STREAM_PARTIAL_TEXT:
                        STREAM_PARTIAL_TEXT[stream_id] += str(text)
                    put('token', {'text': text})
                    # Update global throughput meter
                    meter().record_token(stream_id, len(STREAM_PARTIAL_TEXT[stream_id]))
                    _emit_metering()
                except Exception:
                    logger.warning(
                        "on_token callback failed for session=%s",
                        session_id, exc_info=True,
                    )

            # For streaming chat, invoked when reasoning token is arrived.
            def on_reasoning(text):
                try:
                    nonlocal _message_started, _all_message_ts
                    nonlocal _text_emitted_this_turn, _on_token_turn_id
                    if text is None:
                        return
                    # reasoning=none: SDK final response omits reasoning_content,
                    # but DeepSeek still emits reasoning_content delta chunks that
                    # mirror the assistant text.  Suppress them so no spurious
                    # reasoning is pushed via SSE or persisted to the session.
                    if _reasoning_config and _reasoning_config.get("enabled") is False:
                        return
                    # First reasoning token of a new turn — same logic as on_token.
                    if _on_token_turn_id != _turn_id:
                        _on_token_turn_id = _turn_id
                        _message_started = False
                    if not _message_started:
                        _message_started = True
                        _text_emitted_this_turn = True
                        _ts = time.time()
                        _all_message_ts.append(_ts)
                        put('message_start', {'ts': _ts})
                    _append_reasoning_text(text)
                    put('reasoning', {'text': str(text)})
                    # Track reasoning tokens in the meter so TPS reflects all AI output
                    meter().record_reasoning(stream_id, len(_reasoning_text))
                    _emit_metering()
                except Exception:
                    logger.warning(
                        "on_reasoning callback failed for session=%s",
                        session_id, exc_info=True,
                    )

            # Pre-initialise the activity counter here so on_tool (which
            # closes over it) never captures an unbound name even if this
            # block is reordered later (Issue #765).
            _checkpoint_activity = [0]

            def on_tool(*cb_args, **cb_kwargs):
                nonlocal _message_started, _all_message_ts
                nonlocal _turn_id, _text_emitted_this_turn, _tool_batch_id
                event_type = None
                name = None
                preview = None
                args = None

                if len(cb_args) >= 4:
                    event_type, name, preview, args = cb_args[:4]
                elif len(cb_args) == 3:
                    name, preview, args = cb_args
                    event_type = 'tool.started'
                elif len(cb_args) == 2:
                    event_type, name = cb_args
                elif len(cb_args) == 1:
                    name = cb_args[0]
                    event_type = 'tool.started'

                # When streaming chat is finished, hermes agent would extract total reasoning text (at most 500 charactors) and push them here.
                # The content of on_tool/on_token/on_reasoning maybe same.
                if event_type in ('reasoning.available', '_thinking'):
                    reason_text = preview if event_type == 'reasoning.available' else name
                    # _thinking fires for text-producing turns (content
                    # non-empty).  Turn-boundary tracking is now handled
                    # by on_token(None) which fires unconditionally for
                    # ALL turns with tool calls.
                    if reason_text and not (_reasoning_config and _reasoning_config.get("enabled") is False):
                        # Insert separator at cross-source boundary (streaming → post-hoc summary)
                        if _reasoning_text:
                            _reasoning_text += f"\n{_REASONING_SEP_MARK}\n"
                        _append_reasoning_text(reason_text)
                        put('reasoning', {'text': str(reason_text)})
                        meter().record_reasoning(stream_id, len(_reasoning_text))
                        _emit_metering()
                    return

                # ── Deferred end_stream for subagent completion ──
                # When a subagent completes, the child agent may still be writing
                # final messages to state.db. We defer end_stream until ended_at
                # is set (meaning the agent's close() has run and all messages are
                # persisted). This prevents the frontend from loading incomplete data.
                def _deferred_end_stream(child_session_id: str):
                    """Poll state.db until the child session's ended_at is set, then close the stream."""
                    import sqlite3 as _sqlite3
                    from pathlib import Path as _Path

                    def _close_child_stream():
                        child_session = _get_or_stub_session(child_session_id)
                        # If placeholder, try to load real session from DB and update in-place
                        if getattr(child_session, '_is_placeholder', False):
                            try:
                                from api.models import get_session as _get_sess
                                real_session = _get_sess(child_session_id)
                                if isinstance(real_session, _Session):
                                    child_session.copy_from(real_session)
                                    child_session._is_placeholder = False
                            except (KeyError, Exception):
                                pass  # keep placeholder if DB load fails
                        stream_manager.end_stream(child_session)

                    def _poll():
                        try:
                            from api.profiles import get_active_hermes_home
                            _hermes_home = _Path(get_active_hermes_home()).expanduser().resolve()
                        except Exception:
                            _hermes_home = _Path(os.environ.get("HERMES_HOME", str(_Path.home() / ".hermes"))).expanduser().resolve()
                        _db_path = _hermes_home / "state.db"
                        if not _db_path.exists():
                            # No state.db — fall back to immediate close
                            _close_child_stream()
                            return

                        logger.info("[deferred_end_stream] Polling started for child %s", child_session_id[:12])
                        for _i in range(60):  # max 60s (60 * 1s)
                            time.sleep(1)
                            try:
                                conn = _sqlite3.connect(str(_db_path), timeout=5)
                                row = conn.execute(
                                    "SELECT ended_at FROM sessions WHERE id = ?",
                                    (child_session_id,)
                                ).fetchone()
                                conn.close()
                                if row and row[0] is not None:
                                    # ended_at is set — agent has finished, messages are persisted.
                                    # end_stream triggers the cache eviction callback registered
                                    # in models.py (_setup_stream_cache_invalidation).
                                    logger.info("[deferred_end_stream] ended_at detected for child %s after %ds, closing stream", child_session_id[:12], _i + 1)
                                    _close_child_stream()
                                    return
                            except Exception:
                                pass  # retry on next tick

                        # Timeout — force close anyway to avoid leaking streams
                        logger.warning("[deferred_end_stream] Timeout for child %s after 60s", child_session_id[:12])
                        _close_child_stream()

                    threading.Thread(target=_poll, daemon=True).start()

                # ── Subagent events: route to child agent's independent stream ──
                # The delegate_tool relays child agent events to the parent's
                # tool_progress_callback. We create an independent ReplayableStream
                # for each child agent so the frontend can subscribe directly.
                # Track active tool per child stream so we can emit tool_complete
                # when a new tool starts or the child finishes (hermes-agent SDK
                # only sends subagent.tool, not subagent.tool_complete).
                if not hasattr(_run_agent_streaming, '_active_child_tools'):
                    _run_agent_streaming._active_child_tools = {}
                _active_child_tools = _run_agent_streaming._active_child_tools

                if event_type and event_type.startswith('subagent.'):
                    args_snap_subagent = {}
                    if isinstance(args, dict):
                        for k, v in list(args.items())[:4]:
                            s2 = str(v)
                            args_snap_subagent[k] = s2[:120] + ('...' if len(s2) > 120 else '')
                    # Log subagent events for debugging
                    child_sid = cb_kwargs.get('child_session_id', 'unknown')
                    print(f'[webui] subagent event: type={event_type} child={child_sid} session={session_id}', flush=True)

                    # ── Create child stream on subagent.start ──
                    if event_type == 'subagent.start':
                        from api.models import Session as _Session, LOCK as _MODELS_LOCK, SESSIONS as _SESSIONS, _notify_session_event as _notify
                        goal = cb_kwargs.get('goal', '')

                        # Try to load real session from state.db (get_session
                        # triggers upsert_session notify if found).
                        _child_session = _get_or_stub_session(child_sid)

                        # If not in state.db yet, create a placeholder so the
                        # frontend can show the subagent in the sidebar immediately.
                        if not isinstance(_child_session, _Session):
                            _child_session = _Session(
                                session_id=child_sid,
                                title=goal[:80] or 'Subagent Task',
                                parent_session_id=session_id,
                            )
                            _child_session._is_placeholder = True
                            _child_session.source_tag = 'subagent'
                            _child_session.is_subagent_session = True
                            # Seed user message so the frontend can load it
                            # via HTTP when begin_stream triggers startSessionStream.
                            _child_session.messages = [{
                                'role': 'user',
                                'content': goal,
                                'timestamp': time.time(),
                            }]
                            with _MODELS_LOCK:
                                _SESSIONS[child_sid] = _child_session
                                _SESSIONS.move_to_end(child_sid)
                            _notify('upsert_session', _child_session)

                        # Create stream (triggers begin_stream notify)
                        stream_manager.begin_stream(_child_session)

                        # Update parent session's delegate children count
                        # AFTER begin_stream so the child's stream is active
                        with _MODELS_LOCK:
                            _parent = _SESSIONS.get(session_id)
                        if _parent:
                            # Count delegate children for this parent
                            _delegate_count = sum(
                                1 for s in _SESSIONS.values()
                                if getattr(s, 'parent_session_id', None) == session_id
                                and getattr(s, 'is_subagent_session', False)
                            )
                            # Count running children (those with active streams)
                            _active_ids = set(stream_manager.get_active_session_ids())
                            _running_count = sum(
                                1 for s in _SESSIONS.values()
                                if getattr(s, 'parent_session_id', None) == session_id
                                and getattr(s, 'is_subagent_session', False)
                                and s.session_id in _active_ids
                            )
                            _parent._delegate_children_count = _delegate_count
                            _parent._has_delegate_children = _delegate_count > 0
                            _parent._delegate_running_count = _running_count
                            _notify('upsert_session', _parent)

                        # Notify parent stream (for UI: subagent_start event)
                        put('subagent_start', {
                            'child_session_id': child_sid,
                            'subagent_id': cb_kwargs.get('subagent_id'),
                            'task_index': cb_kwargs.get('task_index'),
                            'goal': goal,
                        })

                    # ── Route event to child's stream ──
                    child_stream = stream_manager.get_stream(child_sid)
                    if child_stream:
                        def _finish_active_child_tool():
                            """Emit tool_complete for the currently active child tool, if any."""
                            active = _active_child_tools.pop(child_sid, None)
                            if active:
                                duration = time.time() - active['start_ts']
                                child_stream.put('tool_complete', {
                                    'event_type': 'tool.completed',
                                    'name': active['name'],
                                    'tid': active['tid'],
                                    'output': '',
                                    'preview': '',
                                    'duration': duration,
                                    'is_error': False,
                                })

                        # Map subagent.* events to standard event types for child stream
                        if event_type == 'subagent.text':
                            _finish_active_child_tool()
                            child_stream.put('token', {'text': preview or ''})
                        elif event_type == 'subagent.thinking':
                            child_stream.put('reasoning', {'text': preview or ''})
                        elif event_type == 'subagent.tool':
                            # Finish previous tool before starting a new one
                            _finish_active_child_tool()
                            tid = f"tool-{name}-{int(time.time() * 1000)}"
                            _active_child_tools[child_sid] = {
                                'tid': tid, 'name': name, 'start_ts': time.time(),
                            }
                            child_stream.put('message_start', {'ts': time.time()})
                            child_stream.put('tool', {
                                'event_type': 'tool.started',
                                'name': name,
                                'tid': tid,
                                'preview': preview or '',
                                'args': args_snap_subagent,
                                'ts': time.time(),
                            })
                        elif event_type == 'subagent.start':
                            child_stream.put('message_start', {'ts': time.time()})
                        elif event_type == 'subagent.complete':
                            _finish_active_child_tool()
                            child_stream.put('done', {
                                'status': cb_kwargs.get('status', 'completed'),
                            })
                            # Defer end_stream until the child agent has finished
                            # writing all messages to state.db. The agent's close()
                            # method sets ended_at after the final message is saved.
                            # We poll state.db in a background thread to detect this.
                            _deferred_end_stream(child_sid)
                        else:
                            # Forward other subagent events as-is
                            child_stream.put('subagent', {
                                'event_type': event_type,
                                'subagent_id': cb_kwargs.get('subagent_id'),
                                'child_session_id': child_sid,
                                'task_index': cb_kwargs.get('task_index'),
                                'goal': cb_kwargs.get('goal'),
                                'preview': preview or name or '',
                                'status': cb_kwargs.get('status'),
                                'tool_name': name,
                                'args': args_snap_subagent,
                                'model': cb_kwargs.get('model'),
                                'depth': cb_kwargs.get('depth'),
                                'tool_count': cb_kwargs.get('tool_count'),
                            })

                    return

                args_snap = {}
                if isinstance(args, dict):
                    for k, v in list(args.items())[:4]:
                        s2 = str(v)
                        args_snap[k] = s2[:120] + ('...' if len(s2) > 120 else '')

                if event_type in (None, 'tool.started'):
                    # ── Unified message_start: every sub-turn (text or tool)
                    #     emits message_start before the first event so the
                    #     frontend always has an assistant row to anchor to.
                    #
                    #     Turn-boundary (including tool-only detection) is
                    #     handled by on_token(None).  Here we only need
                    #     same-batch dedup via _tool_batch_id.
                    #
                    #     Each tool message now carries its own independent
                    #     timestamp (ts field in the tool SSE event) so that
                    #     tool messages get distinct timestamp__webui values
                    #     instead of inheriting the assistant's timestamp.
                    if _tool_batch_id != _turn_id:
                        _tool_batch_id = _turn_id
                    if not _message_started:
                        _message_started = True
                        _ts = time.time()
                        _all_message_ts.append(_ts)
                        put('message_start', {'ts': _ts})
                    # Generate a unique tid for this tool call
                    tid = f"tool-{name}-{int(time.time() * 1000)}-{len(_live_tool_calls)}"
                    _live_tool_calls.append({
                        'name': name,
                        'tid': tid,  # Store tid for later matching in tool.completed
                        'args': args if isinstance(args, dict) else {},
                    })
                    # ── Tool message's own timestamp ──────────────────
                    # Collect a per-message timestamp so each tool message
                    # gets a distinct timestamp__webui instead of sharing
                    # the assistant's.  Appended AFTER any message_start ts
                    # so _all_message_ts order matches s.messages order
                    # (assistant msg before tool msg).
                    _tool_ts = time.time()
                    _all_message_ts.append(_tool_ts)
                    put('tool', {
                        'event_type': event_type or 'tool.started',
                        'name': name,
                        'tid': tid,  # Include tid in the event for frontend
                        'preview': preview,
                        'args': args_snap,
                        'ts': _tool_ts,  # tool message's own timestamp for frontend
                    })
                    # ── CLI tool visualization (non-blocking, try/except) ──
                    try:
                        from api.cli_viewer import on_tool_started
                        on_tool_started(session_id, name, args if isinstance(args, dict) else {}, tid)
                    except Exception:
                        pass
                    # Fallback: poll for pending approval in case notify_cb wasn't
                    # registered (e.g. older approval module without gateway support).
                    try:
                        from tools.approval import has_pending as _has_pending, _pending, _lock
                        if _has_pending(session_id):
                            with _lock:
                                p = dict(_pending.get(session_id, {}))
                            if p:
                                put('approval', p)
                    except ImportError:
                        pass
                    return

                if event_type == 'tool.completed':
                    try:
                        tid_for_event = ''  # Initialize tid
                        _todos_for_event = None  # Full todos list for write_todo tool
                        for live_tc in reversed(_live_tool_calls):
                            if live_tc.get('done'):
                                continue
                            if not name or live_tc.get('name') == name:
                                live_tc['done'] = True
                                live_tc['duration'] = cb_kwargs.get('duration')
                                live_tc['is_error'] = bool(cb_kwargs.get('is_error', False))
                                # Extract tid to include in the event for frontend tracking
                                tid_for_event = live_tc.get('tid', '')
                                # For todo (write_todo): extract full (untruncated) todos list from args
                                if name in ('write_todo', 'todo'):
                                    _tc_args = live_tc.get('args', {})
                                    if isinstance(_tc_args, dict) and isinstance(_tc_args.get('todos'), list):
                                        _todos_for_event = _tc_args['todos']
                                break
                        # Signal the checkpoint thread that new work has completed (Issue #765).
                        # Each completed tool call is a meaningful unit of progress worth persisting.
                        _checkpoint_activity[0] += 1
                        _tool_complete_payload = {
                            'event_type': event_type,
                            'name': name,
                            'tid': tid_for_event,  # Include tid for frontend to match tool message
                            'output': preview,  # Tool result output from preview parameter
                            'preview': preview,
                            'args': args_snap,
                            'duration': cb_kwargs.get('duration'),
                            'is_error': bool(cb_kwargs.get('is_error', False)),
                        }
                        if _todos_for_event is not None:
                            _tool_complete_payload['todos'] = _todos_for_event
                        put('tool_complete', _tool_complete_payload)
                        # ── CLI tool visualization (non-blocking, try/except) ──
                        try:
                            from api.cli_viewer import on_tool_completed
                            on_tool_completed(
                                session_id, name, tid_for_event, preview,
                                cb_kwargs.get('duration'), bool(cb_kwargs.get('is_error', False)),
                            )
                        except Exception:
                            pass
                        # message_started is no longer reset here — the turn-boundary
                        # tracking (_turn_id counter) handles multi-tool sub-turn
                        # detection without creating spurious assistant rows.
                    except Exception:
                        logger.warning(
                            "on_tool tool_completed callback failed for session=%s name=%s",
                            session_id, name, exc_info=True,
                        )
                    return

            _AIAgent = _get_ai_agent()
            if _AIAgent is None:
                raise ImportError("AIAgent not available -- check that hermes-agent is on sys.path")
        
            # Initialize SessionDB so session_search works in WebUI sessions
            _session_db = None
            try:
                from hermes_state import SessionDB
                _session_db = SessionDB()
            except Exception as _db_err:
                print(f"[webui] WARNING: SessionDB init failed — session_search will be unavailable: {_db_err}", flush=True)
            resolved_model, resolved_provider, resolved_base_url = resolve_model_provider(model)

            # Resolve API key via Hermes runtime provider (matches gateway behaviour).
            # Pass the resolved provider so non-default providers get their own credentials.
            resolved_api_key = None
            try:
                from hermes_cli.runtime_provider import resolve_runtime_provider
                _rt = resolve_runtime_provider(requested=resolved_provider)
                resolved_api_key = _rt.get("api_key")
                if not resolved_provider:
                    resolved_provider = _rt.get("provider")
                if not resolved_base_url:
                    resolved_base_url = _rt.get("base_url")
            except Exception as _e:
                print(f"[webui] WARNING: resolve_runtime_provider failed: {_e}", flush=True)

            # Read per-profile config at call time (not module-level snapshot)
            from api.config import get_config as _get_config
            _cfg = _get_config()

            # Per-profile toolsets — use _resolve_cli_toolsets() so MCP
            # server toolsets are included, matching native CLI behaviour.
            from api.config import _resolve_cli_toolsets
            _toolsets = _resolve_cli_toolsets(_cfg)

            # Fallback model from profile config (e.g. for rate-limit recovery)
            _fallback = _cfg.get('fallback_model') or None
            if _fallback:
                # Resolve the fallback through our provider logic too
                fb_model = _fallback.get('model', '')
                fb_provider = _fallback.get('provider', '')
                fb_base_url = _fallback.get('base_url')
                _fallback_resolved = {
                    'model': fb_model,
                    'provider': fb_provider,
                    'base_url': fb_base_url,
                }
            else:
                _fallback_resolved = None

            # Build kwargs defensively — guard newer params so the WebUI
            # degrades gracefully when run against an older hermes-agent build.
            # (fixes: TypeError: AIAgent.__init__() got an unexpected keyword
            # argument 'credential_pool' — issue #772)
            import inspect as _inspect
            _agent_params = set(_inspect.signature(_AIAgent.__init__).parameters)

            # Per-session reasoning effort: read from session first (new behaviour),
            # fall back to global config.yaml for backward compatibility.
            # When both are absent, pass None → agent uses its default.
            try:
                from api.config import parse_reasoning_effort as _parse_reff
                _effort_raw = getattr(s, 'reasoning_effort', None)
                if _effort_raw is None:
                    # Fall back to global config.yaml (CLI-parity / old sessions)
                    _effort_cfg = _cfg.cfg.get('agent', {}) if isinstance(_cfg.cfg, dict) else {}
                    _effort_raw = _effort_cfg.get('reasoning_effort') if isinstance(_effort_cfg, dict) else None
                _reasoning_config = _parse_reff(_effort_raw)
            except Exception:
                _reasoning_config = None

            # Per-session service tier (fast mode): read from session,
            # resolve provider-appropriate request_overrides.
            _service_tier = None
            _request_overrides = None
            try:
                _service_tier = getattr(s, 'service_tier', None) or None
                if _service_tier == "priority":
                    from hermes_cli.models import resolve_fast_mode_overrides
                    _request_overrides = resolve_fast_mode_overrides(resolved_model)
            except Exception:
                _service_tier = None
                _request_overrides = None

            _agent_kwargs = dict(
                model=resolved_model,
                provider=resolved_provider,
                base_url=resolved_base_url,
                api_key=resolved_api_key,
                # Identify browser-originated sessions as WebUI so Hermes Agent
                # does not inject CLI-specific terminal/output guidance.
                platform='webui',
                quiet_mode=True,
                enabled_toolsets=_toolsets,
                fallback_model=_fallback_resolved,
                session_id=session_id,
                session_db=_session_db,
                stream_delta_callback=on_token,
                reasoning_callback=on_reasoning,
                tool_progress_callback=on_tool,
                clarify_callback=(
                    lambda question, choices: _clarify_callback_impl(
                        question, choices, session_id, cancel_event, put
                    )
                ),
            )
            # reasoning_config has been an AIAgent param for several releases,
            # but guard defensively to avoid TypeError on an older agent build.
            if 'reasoning_config' in _agent_params and _reasoning_config is not None:
                _agent_kwargs['reasoning_config'] = _reasoning_config
            # Params added in newer hermes-agent — skip if not supported
            if 'api_mode' in _agent_params:
                _agent_kwargs['api_mode'] = _rt.get('api_mode')
            if 'acp_command' in _agent_params:
                _agent_kwargs['acp_command'] = _rt.get('command')
            if 'acp_args' in _agent_params:
                _agent_kwargs['acp_args'] = _rt.get('args')
            if 'credential_pool' in _agent_params:
                _agent_kwargs['credential_pool'] = _rt.get('credential_pool')
            if 'service_tier' in _agent_params and _service_tier:
                _agent_kwargs['service_tier'] = _service_tier
            if 'request_overrides' in _agent_params and _request_overrides:
                _agent_kwargs['request_overrides'] = _request_overrides
            # Pin Honcho memory sessions to the stable WebUI session ID.
            # Without this, 'per-session' Honcho strategy creates a new Honcho
            # session on every streaming request because HonchoSessionManager is
            # re-instantiated fresh each turn (#855).
            # Use the proper session_key format for gateway routing so async
            # delegation completion events can be delivered back to the session.
            if 'gateway_session_key' in _agent_params:
                _agent_kwargs['gateway_session_key'] = f"agent:main:webui:dm:{session_id}"

            # ── Filesystem checkpoint support ──
            # Read checkpoints config from hermes config.yaml; defaults to disabled.
            _cp_cfg = (_cfg or {}).get('checkpoints', {}) if isinstance(_cfg, dict) else {}
            if 'checkpoints_enabled' in _agent_params:
                _agent_kwargs['checkpoints_enabled'] = _cp_cfg.get('enabled', False)
            if 'checkpoint_max_snapshots' in _agent_params:
                _agent_kwargs['checkpoint_max_snapshots'] = int(_cp_cfg.get('max_snapshots', 20))
            if 'checkpoint_max_total_size_mb' in _agent_params:
                _agent_kwargs['checkpoint_max_total_size_mb'] = int(_cp_cfg.get('max_total_size_mb', 500))
            if 'checkpoint_max_file_size_mb' in _agent_params:
                _agent_kwargs['checkpoint_max_file_size_mb'] = int(_cp_cfg.get('max_file_size_mb', 10))

            # ── Agent cache: reuse across messages in the same session ──
            # Mirrors gateway _agent_cache.  Keeps _user_turn_count alive so
            # injectionFrequency: "first-turn" actually suppresses after turn 1.
            if ephemeral:
                agent = _AIAgent(**_agent_kwargs)
                agent.compression_enabled = False  # WebUI manages compression
                logger.debug('[webui] Created ephemeral agent for session %s', session_id)
            else:
                import hashlib as _hashlib
                import json as _json
                from api.config import SESSION_AGENT_CACHE, SESSION_AGENT_CACHE_LOCK
                _sig_blob = _json.dumps([
                    resolved_model or '',
                    _hashlib.sha256((resolved_api_key or '').encode()).hexdigest()[:16],
                    resolved_base_url or '',
                    resolved_provider or '',
                    sorted(_toolsets) if _toolsets else [],
                    sorted(getattr(s, 'skills', None) or []),
                    _reasoning_config,
                    _service_tier,
                    _request_overrides,
                ], sort_keys=True)
                _agent_sig = _hashlib.sha256(_sig_blob.encode()).hexdigest()[:16]

                agent = None
                with SESSION_AGENT_CACHE_LOCK:
                    _cached = SESSION_AGENT_CACHE.get(session_id)
                    if _cached and _cached[1] == _agent_sig:
                        agent = _cached[0]
                        logger.debug('[webui] Reusing cached agent for session %s', session_id)

                if agent is not None:
                    # Refresh per-turn callbacks — these close over request-scoped
                    # objects (put queue, cancel_event) that are new each request.
                    agent.stream_delta_callback = _agent_kwargs.get('stream_delta_callback')
                    agent.tool_progress_callback = _agent_kwargs.get('tool_progress_callback')
                    if hasattr(agent, 'reasoning_callback'):
                        agent.reasoning_callback = _agent_kwargs.get('reasoning_callback')
                    if hasattr(agent, 'clarify_callback'):
                        agent.clarify_callback = _agent_kwargs.get('clarify_callback')
                    # Refresh reasoning_config — may have changed per-session between turns
                    if hasattr(agent, 'reasoning_config') and 'reasoning_config' in _agent_kwargs:
                        agent.reasoning_config = _agent_kwargs['reasoning_config']
                    # Refresh service_tier / request_overrides — may have changed via /fast
                    if hasattr(agent, 'service_tier'):
                        agent.service_tier = _service_tier
                    if hasattr(agent, 'request_overrides'):
                        agent.request_overrides = dict(_request_overrides or {})
                    if _session_db is not None:
                        agent._session_db = _session_db
                    if hasattr(agent, '_api_call_count'):
                        agent._api_call_count = 0
                    # Reset interrupt state from a prior cancel so the reused
                    # agent does not think it is still interrupted.
                    if hasattr(agent, '_interrupted'):
                        agent._interrupted = False
                    if hasattr(agent, '_interrupt_message'):
                        agent._interrupt_message = None
                    agent.compression_enabled = False  # WebUI manages compression
                else:
                    agent = _AIAgent(**_agent_kwargs)
                    agent.compression_enabled = False  # WebUI manages compression
                    with SESSION_AGENT_CACHE_LOCK:
                        SESSION_AGENT_CACHE[session_id] = (agent, _agent_sig)
                    logger.debug('[webui] Created new agent for session %s', session_id)

            # Store agent instance for cancel/interrupt propagation
            with STREAMS_LOCK:
                AGENT_INSTANCES[stream_id] = agent
                # If cancel was requested during agent init, interrupt now
                # so _interrupt_requested is set.  The agent proceeds to
                # run_conversation where the main loop detects the flag and
                # breaks — this guarantees s.messages is updated with the
                # user message before the agent thread exits.
                if stream_id in CANCEL_FLAGS and CANCEL_FLAGS[stream_id].is_set():
                    try:
                        agent.interrupt("Cancelled before start")
                    except Exception:
                        pass

            # Prepend workspace context so the agent always knows which directory
            # to use for file operations, regardless of session age or AGENTS.md defaults.
            workspace_ctx = f"[Workspace: {s.workspace}]\n"
            workspace_system_msg = (
                f"Active workspace at session start: {s.workspace}\n"
                "Every user message is prefixed with [Workspace: /absolute/path] indicating the "
                "workspace the user has selected in the web UI at the time they sent that message. "
                "This tag is the single authoritative source of the active workspace and updates "
                "with every message. It overrides any prior workspace mentioned in this system "
                "prompt, memory, or conversation history. Always use the value from the most recent "
                "[Workspace: ...] tag as your default working directory for ALL file operations: "
                "write_file, read_file, search_files, terminal workdir, and patch. "
                "Never fall back to a hardcoded path when this tag is present."
            )
            # Resolve personality prompt from config.yaml agent.personalities
            # (matches hermes-agent CLI behavior — passes via ephemeral_system_prompt)
            _pname = getattr(s, 'personality', None)
            _personality_prompt = _resolve_personality_prompt(_cfg, _pname)
            agent.ephemeral_system_prompt = _personality_prompt
            if _personality_prompt:
                print(
                    f'[webui] session={session_id} personality={_pname} '
                    f'ephemeral_system_prompt={_personality_prompt!r}',
                    flush=True,
                )
            else:
                print(
                    f'[webui] session={session_id} personality=None (default)',
                    flush=True,
                )

            # ── Per-session skill binding: replace <available_skills> block ──
            # When a session has a skills list configured (not None), replace the
            # <available_skills> block in _cached_system_prompt so only the bound
            # skills appear in the model's index.  None = not configured (use
            # full global index); [] = explicitly empty (remove block entirely).
            _session_skills = getattr(s, 'skills', None)
            if _session_skills is not None:
                import re as _re_skills
                # Ensure _cached_system_prompt is built before we modify it
                if agent._cached_system_prompt is None:
                    try:
                        agent._cached_system_prompt = agent._build_system_prompt(workspace_system_msg)
                    except Exception as _sp_err:
                        logger.debug('[webui] _build_system_prompt failed for skill binding: %s', _sp_err)
                if agent._cached_system_prompt:
                    _skills_block_content = _build_session_skills_block(_session_skills)
                    if _skills_block_content:
                        _new_block = f'<available_skills>\n{_skills_block_content}\n</available_skills>'
                        _replaced, _n = _re_skills.subn(
                            r'<available_skills>.*?</available_skills>',
                            _new_block,
                            agent._cached_system_prompt,
                            flags=_re_skills.DOTALL,
                        )
                        if _n > 0:
                            agent._cached_system_prompt = _replaced
                            logger.debug(
                                '[webui] Replaced available_skills for session %s: %s',
                                session_id, _session_skills,
                            )
                    else:
                        # Empty skill list — remove the entire <available_skills> block
                        agent._cached_system_prompt = _re_skills.sub(
                            r'\n*<available_skills>.*?</available_skills>\n*',
                            '\n',
                            agent._cached_system_prompt,
                            flags=_re_skills.DOTALL,
                        )
                        logger.debug(
                            '[webui] Removed available_skills block for session %s (empty skill list)',
                            session_id,
                        )
                    # Write back to SQLite so the filtered version is restored on restart
                    if _session_db is not None:
                        try:
                            _session_db.update_system_prompt(session_id, agent._cached_system_prompt)
                        except Exception as _sdb_err:
                            logger.debug(
                                '[webui] update_system_prompt for skill binding failed: %s', _sdb_err
                            )

            _previous_messages = list(s.messages or [])

            # ── Periodic checkpoint during streaming (Issue #765) ──
            # The agent works on an internal copy of s.messages during run_conversation()
            # so we cannot watch s.messages for growth. Instead, on_tool() increments
            # _checkpoint_activity[0] each time a tool call completes — that is the real
            # signal that progress has been made worth persisting.
            #
            # What gets saved on each checkpoint:
            #   - s.pending_user_message (already written before run starts)
            #   - s.pending_started_at / s.active_stream_id (turn bookkeeping)
            # On a server restart the UI will see a session with a pending message and no
            # response — better than a silent loss of the entire conversation turn.
            # The final s.sync_webui_metadata() at task completion handles the full session update + index.
            # (_checkpoint_stop is pre-initialised at the top of the outer try.)
            # (_checkpoint_activity is already initialised before on_tool().)

            def _periodic_checkpoint():
                last_saved_activity = 0
                while not _checkpoint_stop.wait(15):
                    try:
                        cur = _checkpoint_activity[0]
                        if cur > last_saved_activity:
                            with _agent_lock:
                                s.sync_webui_metadata(skip_index=True)
                            last_saved_activity = cur
                    except Exception as e:
                        logger.debug("Periodic checkpoint save failed: %s", e)

            _checkpoint_stop = threading.Event()
            # Persist the user message BEFORE streaming starts so it's durable even if
            # the server crashes before the first checkpoint fires (every 15s).
            with _agent_lock:
                s.sync_webui_metadata(touch_updated_at=True, skip_index=False)

            _ckpt_thread = threading.Thread(
                target=_periodic_checkpoint, daemon=True,
                name=f"ckpt-{session_id[:8]}",
            )
            _ckpt_thread.start()

            # ── Log full system prompt sent to model ──
            _cached_sp = getattr(agent, '_cached_system_prompt', None) or ''
            _ephemeral_sp = getattr(agent, 'ephemeral_system_prompt', None) or ''
            _full_sp = (_cached_sp + '\n\n' + _ephemeral_sp).strip() if _ephemeral_sp else _cached_sp
            print(
                f'[webui] session={session_id} '
                f'cached_len={len(_cached_sp)} ephemeral_len={len(_ephemeral_sp)} '
                f'full_system_prompt_len={len(_full_sp)}',
                flush=True,
            )
            print(
                f'[webui] session={session_id} '
                f'=== FULL SYSTEM PROMPT ===\n{_full_sp}\n=== END ===',
                flush=True,
            )

            put('phase', {'stage': 'thinking'})

            # [dedup-diagnostic] Log conversation_history being sent to agent
            _sanitized_history = _sanitize_messages_for_api(s.messages)
            _hist_roles = [m.get('role', '?') for m in _sanitized_history]
            _hist_user_count = sum(1 for m in _sanitized_history if m.get('role') == 'user')
            _hist_user_snips = [
                str(m.get('content', ''))[:80] for m in _sanitized_history
                if m.get('role') == 'user'
            ]
            logger.debug(
                "[dedup-diagnostic] run_conversation INPUT | session=%s | "
                "s.messages_count=%d | sanitized_count=%d | roles=%s | "
                "user_count=%d | user_snips=%s | new_user_msg=%r",
                session_id, len(s.messages or []), len(_sanitized_history),
                _hist_roles, _hist_user_count, _hist_user_snips,
                msg_text[:80],
            )
            print(
                f'[dedup] TURN_INPUT session={session_id} '
                f's.msgs={len(s.messages or [])} sanitized={len(_sanitized_history)} '
                f'user_count={_hist_user_count} '
                f'user_snips={_hist_user_snips} '
                f'new_msg={msg_text[:80]!r}',
                flush=True,
            )

            # ── Pre-flight compression ──────────────────────────────────────────
            # Check both message-count and token thresholds before the agent
            # runs.  SDK built-in compression is disabled (compression_enabled
            # = False) so all compression is managed here.  When compression
            # fires we send compressing/compressed SSE events so the frontend
            # can show a transient InfoCard instead of a silent pause.
            from api.config import load_settings as _ls_preflight
            from api.routes import (
                _estimate_messages_tokens_rough,
                _archive_session_before_compress,
                _persist_compressed_messages,
                _reanchor_last_seen_after_compress,
                _record_auto_compress_quality,
            )
            _settings_preflight = _ls_preflight()
            _msg_threshold = int(str(_settings_preflight.get("auto_compress_threshold", "0") or "0"))
            _token_threshold_pct = float(str(_settings_preflight.get("auto_compress_token_threshold", "0") or "0"))
            _need_compress = False
            _est_tokens = 0

            if _msg_threshold > 0 and len(s.messages) >= _msg_threshold:
                _need_compress = True
            elif _token_threshold_pct > 0:
                _ctx_len = getattr(agent.context_compressor, 'context_length', 0) or 0
                _est_tokens = _estimate_messages_tokens_rough(_sanitized_history)
                if _ctx_len > 0 and _est_tokens >= int(_ctx_len * _token_threshold_pct):
                    _need_compress = True

            if _need_compress and not getattr(s, 'auto_compress_disabled', False):
                _reason_parts = []
                if _msg_threshold > 0 and len(s.messages) >= _msg_threshold:
                    _reason_parts.append(f'{len(s.messages)} messages ≥ {_msg_threshold}')
                elif _token_threshold_pct > 0:
                    _reason_parts.append(f'tokens ≥ {int(_token_threshold_pct * 100)}% of context')
                _reason = ', '.join(_reason_parts) if _reason_parts else 'threshold reached'
                try:
                    put('compressing', {
                        'message': '正在压缩上下文...',
                        'reason': _reason,
                        'msg_count': len(s.messages),
                        'msg_threshold': _msg_threshold,
                        'token_threshold_pct': _token_threshold_pct,
                    })
                except Exception:
                    pass

                if not _est_tokens:
                    _est_tokens = _estimate_messages_tokens_rough(_sanitized_history)
                _msg_before = len(_sanitized_history)
                try:
                    _compressed = agent.context_compressor.compress(
                        _sanitized_history, current_tokens=_est_tokens,
                    )
                except Exception:
                    _compressed = None
                # "compressed" event semantics = "the compression attempt has
                # finished", NOT "compression actually shrank the history".
                # compress() returns the *same* list unchanged when there is
                # nothing to compress (too few messages, no compressable window,
                # or summary failure with abort_on_summary_failure), and can even
                # return an equal-length list when only one turn is summarised.
                # We must still emit `compressed` so the frontend always
                # dismisses the "compressing" spinner InfoCard.
                _effective = (
                    bool(_compressed)
                    and _compressed is not _sanitized_history
                    and len(_compressed) < _msg_before
                )
                if _effective:
                    try:
                        _archive_session_before_compress(s)
                    except Exception:
                        pass
                    s.messages = _compressed
                    _sanitized_history = _compressed
                    _distribute_inline_reasoning(s.messages)
                    s.tool_calls = []
                    s.last_compression_time = time.time()
                    # Re-anchor the seen watermark to the compressed count so
                    # the unread badge stays correct for this turn's new messages.
                    _reanchor_last_seen_after_compress(s)
                    # Persist now (before run_conversation) so loadSession
                    # returns the compressed list even after server restart.
                    # The current user message is persisted by the SDK
                    # (run_conversation's persist_user_message), not here —
                    # writing it here would double-persist it.
                    _persist_compressed_messages(s)

                _msg_after = len(_compressed) if _compressed else _msg_before
                _approx_tokens_after = (
                    sum(
                        len(str(m.get('content', ''))) for m in _compressed
                        if isinstance(m, dict)
                    ) // 4 if _compressed else _est_tokens
                )
                try:
                    put('compressed', {
                        'message': 'Context auto-compressed to continue the conversation',
                        'effective': _effective,
                        'saved_messages': _msg_before - _msg_after if _effective else 0,
                        'approx_tokens_before': _est_tokens,
                        'approx_tokens_after': _approx_tokens_after,
                    })
                except Exception:
                    pass
                print(
                    f'[preflight-compress] session={session_id} '
                    f'messages {_msg_before}→{_msg_after} '
                    f'effective={_effective} '
                    f'tokens_approx {_est_tokens}→{_approx_tokens_after}',
                    flush=True,
                )

                # ── Quality tracking ───────────────────────────────────
                # Feed the compression outcome into the auto-compress quality
                # gate so repeated ineffective compressions eventually disable
                # auto-compress (same logic as _auto_compress_session).  When
                # _compressed is None (compress raised), _msg_after == _msg_before
                # → ratio 1.0 → counted as one poor attempt.
                _record_auto_compress_quality(s, _msg_before, _msg_after)
                try:
                    s.sync_webui_metadata()
                except Exception:
                    pass

            result = agent.run_conversation(
                user_message=workspace_ctx + msg_text,
                system_message=workspace_system_msg,
                conversation_history=_sanitized_history,
                task_id=session_id,
                persist_user_message=msg_text,
            )
            # ── Skill command: attach original_content to user message for clean display ──
            _pending_original = getattr(s, '_pending_original_message', None)
            if _pending_original:
                # Only clear on normal completion; cancel_stream needs it.
                if not cancel_event.is_set():
                    s._pending_original_message = None
                for _m in reversed(result.get('messages') or []):
                    if isinstance(_m, dict) and _m.get('role') == 'user':
                        _m['original_content__webui'] = _pending_original
                        break
            # ── Diagnostic: always print run_conversation result summary ──
            _diag_msgs = result.get('messages') or [] if isinstance(result, dict) else []
            _diag_roles = [m.get('role') for m in _diag_msgs if isinstance(m, dict)]
            print(
                f'[webui] run_conversation done session={session_id}'
                f' | result_type={type(result).__name__}'
                f' | result_error={result.get("error") if isinstance(result, dict) else "N/A"!r}'
                f' | msg_roles={_diag_roles}'
                f' | agent._last_error={getattr(agent, "_last_error", "ATTR_MISSING")!r}',
                flush=True,
            )
            # ── Ephemeral mode (/btw): deliver answer, skip persistence, cleanup ──
            if ephemeral:
                _answer = ''
                for _m in reversed(result.get('messages') or []):
                    if isinstance(_m, dict) and _m.get('role') == 'assistant':
                        _answer = str(_m.get('content', ''))
                        break
                put('done', {
                    'session': {'session_id': session_id, 'messages': result.get('messages', [])},
                    'usage': {'input_tokens': 0, 'output_tokens': 0},
                    'ephemeral': True,
                    'answer': _answer,
                })
                if _checkpoint_stop is not None:
                    _checkpoint_stop.set()
                try:
                    import pathlib
                    pathlib.Path(s.path).unlink(missing_ok=True)
                except Exception:
                    pass
                return  # skip all normal persistence for ephemeral sessions
            if _checkpoint_stop is not None:
                _checkpoint_stop.set()
            if _ckpt_thread is not None:
                _ckpt_thread.join(timeout=15)
            with _agent_lock:
                # The agent returns per-message reasoning_content (API-native).
                # Copy it to reasoning (WebUI display field).
                _agent_msgs = result.get('messages') or s.messages
                for _m in _agent_msgs:
                    if isinstance(_m, dict) and _m.get('role') == 'assistant':
                        _rc = _m.get('reasoning_content')
                        if isinstance(_rc, str) and _rc.strip() and not _m.get('reasoning'):
                            _m['reasoning'] = _rc.strip()

                # [dedup-diagnostic] Log s.messages assignment
                _prev_msg_count = len(s.messages or [])
                _result_roles = [m.get('role', '?') for m in (_agent_msgs or [])]
                _result_user_count = sum(1 for m in (_agent_msgs or []) if m.get('role') == 'user')
                logger.debug(
                    "[dedup-diagnostic] s.messages = result['messages'] | session=%s | "
                    "cancelled=%s | prev_msg_count=%d | result_count=%d result_roles=%s | "
                    "result_user_count=%d",
                    session_id, cancel_event.is_set(),
                    _prev_msg_count, len(_agent_msgs or []), _result_roles,
                    _result_user_count,
                )
                print(
                    f'[dedup] s.messages=result session={session_id} '
                    f'cancelled={cancel_event.is_set()} '
                    f'prev_msgs={_prev_msg_count} '
                    f'result_msgs={len(_agent_msgs or [])} result_roles={_result_roles}',
                    flush=True,
                )

                # When cancelled, cancel_stream handles the final s.messages
                # state (DB reload + post-processing).  Preserve whatever
                # cancel_stream set up instead of overwriting.
                if not cancel_event.is_set():
                    s.messages = _agent_msgs

                # Save collected timestamps so cancel_stream can use them
                # (they would otherwise be lost when this function returns).
                s._all_message_ts = _all_message_ts

                # ── Step 1: post-process agent result ────────────────────
                _postprocess_messages(
                    s.messages,
                    start_idx=_prev_msg_count,
                    msg_text=msg_text,
                    attachments=attachments,
                    all_message_ts=_all_message_ts,
                    reasoning_text=_reasoning_text,
                )

                # ── Detect silent agent failure (no assistant reply produced) ──
                # When the agent catches an auth/network error internally it may return
                # an empty final_response without raising — the stream would end with
                # a done event containing zero assistant messages, leaving the user with
                # no feedback. Emit an apperror so the client shows an inline error.
                #
                # IMPORTANT: check only messages AFTER the last user message in the
                # result, not the entire history. The history already contains assistant
                # messages from previous turns; we need to know if the CURRENT turn
                # produced a new assistant reply.
                _result_messages = result.get('messages') or []
                # Find the index of the last user message in the result
                _last_user_idx = -1
                for _idx, _m in enumerate(_result_messages):
                    if isinstance(_m, dict) and _m.get('role') == 'user':
                        _last_user_idx = _idx
                # Check if there's any non-empty assistant message after the last user message
                _assistant_added = any(
                    m.get('role') == 'assistant' and str(m.get('content') or '').strip()
                    for m in _result_messages[_last_user_idx + 1:]
                ) if _last_user_idx >= 0 else any(
                    m.get('role') == 'assistant' and str(m.get('content') or '').strip()
                    for m in _result_messages
                )
                # _token_sent tracks whether on_token() was called (any streamed text).
                # Skip the no-response error when the stream was cancelled — the agent
                # may have been interrupted before producing any output, which is expected.
                if cancel_event.is_set():
                    put('cancel', {'message': 'Cancelled by user'})
                elif not _assistant_added and not _token_sent:
                    # Extract error from multiple possible locations in the result dict.
                    # hermes-agent stores errors in 'error', 'failed', or 'final_response'.
                    _result_error = ''
                    if isinstance(result, dict):
                        _result_error = (
                            str(result.get('error') or '')
                            or str(result.get('final_response') or '')
                            or str(result.get('failed') or '')
                        )
                    _last_err = getattr(agent, '_last_error', None) or _result_error or ''
                    _err_str = str(_last_err) if _last_err else _result_error
                    # For exceptions with no message (e.g. bare AssertionError), include
                    # the exception type and traceback snippet so users see something useful.
                    if _last_err and not _err_str:
                        _err_str = type(_last_err).__name__
                    # Always print diagnostic info — even when _last_err is None,
                    # the result dict may contain useful context.
                    import traceback as _tb_mod
                    _tb_info = ''.join(_tb_mod.format_exception(type(_last_err), _last_err, _last_err.__traceback__)) if isinstance(_last_err, Exception) else ''
                    print(
                        f'[webui] silent agent failure for session {session_id}'
                        f' | type={type(_last_err).__name__ if _last_err else "None"!r}'
                        f' | err={str(_last_err)!r}'
                        f' | result_keys={list(result.keys()) if isinstance(result, dict) else type(result).__name__}'
                        f' | result_error={result.get("error") if isinstance(result, dict) else ""!r}'
                        f' | messages_count={len(result.get("messages") or []) if isinstance(result, dict) else 0}'
                        + (('\n' + _tb_info) if _tb_info else ''),
                        flush=True,
                    )
                    _err_lower = _err_str.lower()
                    _is_quota = (
                        'insufficient credit' in _err_lower
                        or 'credit balance' in _err_lower
                        or 'credits exhausted' in _err_lower
                        or 'quota_exceeded' in _err_lower
                        or 'quota exceeded' in _err_lower
                        or 'exceeded your current quota' in _err_lower
                    )
                    _is_auth = (
                        not _is_quota and (
                            '401' in _err_str
                            or (_last_err and 'AuthenticationError' in type(_last_err).__name__)
                            or 'authentication' in _err_lower
                            or 'unauthorized' in _err_lower
                            or 'invalid api key' in _err_lower
                            or 'invalid_api_key' in _err_lower
                        )
                    )
                    _is_timeout = (
                        not _is_quota and not _is_auth and (
                            'timed out' in _err_lower
                            or 'timeout' in _err_lower
                            or 'api request returned none' in _err_lower
                            or 'returned none after all retries' in _err_lower
                        )
                    )
                    if _is_quota:
                        _err_label = 'Out of credits'
                        _err_type = 'quota_exhausted'
                        _err_hint = 'Your provider account is out of credits. Top up your balance or switch providers via `hermes model`.'
                    elif _is_auth:
                        _err_label = 'Authentication failed'
                        _err_type = 'auth_mismatch'
                        _err_hint = (
                            'The selected model may not be supported by your configured provider or '
                            'your API key is invalid. Run `hermes model` in your terminal to '
                            'update credentials, then restart the WebUI.'
                        )
                    elif _is_timeout:
                        _err_label = 'Request timed out'
                        _err_type = 'timeout'
                        _err_hint = (
                            'The API request timed out after all retries. '
                            'This may be a temporary network issue or the model endpoint is overloaded. '
                            'Please try again in a moment.'
                        )
                    else:
                        _err_label = 'No response received'
                        _err_type = 'no_response'
                        _err_hint = 'Verify your API key is valid and the selected model is available for your account.'
                    put('apperror', {
                        'message': _err_str or f'{_err_label}.',
                        'type': _err_type,
                        'hint': _err_hint,
                    })
                    # Also push via notify channel so the frontend receives the
                    # error before end_stream disconnects the replay listener.
                    try:
                        from api.notify_stream import notify_stream as _ns
                        _ns.put('apperror', {
                            'session_id': s.session_id,
                            'stream_id': stream_id,
                            'message': _err_str or f'{_err_label}.',
                            'type': _err_type,
                            'hint': _err_hint,
                        })
                    except Exception:
                        pass
                    # Clear stream/pending state so the session does not appear
                    # "agent_running" on reload after a silent failure.
                    # Persist the error so it survives page reload.
                    # _error=True ensures _sanitize_messages_for_api excludes it from
                    # subsequent API calls so the LLM never sees its own error as prior context.
                    s.pending_user_message = None
                    s.pending_attachments = []
                    s.pending_started_at = None
                    return  # apperror already closes the stream on the client side

                # ── Handle context compression side effects ──
                # If compression fired inside run_conversation, the agent may have
                # rotated its session_id. Detect and fix the mismatch so the WebUI
                # continues writing to the correct session file.
                #
                # Lock migration: when session_id rotates, we alias the new ID to
                # the *same* Lock object under SESSION_AGENT_LOCKS so that
                # subsequent callers using _get_session_agent_lock(new_sid) get the
                # same Lock the streaming thread is already holding.  We then pop
                # the old-id entry to prevent a leak.  This is safe because we
                # already hold _agent_lock (the Lock object itself), so the
                # Only auto-generate title when still default; preserves user renames
                if s.title == 'Untitled' or s.title == 'New Chat' or not s.title:
                    s.title = title_from(s.messages, s.title)
                _looks_default = (s.title == 'Untitled' or s.title == 'New Chat' or not s.title)
                _looks_provisional = _is_provisional_title(s.title, s.messages)
                _invalid_existing_title = _looks_invalid_generated_title(s.title)
                _should_bg_title = (
                    (_looks_default or _looks_provisional or _invalid_existing_title)
                    and (not getattr(s, 'llm_title_generated', False) or _invalid_existing_title)
                )
                _u0 = ''
                _a0 = ''
                if _should_bg_title:
                    _u0, _a0 = _first_exchange_snippets(s.messages)
                # Read token/cost usage from the agent object (if available)
                input_tokens = getattr(agent, 'session_prompt_tokens', 0) or 0
                output_tokens = getattr(agent, 'session_completion_tokens', 0) or 0
                estimated_cost = getattr(agent, 'session_estimated_cost_usd', None)
                s.input_tokens = (s.input_tokens or 0) + input_tokens
                s.output_tokens = (s.output_tokens or 0) + output_tokens
                if estimated_cost:
                    s.estimated_cost = (s.estimated_cost or 0) + estimated_cost
                # Persist context-window data from the compressor so the
                # frontend ContextUsageIndicator can restore after page refresh.
                _cc = getattr(agent, 'context_compressor', None)
                if _cc:
                    s.context_length = getattr(_cc, 'context_length', 0) or 0
                    s.last_prompt_tokens = getattr(_cc, 'last_prompt_tokens', 0) or 0
                    s.threshold_tokens = getattr(_cc, 'threshold_tokens', 0) or 0
                # Persist tool-call summaries even when the final message history only
                # kept bare tool rows and omitted explicit assistant tool_call IDs.
                tool_calls = _extract_tool_calls_from_messages(
                    s.messages,
                    live_tool_calls=_live_tool_calls,
                )
                s.tool_calls = tool_calls
                s.pending_user_message = None
                s.pending_attachments = []
                s.pending_started_at = None

                # ── Step 2: sync __webui columns to DB ──────────────────
                s.sync_webui_metadata()

                # ── Step 3: reload from DB to guarantee cache == DB ─────
                # On cancel, cancel_stream handles this instead.
                if not cancel_event.is_set():
                    from api.models import Session as _SessionCls
                    _fresh = _SessionCls.load(session_id)
                    if _fresh and _fresh.messages:
                        s.messages = _fresh.messages

                # ── Step 4: re-apply post-processing on reloaded messages ──
                # DB stores raw SDK content; XML strip, reasoning dist,
                # and attachments must be re-applied.  Timestamp stamping
                # is skipped because __webui columns were backfilled in
                # step 2 and reloaded from DB.
                _postprocess_messages(
                    s.messages,
                    start_idx=0,
                    msg_text=msg_text,
                    attachments=attachments,
                    all_message_ts=None,       # DB already has timestamps
                    reasoning_text=_reasoning_text,
                )
            usage = {'input_tokens': input_tokens, 'output_tokens': output_tokens, 'estimated_cost': estimated_cost}
            # Include context window data from the agent's compressor for the UI indicator
            _cc = getattr(agent, 'context_compressor', None)
            if _cc:
                usage['context_length'] = getattr(_cc, 'context_length', 0) or 0
                usage['threshold_tokens'] = getattr(_cc, 'threshold_tokens', 0) or 0
                usage['last_prompt_tokens'] = getattr(_cc, 'last_prompt_tokens', 0) or 0
            # (reasoning trace already attached + saved above, before s.sync_webui_metadata())
            # Leftover-steer delivery: if a /steer was queued (via
            # api/chat/steer) but the agent finished its turn before
            # reaching a tool-result boundary that would consume it,
            # the text is still stashed in agent._pending_steer. Drain
            # it now and emit a pending_steer_leftover SSE event so the
            # frontend can queue it for the next turn — same fallback
            # path as the CLI in cli.py:8788-8794.
            try:
                _drain_pending_steer = getattr(agent, '_drain_pending_steer', None)
                _leftover = _drain_pending_steer() if _drain_pending_steer else None
                if _leftover:
                    put('pending_steer_leftover', {
                        'session_id': session_id,
                        'text': str(_leftover),
                    })
            except Exception:
                logger.debug("Failed to drain pending steer for session %s", session_id)
            raw_session = s.compact() | {'messages': s.messages, 'tool_calls': tool_calls}

            if _should_bg_title and _u0 and _a0:
                print(f'[title-sync-backend] running title update: session={s.session_id[:8]} title={s.title!r}', flush=True)
                _run_title_update(s.session_id, _u0, _a0, str(s.title or '').strip(), put, agent)
            else:
                print(f'[title-sync-backend] title NOT run: should={_should_bg_title} u0={bool(_u0)} a0={bool(_a0)} session={session_id[:8]}', flush=True)
            # done is emitted AFTER title generation so the frontend sees the
            # final title when it reloads sessions on the done event.
            logger.info(
                "[done] session=%s stream=%s subscribers=%d",
                session_id[:12], stream_id[:8],
                len(q._subscribers) if hasattr(q, '_subscribers') else -1,
            )
            put('done', {
                'session': redact_session_data(raw_session),
                'usage': usage,
            })
            # Emit metering stats for the header TPS label
            meter_stats = meter().get_stats()
            meter_stats['session_id'] = session_id
            put('metering', meter_stats)
            # Adaptive title refresh: re-generate title from latest exchange
            # every N exchanges (when enabled in settings). Runs asynchronously
            # so it doesn't block the stream.
            _maybe_schedule_title_refresh(s, put, agent)
        finally:
            # Stop the live metering ticker
            _metering_stop.set()
            # Unregister the gateway approval callback and unblock any threads
            # still waiting on approval (e.g. stream cancelled mid-approval).
            if _approval_registered and _unreg_notify is not None:
                try:
                    _unreg_notify(session_id)
                except Exception:
                    logger.debug("Failed to unregister approval callback")
            if _clarify_registered and _unreg_clarify_notify is not None:
                try:
                    _unreg_clarify_notify(session_id)
                except Exception:
                    logger.debug("Failed to unregister clarify callback")
            with _ENV_LOCK:
                if old_cwd is None: os.environ.pop('TERMINAL_CWD', None)
                else: os.environ['TERMINAL_CWD'] = old_cwd
                if old_exec_ask is None: os.environ.pop('HERMES_EXEC_ASK', None)
                else: os.environ['HERMES_EXEC_ASK'] = old_exec_ask
                if old_gateway_session is None: os.environ.pop('HERMES_GATEWAY_SESSION', None)
                else: os.environ['HERMES_GATEWAY_SESSION'] = old_gateway_session
                if old_hermes_home is None: os.environ.pop('HERMES_HOME', None)
                else: os.environ['HERMES_HOME'] = old_hermes_home
                try:
                    if old_cdp_url is None:
                        os.environ.pop('BROWSER_CDP_URL', None)
                    else:
                        os.environ['BROWSER_CDP_URL'] = old_cdp_url
                    if old_http_proxy is not None:
                        os.environ['HTTP_PROXY'] = old_http_proxy
                    if old_https_proxy is not None:
                        os.environ['HTTPS_PROXY'] = old_https_proxy
                except NameError:
                    pass
            # Clear per-thread session context (ContextVar)
            try:
                clear_session_vars(_session_ctx_tokens)
            except Exception:
                pass

        # Close any remaining xterm windows for this session
        try:
            from api.cli_viewer import cleanup_session_xterms
            cleanup_session_xterms(session_id)
        except Exception:
            pass

        # ── Goal continuation (post-turn) ──
        # If a standing goal is active, evaluate the last assistant response
        # and auto-continue if the goal is not yet achieved (CLI behaviour).
        try:
            from hermes_cli.goals import GoalManager
            mgr = GoalManager(session_id)
            if mgr.is_active():
                last_response = ""
                for m in reversed(s.messages or []):
                    if isinstance(m, dict) and m.get("role") == "assistant":
                        last_response = str(m.get("content", ""))
                        break
                if last_response:
                    decision = mgr.evaluate_after_turn(last_response, user_initiated=False)
                    # Merge any subgoals added by a concurrent /subgoal command.
                    # evaluate_after_turn calls save_goal() which can overwrite
                    # subgoals added mid-turn.  The handler tags s._pending_subgoals
                    # so we can merge them back and re-save.
                    _pending = getattr(s, '_pending_subgoals', None)
                    if _pending is not None and mgr._state:
                        if _pending != (mgr._state.subgoals or []):
                            mgr._state.subgoals = list(_pending)
                            from hermes_cli.goals import save_goal as _save_goal
                            _save_goal(session_id, mgr._state)
                    s._pending_subgoals = None
                    # Re-check against SessionDB: the goal may have been
                    # paused/cleared from another thread (e.g. /goal pause)
                    # while this turn was running.  Only enqueue if the goal
                    # is STILL active after reloading from disk.
                    if decision.get("should_continue"):
                        from hermes_cli.goals import load_goal as _reload_goal
                        fresh = _reload_goal(session_id)
                        if fresh is None or fresh.status != "active":
                            logger.debug(
                                "Goal continuation skipped for session %s: "
                                "goal no longer active (status=%s)",
                                session_id, fresh.status if fresh else None,
                            )
                        else:
                            prompt = decision.get("continuation_prompt")
                            if prompt:
                                from api.config import SESSION_MESSAGE_QUEUE, SESSION_MESSAGE_QUEUE_LOCK, _MAX_QUEUE_LENGTH
                                with SESSION_MESSAGE_QUEUE_LOCK:
                                    q = SESSION_MESSAGE_QUEUE.get(session_id)
                                    if q is None:
                                        q = []
                                        SESSION_MESSAGE_QUEUE[session_id] = q
                                    if len(q) < _MAX_QUEUE_LENGTH:
                                        # Preserve goal text as original_content so the
                                        # continuation prompt isn't exposed in chat history.
                                        _goal_text = (fresh.goal if fresh else None) or ""
                                        q.append({
                                            "message": prompt,
                                            "model": s.model,
                                            "workspace": s.workspace,
                                            "attachments": [],
                                            "original_content__webui": _goal_text,
                                        })
                                logger.debug("Goal continuation enqueued for session %s (verdict=%s)",
                                             session_id, decision.get("verdict"))
        except Exception as e:
            logger.debug("Goal continuation check failed for session %s: %s", session_id, e)

        # Dequeue any queued message for this session (success path)
        _dequeue_next(session_id)

    except Exception as e:
        _tb_str = traceback.format_exc()
        print(
            f'[webui] stream error for session {session_id}'
            f' | type={type(e).__name__!r} | msg={str(e)!r}\n'
            + _tb_str,
            flush=True,
        )
        err_str = str(e)
        # For exceptions with no message (e.g. bare AssertionError), fall back to
        # the last line of the traceback which usually contains the most useful info.
        if not err_str:
            _tb_lines = [l.strip() for l in _tb_str.splitlines() if l.strip()]
            # Use "ExceptionType (at file:line)" format so users see something actionable
            _loc = _tb_lines[-2] if len(_tb_lines) >= 2 else ''
            err_str = f'{type(e).__name__}: (no message){(" — " + _loc) if _loc else ""}'
        # Sanitize HTML from provider error responses — some providers return
        # full HTML pages (e.g. nginx "404 page not found") instead of JSON errors.
        # Strip HTML tags to avoid rendering raw markup in the chat message.
        _stripped = re.sub(r'<[^>]+>', ' ', err_str)
        _stripped = re.sub(r'\s+', ' ', _stripped).strip()
        if _stripped != err_str:
            err_str = _stripped
        _exc_lower = err_str.lower()
        # Classify before saving so the error message can be persisted to the session.
        # Check quota exhaustion first — OpenAI billing 429s use insufficient_quota which
        # also matches rate-limit patterns, so order matters.
        _exc_is_quota = (
            'insufficient credit' in _exc_lower
            or 'credit balance' in _exc_lower
            or 'credits exhausted' in _exc_lower
            or 'quota_exceeded' in _exc_lower
            or 'quota exceeded' in _exc_lower
            or 'exceeded your current quota' in _exc_lower
        )
        _exc_is_rate_limit = (not _exc_is_quota) and (
            'rate limit' in _exc_lower or '429' in err_str or 'RateLimitError' in type(e).__name__
        )
        _exc_is_auth = (
            '401' in err_str
            or 'AuthenticationError' in type(e).__name__
            or 'authentication' in _exc_lower
            or 'unauthorized' in _exc_lower
            or 'invalid api key' in _exc_lower
            or 'no cookie auth credentials' in _exc_lower
        )
        _exc_is_not_found = (
            '404' in err_str
            or 'not found' in _exc_lower
            or 'does not exist' in _exc_lower
            or 'model not found' in _exc_lower
            or 'model_not_found' in _exc_lower
            or 'invalid model' in _exc_lower
            or 'does not match any known model' in _exc_lower
            or 'unknown model' in _exc_lower
        )
        if _exc_is_quota:
            _exc_label, _exc_type, _exc_hint = (
                'Out of credits', 'quota_exhausted',
                'Your provider account is out of credits. Top up your balance or switch providers via `hermes model`.',
            )
        elif _exc_is_rate_limit:
            _exc_label, _exc_type, _exc_hint = (
                'Rate limit reached', 'rate_limit',
                'Rate limit reached. The fallback model (if configured) was also exhausted. Try again in a moment.',
            )
        elif _exc_is_auth:
            _exc_label, _exc_type, _exc_hint = (
                'Authentication error', 'auth_mismatch',
                'The selected model may not be supported by your configured provider. '
                'Run `hermes model` in your terminal to switch providers, then restart the WebUI.',
            )
        elif _exc_is_not_found:
            _exc_label, _exc_type, _exc_hint = (
                'Model not found', 'model_not_found',
                'The selected model was not found by the provider. '
                'Check the model ID in Settings or run `hermes model` to verify it exists for your provider.',
            )
        else:
            _exc_label, _exc_type, _exc_hint = 'Error', 'error', ''
        # Whether this thread still owns the session.  When cancel_stream() has
        # already released the stream (interrupt mode, eager release), the
        # session may be claimed by a new stream before this except block runs.
        # We must not mutate session state or dequeue in that case — the active
        # stream's own exit handler will handle cleanup and dequeue.
        _owned = True
        if s is not None:
            if _checkpoint_stop is not None:
                _checkpoint_stop.set()
            if _ckpt_thread is not None:
                _ckpt_thread.join(timeout=15)
            _lock_ctx = _agent_lock if _agent_lock is not None else contextlib.nullcontext()
            with _lock_ctx:
                # Guard: only mutate session if we are still the active stream.
                # After cancel_stream (interrupt mode), stream may have been
                # replaced by a new one.
                if stream_manager.get_stream_id(s.session_id) != stream_id:
                    _owned = False
                if _owned:
                    s.pending_user_message = None
                    s.pending_attachments = []
                    s.pending_started_at = None
        _apperror_payload: dict = {'message': err_str, 'type': _exc_type}
        if _exc_hint:
            _apperror_payload['hint'] = _exc_hint
        put('apperror', _apperror_payload)
        # Also push via notify channel (same rationale as inner handler).
        try:
            from api.notify_stream import notify_stream as _ns
            _ns.put('apperror', {
                'session_id': s.session_id,
                'stream_id': stream_id,
                **_apperror_payload,
            })
        except Exception:
            pass
        # Check for queued messages before cleanup so the dequeued stream
        # can register itself in STREAMS before the current stream is popped.
        # Skip if we lost ownership — the active stream's exit handler will dequeue.
        if _owned:
            _dequeue_next(session_id)
    finally:
        # Stop periodic checkpoint thread if it was started (Issue #765)
        if _checkpoint_stop is not None:
            _checkpoint_stop.set()
        if _ckpt_thread is not None:
            _ckpt_thread.join(timeout=15)
        _clear_thread_env()  # TD1: always clear thread-local context
        # End the stream via StreamManager (handles ReplayableStream.close())
        stream_manager.end_stream(s, stream_id)
        with STREAMS_LOCK:
            CANCEL_FLAGS.pop(stream_id, None)
            AGENT_INSTANCES.pop(stream_id, None)  # Clean up agent instance reference
            STREAM_PARTIAL_TEXT.pop(stream_id, None)  # Clean up partial text buffer (#893)

# ============================================================
# SECTION: HTTP Request Handler
# do_GET: read-only API endpoints + SSE stream + static HTML
# do_POST: mutating endpoints (session CRUD, chat, upload, approval)
# Routing is a flat if/elif chain. See ARCHITECTURE.md section 4.1.
# ============================================================


def _dequeue_next(session_id: str) -> None:
    """If the session has a queued message, dequeue and start a new agent thread.

    Called from the streaming thread's finally block (success/error exit) and
    from cancel_stream() (cancel exit).  The caller must NOT hold any lock that
    _run_agent_streaming needs to acquire internally.
    """
    from api.config import SESSION_MESSAGE_QUEUE, SESSION_MESSAGE_QUEUE_LOCK

    with SESSION_MESSAGE_QUEUE_LOCK:
        q = SESSION_MESSAGE_QUEUE.get(session_id)
        if not q:
            return
        payload = q.pop(0)
        if not q:
            del SESSION_MESSAGE_QUEUE[session_id]

    # Spawn new agent thread outside the lock
    stream_id = uuid.uuid4().hex
    try:
        s = get_session(session_id)
    except KeyError:
        return
    # Guard: if the session already has an active stream (e.g. interrupt mode
    # started a new stream before this dequeue fired), re-enqueue the message
    # so the active stream's own exit handler picks it up when it finishes.
    if stream_manager.get_stream_id(s.session_id):
        with SESSION_MESSAGE_QUEUE_LOCK:
            q = SESSION_MESSAGE_QUEUE.get(session_id)
            if q is None:
                q = []
                SESSION_MESSAGE_QUEUE[session_id] = q
            q.insert(0, payload)
        return
    stream_id = stream_manager.begin_stream(s)
    if not stream_id:
        return
    with _get_session_agent_lock(session_id):
        s.pending_user_message = payload.get("message", "")
        s.pending_attachments = payload.get("attachments", [])
        _oc = payload.get("original_content", "")
        if _oc:
            s._pending_original_message = _oc
        s.pending_started_at = time.time()
        s.sync_webui_metadata()
    thr = threading.Thread(
        target=_run_agent_streaming,
        args=(
            session_id,
            payload.get("message", ""),
            payload.get("model", s.model),
            payload.get("workspace", s.workspace),
            stream_id,
            payload.get("attachments", []),
        ),
        daemon=True,
    )
    thr.start()


def _try_steer(session_id: str, text: str) -> dict:
    """Attempt to inject steer text into the running agent for a session.

    Returns {"accepted": bool, "fallback": str|None, "stream_id": str|None}.
    Does NOT format an HTTP response — caller handles that.
    """
    from api import config as _cfg

    with _cfg.SESSION_AGENT_CACHE_LOCK:
        cached = _cfg.SESSION_AGENT_CACHE.get(session_id)
    if not cached:
        return {"accepted": False, "fallback": "no_cached_agent", "stream_id": None}
    agent = cached[0]
    if not hasattr(agent, "steer"):
        return {"accepted": False, "fallback": "agent_lacks_steer", "stream_id": None}

    try:
        s = get_session(session_id)
    except KeyError:
        return {"accepted": False, "fallback": "session_not_found", "stream_id": None}
    active_stream_id = stream_manager.get_stream_id(session_id)
    if not active_stream_id:
        return {"accepted": False, "fallback": "not_running", "stream_id": None}
    stream_alive = stream_manager.get_stream_by_id(active_stream_id) is not None
    if not stream_alive:
        return {"accepted": False, "fallback": "stream_dead", "stream_id": None}

    try:
        accepted = bool(agent.steer(text))
    except Exception:
        return {"accepted": False, "fallback": "steer_error", "stream_id": active_stream_id}

    return {"accepted": accepted, "fallback": None, "stream_id": active_stream_id}


def _handle_chat_steer(handler, body: dict) -> bool:
    """Inject a /steer payload into the active agent for a session.

    Delegates to _try_steer() for the core logic, then formats an HTTP response.
    """
    from api.helpers import j, bad

    sid = str((body or {}).get("session_id", "") or "").strip()
    text = str((body or {}).get("text", "") or "").strip()
    if not sid:
        return bad(handler, "session_id required")
    if not text:
        return bad(handler, "text required")

    result = _try_steer(sid, text)
    return j(handler, result)


def cancel_stream(stream_id: str) -> bool:
    """Signal an in-flight stream to cancel. Returns True if the stream existed.

    Waits for the agent thread to fully exit before performing session cleanup,
    so ``s.messages`` is guaranteed to be in its final state with no race
    between cancel_stream and the agent thread's _restore_reasoning_metadata.

    Step order:
      1. Set cancel flag
      2. Wait for agent to appear in AGENT_INSTANCES (timeout 5 s)
      3. Call agent.interrupt()
      4. Wait for agent thread to exit (AGENT_INSTANCES removed by finally)
      5. Pop stream state + session cleanup + "Interrupted." separator
    """
    # ── Step 1: set cancel flag ──────────────────────────────────────────
    with STREAMS_LOCK:
        if stream_manager.get_stream_by_id(stream_id) is None:
            return False
        flag = CANCEL_FLAGS.get(stream_id)
        if flag:
            flag.set()
        agent = AGENT_INSTANCES.get(stream_id)

    # ── Step 2: wait for agent to be initialised ─────────────────────────
    # The agent thread registers itself in AGENT_INSTANCES partway through
    # _run_agent_streaming.  If cancel arrives before that point the agent
    # object is None and we must poll until it appears.  Release the lock
    # during the wait so the agent thread can acquire it for registration.
    if agent is None:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            time.sleep(0.05)
            with streams_lock:
                agent = agent_instances.get(stream_id)
            if agent is not None:
                break

    # ── Step 3: interrupt ────────────────────────────────────────────────
    if agent:
        try:
            agent.interrupt("Cancelled by user")
        except Exception:
            logger.debug("Failed to interrupt agent for stream %s", stream_id)

        # Clear any pending clarify prompt so blocked tool calls can unwind.
        try:
            from api.clarify import clear_pending as _clear_clarify_pending
            if getattr(agent, "session_id", None):
                _clear_clarify_pending(agent.session_id)
        except Exception:
            pass

        _cancel_session_id = getattr(agent, 'session_id', None)
    else:
        _cancel_session_id = None

    # Signal that a cancel is in progress for this session.  New
    # /api/chat/start requests arriving mid-cancel will spin-wait on this
    # event instead of getting a 409.
    if _cancel_session_id:
        _cancel_done = threading.Event()
        _CANCEL_EVENTS[_cancel_session_id] = _cancel_done

    # ── Step 4: wait for agent thread to exit ────────────────────────────
    # The agent's finally block removes stream_id from AGENT_INSTANCES.
    # Once that happens s.messages is final (updated inside _agent_lock
    # before the finally runs) and it is safe to read / modify it.
    if agent:
        deadline = time.time() + 10.0
        while time.time() < deadline:
            with STREAMS_LOCK:
                if stream_id not in AGENT_INSTANCES:
                    break
            time.sleep(0.1)

    # ── Step 5: stream cleanup + session cleanup ─────────────────────────
    # Put a cancel event into the stream queue for the SSE client.
    q = stream_manager.get_stream_by_id(stream_id)
    if q:
        try:
            q.put('cancel', {'message': 'Cancelled by user'})
        except Exception:
            pass
    # Find session_id for this stream to end it properly
    with STREAMS_LOCK:
        CANCEL_FLAGS.pop(stream_id, None)
        AGENT_INSTANCES.pop(stream_id, None)

    if _cancel_session_id:
        try:
            with _get_session_agent_lock(_cancel_session_id):
                # Evict cached agent so the next stream creates a fresh instance.
                try:
                    from api.config import SESSION_AGENT_CACHE, SESSION_AGENT_CACHE_LOCK
                    with SESSION_AGENT_CACHE_LOCK:
                        SESSION_AGENT_CACHE.pop(_cancel_session_id, None)
                except Exception:
                    pass
                try:
                    _cs = get_session(_cancel_session_id)
                    _pre_roles = [m.get('role', '?') for m in (_cs.messages or [])]
                    _pre_user_texts = [
                        str(m.get('content', ''))[:60] for m in (_cs.messages or [])
                        if m.get('role') == 'user'
                    ]
                    logger.debug(
                        "[dedup-diagnostic] cancel_stream session cleanup | "
                        "session=%s | msgs=%d | roles=%s",
                        _cancel_session_id, len(_cs.messages or []), _pre_roles,
                    )
                    print(
                        f'[dedup] CANCEL_CLEANUP session={_cancel_session_id} '
                        f'msgs={len(_cs.messages or [])} roles={_pre_roles} '
                        f'user_texts={_pre_user_texts}',
                        flush=True,
                    )

                    _prev_count = len(_cs.messages or [])

                    # ── Reload messages from state.db ──────────────────
                    # The SDK has already flushed the partial turn to DB.
                    # Reload to get the complete picture (old messages +
                    # partial user/assistant/tool messages) instead of
                    # appending [User interrupted] which loses the partial
                    # result and violates the "webui never writes message
                    # rows" principle.
                    try:
                        from api.models import _get_session_db
                        _db = _get_session_db()
                        if _db is not None:
                            _db_messages = _db.get_messages(_cs.session_id)
                            _cs.messages = _db_messages
                    except Exception:
                        pass

                    # ── Insert cancel separator unconditionally ────────────
                    # Always append [User interrupted] on cancel so the DB
                    # carries a durable record that a turn was interrupted.
                    # This also ensures role alternation — without it a
                    # dangling user message would produce consecutive users
                    # on the next turn, causing the SDK to merge them and
                    # break _persist_user_message_idx tracking.
                    _now = time.time()
                    _sep = {
                        'role': 'assistant',
                        'content': '[User interrupted]',
                        'timestamp': int(_now),
                        'timestamp__webui': _now,
                    }
                    _cs.messages.append(_sep)
                    # NOTE: sole exception to the webui-no-message-writes rule.
                    _db.append_message(
                        session_id=_cs.session_id,
                        role='assistant',
                        content='[User interrupted]',
                    )

                    # ── Restore original_content__webui ─────────────────
                    # The streaming thread attached this to _agent_msgs but
                    # the cancel guard skipped s.messages = _agent_msgs.
                    # Apply it to the reloaded user message here so
                    # sync_webui_metadata backfills it to state.db.
                    _pending_original = getattr(_cs, '_pending_original_message', None)
                    if _pending_original:
                        for _m in reversed(_cs.messages):
                            if isinstance(_m, dict) and _m.get('role') == 'user':
                                _m['original_content__webui'] = _pending_original
                                _cs._pending_original_message = None
                                break

                    # ── Post-process reloaded messages ─────────────────
                    # XML strip and reasoning distribution run globally
                    # (DB stores raw content).  Timestamp stamping uses
                    # the real-time timestamps collected during streaming
                    # (saved to s._all_message_ts above).
                    _postprocess_messages(
                        _cs.messages,
                        start_idx=_prev_count,
                        all_message_ts=getattr(_cs, '_all_message_ts', None),
                    )

                    _cs.pending_user_message = None
                    _cs.pending_attachments = []
                    _cs.pending_started_at = None

                    _cs.sync_webui_metadata()

                    _post_roles = [m.get('role', '?') for m in _cs.messages]
                    _post_user_texts = [
                        str(m.get('content', ''))[:60] for m in _cs.messages
                        if m.get('role') == 'user'
                    ]
                    logger.debug(
                        "[dedup-diagnostic] cancel_stream AFTER | session=%s | "
                        "msgs=%d | roles=%s | user_texts=%s",
                        _cancel_session_id, len(_cs.messages),
                        _post_roles, _post_user_texts,
                    )
                    print(
                        f'[dedup] CANCEL_AFTER session={_cancel_session_id} '
                        f'msgs={len(_cs.messages)} roles={_post_roles} '
                        f'user_texts={_post_user_texts}',
                        flush=True,
                    )
                except Exception:
                    logger.debug(
                        "Failed to clear session state on cancel for %s",
                        _cancel_session_id,
                    )
        finally:
            if _cancel_session_id:
                _done = _CANCEL_EVENTS.pop(_cancel_session_id, None)
                if _done:
                    _done.set()

    _dequeue_next(_cancel_session_id)
    return True
