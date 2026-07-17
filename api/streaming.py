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
from api.metering import meter

# Global lock for os.environ writes. Per-session locks (_agent_lock) prevent
# concurrent runs of the SAME session, but two DIFFERENT sessions can still
# interleave their os.environ writes. This global lock serializes the env
# save/restore around the entire agent run.
_ENV_LOCK = threading.Lock()
_REASONING_SEP_MARK = "<|reasoning_sep|>"

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
# metadata added by the webui and must be stripped before the API call.
_API_SAFE_MSG_KEYS = {'role', 'content', 'tool_calls', 'tool_call_id', 'name', 'refusal', 'reasoning'}


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


def _run_background_title_update(session_id: str, user_text: str, assistant_text: str, placeholder_title: str, put_event, agent=None):
    """Generate and publish a better title after `done`, then end the stream."""
    try:
        try:
            s = get_session(session_id)
        except KeyError:
            _put_title_status(put_event, session_id, 'skipped', 'missing_session')
            return
        # Allow self-heal when a previously generated title leaked thinking text.
        _invalid_existing = _looks_invalid_generated_title(s.title)
        if getattr(s, 'llm_title_generated', False) and not _invalid_existing:
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
                    s.save(touch_updated_at=False)
                    effective_title = s.title
                    wrote_title = True

        if wrote_title:
            if source == 'fallback':
                _put_title_status(put_event, session_id, source, fallback_reason, effective_title, raw_preview)
            else:
                _put_title_status(put_event, session_id, source, llm_status, effective_title, raw_preview)
            put_event('title', {'session_id': session_id, 'title': effective_title})
        else:
            _put_title_status(put_event, session_id, 'skipped', source or 'unchanged', effective_title, raw_preview)
    finally:
        put_event('stream_end', {'session_id': session_id})


def _run_background_title_refresh(session_id: str, user_text: str, assistant_text: str, current_title: str, put_event, agent=None):
    """Refresh an existing LLM-generated title using the latest exchange text.

    Unlike _run_background_title_update, this does NOT guard on
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
                s.save(touch_updated_at=False)
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
    """Return a deep copy of messages with only API-safe fields.

    The webui stores extra metadata on messages (attachments, timestamp, _ts)
    for display purposes. Some providers (e.g. Z.AI/GLM) reject unknown fields
    instead of ignoring them, causing HTTP 400 errors on subsequent messages.

    Also strips orphaned tool-role messages whose tool_call_id cannot be linked
    to a preceding assistant message with tool_calls. Strictly-conformant providers
    (Mercury-2/Inception, newer OpenAI models) reject histories containing dangling
    tool results with a 400 error: "Message has tool role, but there was no previous
    assistant message with a tool call."
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
        sanitized = {k: v for k, v in msg.items() if k in _API_SAFE_MSG_KEYS}
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
            prev['content'] = prev_c + cur_c
        else:
            merged.append(msg)
    return merged


def _api_safe_message_positions(messages):
    """Return [(original_index, sanitized_message)] for API-safe messages."""
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

    out = []
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get('role')
        if role == 'tool':
            tid = msg.get('tool_call_id') or ''
            if not tid or tid not in valid_tool_call_ids:
                continue
        sanitized = {k: v for k, v in msg.items() if k in _API_SAFE_MSG_KEYS}
        # Strip empty tool_calls — some providers (DeepSeek) reject []
        if isinstance(sanitized.get('tool_calls'), list) and not sanitized['tool_calls']:
            del sanitized['tool_calls']
        if sanitized.get('role'):
            out.append((idx, sanitized))
    return out


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
        # Content is intentionally left intact — <think> tags remain so
        # they participate in API context for subsequent turns.


def _restore_reasoning_metadata(previous_messages, updated_messages):
    """Carry forward display-only metadata lost during API-safe history sanitization.

    The provider-facing history strips WebUI-only fields like `reasoning`. When the
    agent returns its new full message history, prior assistant messages come back
    without that metadata unless we merge it back in by API-history position.

    This also preserves existing timestamps for unchanged historical messages.
    Without that, older turns that come back from the agent without `_ts` /
    `timestamp` can be re-stamped with the current time on every new assistant
    response, making prior messages appear to "move" in time.
    """
    if not previous_messages or not updated_messages:
        return updated_messages
    updated_messages = list(updated_messages)
    prev_safe = _api_safe_message_positions(previous_messages)

    def _safe_projection(msg):
        if not isinstance(msg, dict):
            return None
        proj = {k: v for k, v in msg.items() if k in _API_SAFE_MSG_KEYS and msg.get('role')}
        if isinstance(proj.get('tool_calls'), list) and not proj['tool_calls']:
            del proj['tool_calls']
        return proj

    def _reasoning_only_assistant(msg):
        if not isinstance(msg, dict) or msg.get('role') != 'assistant' or not msg.get('reasoning'):
            return False
        if msg.get('tool_calls'):
            return False
        return not _message_text(msg.get('content'))

    safe_pos = 0
    while safe_pos < len(prev_safe):
        prev_idx, _ = prev_safe[safe_pos]
        prev_msg = previous_messages[prev_idx]
        cur_msg = updated_messages[safe_pos] if safe_pos < len(updated_messages) else None

        if isinstance(prev_msg, dict) and isinstance(cur_msg, dict) and _safe_projection(prev_msg) == _safe_projection(cur_msg):
            if prev_msg.get('role') == 'assistant' and prev_msg.get('reasoning') and not cur_msg.get('reasoning'):
                cur_msg['reasoning'] = prev_msg['reasoning']
            if prev_msg.get('timestamp') and not cur_msg.get('timestamp'):
                cur_msg['timestamp'] = prev_msg['timestamp']
            elif prev_msg.get('_ts') and not cur_msg.get('_ts') and not cur_msg.get('timestamp'):
                cur_msg['_ts'] = prev_msg['_ts']
            # Preserve original_content across sanitization (goal/skill commands)
            if prev_msg.get('original_content') and not cur_msg.get('original_content'):
                cur_msg['original_content'] = prev_msg['original_content']
            safe_pos += 1
            continue

        if _reasoning_only_assistant(prev_msg):
            updated_messages.insert(safe_pos, copy.deepcopy(prev_msg))
            safe_pos += 1
            continue

        safe_pos += 1
    return updated_messages


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


def _run_agent_streaming(session_id, msg_text, model, workspace, stream_id, attachments=None, *, ephemeral=False):
    """Run agent in background thread, writing SSE events to STREAMS[stream_id].

    When ephemeral=True, session mutations are skipped — used by /btw to get
    a streaming answer without persisting to the parent session.
    """
    q = STREAMS.get(stream_id)
    if q is None:
        return
    s = None
    _rt = {}
    old_cwd = None
    old_exec_ask = None
    old_session_key = None
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
        put('phase', {'stage': 'initializing'})
        s.workspace = str(Path(workspace).expanduser().resolve())
        s.model = model

        _agent_lock = _get_session_agent_lock(session_id)
        # TD1: set thread-local env context so concurrent sessions don't clobber globals
        # Check for pre-flight cancel (user cancelled before agent even started)
        if cancel_event.is_set():
            put('cancel', {'message': 'Cancelled before start'})
            return

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

        _set_thread_env(
            TERMINAL_CWD=str(s.workspace),
            HERMES_EXEC_ASK='1',
            HERMES_SESSION_KEY=session_id,
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
            old_session_key = os.environ.get('HERMES_SESSION_KEY')
            old_gateway_session = os.environ.get('HERMES_GATEWAY_SESSION')
            old_hermes_home = os.environ.get('HERMES_HOME')
            old_cdp_url = os.environ.get('BROWSER_CDP_URL')
            old_http_proxy = os.environ.get('HTTP_PROXY')
            old_https_proxy = os.environ.get('HTTPS_PROXY')
            os.environ['TERMINAL_CWD'] = str(s.workspace)
            os.environ['HERMES_EXEC_ASK'] = '1'
            os.environ['HERMES_SESSION_KEY'] = session_id
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
                nonlocal _token_sent, _message_started
                if text is None:
                    return  # end-of-stream sentinel
                # Emit 'message_start' at the beginning of each assistant message
                # This tells the frontend to create a new message row.
                # Boundaries: first token of stream, and first token after tool_complete.
                if not _message_started:
                    _message_started = True
                    put('message_start', {})
                _token_sent = True
                # Accumulate partial text so cancel_stream() can persist it (#893)
                if stream_id in STREAM_PARTIAL_TEXT:
                    STREAM_PARTIAL_TEXT[stream_id] += str(text)
                put('token', {'text': text})
                # Update global throughput meter
                meter().record_token(stream_id, len(STREAM_PARTIAL_TEXT[stream_id]))
                _emit_metering()

            # For streaming chat, invoked when reasoning token is arrived.
            def on_reasoning(text):
                if text is None:
                    return
                # reasoning=none: SDK final response omits reasoning_content,
                # but DeepSeek still emits reasoning_content delta chunks that
                # mirror the assistant text.  Suppress them so no spurious
                # reasoning is pushed via SSE or persisted to the session.
                if _reasoning_config and _reasoning_config.get("enabled") is False:
                    return
                _append_reasoning_text(text)
                put('reasoning', {'text': str(text)})
                # Track reasoning tokens in the meter so TPS reflects all AI output
                meter().record_reasoning(stream_id, len(_reasoning_text))
                _emit_metering()

            # Pre-initialise the activity counter here so on_tool (which
            # closes over it) never captures an unbound name even if this
            # block is reordered later (Issue #765).
            _checkpoint_activity = [0]

            def on_tool(*cb_args, **cb_kwargs):
                nonlocal _message_started
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
                    if reason_text and not (_reasoning_config and _reasoning_config.get("enabled") is False):
                        # Insert separator at cross-source boundary (streaming → post-hoc summary)
                        if _reasoning_text:
                            _reasoning_text += f"\n{_REASONING_SEP_MARK}\n"
                        _append_reasoning_text(reason_text)
                        put('reasoning', {'text': str(reason_text)})
                        meter().record_reasoning(stream_id, len(_reasoning_text))
                        _emit_metering()
                    return

                args_snap = {}
                if isinstance(args, dict):
                    for k, v in list(args.items())[:4]:
                        s2 = str(v)
                        args_snap[k] = s2[:120] + ('...' if len(s2) > 120 else '')

                if event_type in (None, 'tool.started'):
                    # Generate a unique tid for this tool call
                    import time
                    tid = f"tool-{name}-{int(time.time() * 1000)}-{len(_live_tool_calls)}"
                    _live_tool_calls.append({
                        'name': name,
                        'tid': tid,  # Store tid for later matching in tool.completed
                        'args': args if isinstance(args, dict) else {},
                    })
                    put('tool', {
                        'event_type': event_type or 'tool.started',
                        'name': name,
                        'tid': tid,  # Include tid in the event for frontend
                        'preview': preview,
                        'args': args_snap,
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
                    # Reset message_started so the next token creates a new message row
                    _message_started = False
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
            if 'gateway_session_key' in _agent_params:
                _agent_kwargs['gateway_session_key'] = session_id

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
                else:
                    agent = _AIAgent(**_agent_kwargs)
                    with SESSION_AGENT_CACHE_LOCK:
                        SESSION_AGENT_CACHE[session_id] = (agent, _agent_sig)
                    logger.debug('[webui] Created new agent for session %s', session_id)

            # Store agent instance for cancel/interrupt propagation
            with STREAMS_LOCK:
                AGENT_INSTANCES[stream_id] = agent
                # Check if cancel was requested during agent initialization
                if stream_id in CANCEL_FLAGS and CANCEL_FLAGS[stream_id].is_set():
                    # Cancel arrived during agent creation - interrupt immediately
                    try:
                        agent.interrupt("Cancelled before start")
                    except Exception:
                        logger.debug("Failed to interrupt agent before start")
                    put('cancel', {'message': 'Cancelled by user'})
                    return

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
            # The final s.save() at task completion handles the full session update + index.
            # (_checkpoint_stop is pre-initialised at the top of the outer try.)
            # (_checkpoint_activity is already initialised before on_tool().)

            def _periodic_checkpoint():
                last_saved_activity = 0
                while not _checkpoint_stop.wait(15):
                    try:
                        cur = _checkpoint_activity[0]
                        if cur > last_saved_activity:
                            with _agent_lock:
                                s.save(skip_index=True)
                            last_saved_activity = cur
                    except Exception as e:
                        logger.debug("Periodic checkpoint save failed: %s", e)

            _checkpoint_stop = threading.Event()
            # Persist the user message BEFORE streaming starts so it's durable even if
            # the server crashes before the first checkpoint fires (every 15s).
            with _agent_lock:
                s.save(touch_updated_at=True, skip_index=False)

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

            result = agent.run_conversation(
                user_message=workspace_ctx + msg_text,
                system_message=workspace_system_msg,
                conversation_history=_sanitize_messages_for_api(s.messages),
                task_id=session_id,
                persist_user_message=msg_text,
            )
            # ── Skill command: attach original_content to user message for clean display ──
            _pending_original = getattr(s, '_pending_original_message', None)
            if _pending_original:
                s._pending_original_message = None
                for _m in result.get('messages') or []:
                    if isinstance(_m, dict) and _m.get('role') == 'user':
                        _m['original_content'] = _pending_original
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
                # Copy it to reasoning (WebUI display field) BEFORE
                # _restore_reasoning_metadata — which sanitizes through
                # _API_SAFE_MSG_KEYS and would drop reasoning_content otherwise.
                _agent_msgs = result.get('messages') or s.messages
                for _m in _agent_msgs:
                    if isinstance(_m, dict) and _m.get('role') == 'assistant':
                        _rc = _m.get('reasoning_content')
                        if isinstance(_rc, str) and _rc.strip() and not _m.get('reasoning'):
                            _m['reasoning'] = _rc.strip()
                s.messages = _restore_reasoning_metadata(
                    _previous_messages,
                    _agent_msgs,
                )
                # Strip XML tool-call blocks from assistant message content.
                # DeepSeek and some other providers emit <function_calls>...</function_calls>
                # in the raw response text; this must be removed before the content is
                # saved to the session and displayed in the chat bubble. (#702)
                for _m in s.messages:
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
                # _token_sent tracks whether on_token() was called (any streamed text)
                if not _assistant_added and not _token_sent:
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
                    # Clear stream/pending state so the session does not appear
                    # "agent_running" on reload after a silent failure.
                    # Persist the error so it survives page reload.
                    # _error=True ensures _sanitize_messages_for_api excludes it from
                    # subsequent API calls so the LLM never sees its own error as prior context.
                    s.active_stream_id = None
                    s.pending_user_message = None
                    s.pending_attachments = []
                    s.pending_started_at = None
                    s.messages.append({
                        'role': 'assistant',
                        'content': f'**{_err_label}:** {_err_str or _err_label}\n\n*{_err_hint}*',
                        'timestamp': int(time.time()),
                        '_error': True,
                    })
                    try:
                        s.save()
                    except Exception:
                        pass
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
                # reference stays alive even after the dict entry is removed.
                # Concurrent readers that already looked up the old ID will still
                # see the same Lock object until they release it.
                _agent_sid = getattr(agent, 'session_id', None)
                _compressed = False
                if _agent_sid and _agent_sid != session_id:
                    old_sid = session_id
                    new_sid = _agent_sid
                    # Rename the session file
                    old_path = SESSION_DIR / f'{old_sid}.json'
                    new_path = SESSION_DIR / f'{new_sid}.json'
                    s.session_id = new_sid
                    with LOCK:
                        if old_sid in SESSIONS:
                            SESSIONS[new_sid] = SESSIONS.pop(old_sid)
                    # Migrate the per-session lock: alias new_sid to the held
                    # _agent_lock reference directly (not via old_sid lookup),
                    # then remove the old_sid entry to prevent a leak.
                    with SESSION_AGENT_LOCKS_LOCK:
                        SESSION_AGENT_LOCKS[new_sid] = _agent_lock
                        SESSION_AGENT_LOCKS.pop(old_sid, None)
                    # Migrate cached agent to the new session ID so the turn
                    # count survives context compression.
                    from api.config import SESSION_AGENT_CACHE, SESSION_AGENT_CACHE_LOCK
                    with SESSION_AGENT_CACHE_LOCK:
                        _cached_entry = SESSION_AGENT_CACHE.pop(old_sid, None)
                        if _cached_entry:
                            SESSION_AGENT_CACHE[new_sid] = _cached_entry
                    if old_path.exists() and not new_path.exists():
                        try:
                            old_path.rename(new_path)
                        except OSError:
                            logger.debug("Failed to rename session file during compression")
                    _compressed = True
                # Also detect compression via the result dict or compressor state
                if not _compressed:
                    _compressor = getattr(agent, 'context_compressor', None)
                    if _compressor and getattr(_compressor, 'compression_count', 0) > 0:
                        _compressed = True
                # Notify the frontend that compression happened
                if _compressed:
                    # Calculate message counts before/after compression
                    _msg_before = len(_previous_messages)
                    _msg_after = len(s.messages)
                    # Rough token estimation (chars / 4 heuristic)
                    _approx_tokens_before = sum(
                        len(str(m.get('content', ''))) for m in _previous_messages
                        if isinstance(m, dict)
                    ) // 4
                    _approx_tokens_after = sum(
                        len(str(m.get('content', ''))) for m in s.messages
                        if isinstance(m, dict)
                    ) // 4
                    s.last_compression_time = time.time()
                    put('compressed', {
                        'message': 'Context auto-compressed to continue the conversation',
                        'saved_messages': max(0, _msg_before - _msg_after),
                        'approx_tokens_before': _approx_tokens_before,
                        'approx_tokens_after': _approx_tokens_after,
                    })

                # Stamp 'timestamp' on any new messages that don't have one yet.
                # Only process messages added in this turn (not in _previous_messages)
                # to avoid overwriting different-time messages with a single shared timestamp.
                _prev_msg_ids = {id(m) for m in _previous_messages}
                for _m in s.messages:
                    if id(_m) not in _prev_msg_ids and isinstance(_m, dict) and not _m.get('timestamp') and not _m.get('_ts'):
                        _m['timestamp'] = int(time.time())
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
                s.active_stream_id = None
                s.pending_user_message = None
                s.pending_attachments = []
                s.pending_started_at = None
                # Tag the matching user message with attachment filenames for display on reload
                # Only tag a user message whose content relates to this turn's text
                # (msg_text is the full message including the [Attached files: ...] suffix)
                if attachments:
                    for m in reversed(s.messages):
                        if m.get('role') == 'user':
                            content = str(m.get('content', ''))
                            # Match if content is part of the sent message or vice-versa
                            base_text = msg_text.split('\n\n[Attached files:')[0].strip() if '\n\n[Attached files:' in msg_text else msg_text
                            if base_text[:60] in content or content[:60] in msg_text:
                                m['attachments'] = attachments
                                break
                # Persist reasoning trace per-message so each assistant message
                # carries only the thinking that led to it, not the whole turn.
                # DeepSeek / QwQ / MiniMax embed thinking as <think> tags inside
                # the content text; extract them and move them into the dedicated
                # 'reasoning' field for structured display and persistence.
                _distribute_inline_reasoning(s.messages)
                # Fallback: any reasoning accumulated via on_reasoning callbacks
                # that was NOT embedded as <think> tags (e.g. reasoning=high mode
                # where delta.reasoning_content is already per-message) still ends
                # up here, but only if not already distributed above.
                if _reasoning_text and s.messages:
                    for _rm in reversed(s.messages):
                        if isinstance(_rm, dict) and _rm.get('role') == 'assistant':
                            if not _rm.get('reasoning'):
                                _rm['reasoning'] = _reasoning_text
                            break
                # ── WebUI-side token-threshold auto-compress (path 3) ──
                # The agent's built-in compression is disabled when
                # auto_compress_token_threshold > 0 (see settings sync in
                # routes.py).  WebUI checks last_prompt_tokens / context_length
                # after each turn and compresses when the ratio exceeds the
                # configured threshold.  This keeps the session_id stable and
                # archives pre-compression state to {sid}_archived/ — consistent
                # with path 1 (message-count auto-compress) behaviour.
                from api.config import load_settings as _load_settings_ctx
                _settings = _load_settings_ctx()
                _token_thresh_str = str(_settings.get('auto_compress_token_threshold', '0') or '0').strip()
                try:
                    _token_thresh_pct = float(_token_thresh_str)
                except (ValueError, TypeError):
                    _token_thresh_pct = 0.0
                if _token_thresh_pct > 0:
                    _cc2 = getattr(agent, 'context_compressor', None)
                    if _cc2:
                        _ctx_len = getattr(_cc2, 'context_length', 0) or 0
                        _last_prompt = getattr(_cc2, 'last_prompt_tokens', 0) or 0
                        _threshold_tokens = int(_ctx_len * _token_thresh_pct)
                        if _ctx_len > 0 and _last_prompt >= _threshold_tokens > 0:
                            _pre_messages = _sanitize_messages_for_api(s.messages)
                            if len(_pre_messages) >= 4:
                                logger.debug(
                                    '[webui] token-threshold compress triggered session=%s '
                                    'prompt_tokens=%d context_length=%d threshold=%.0f%%',
                                    session_id, _last_prompt, _ctx_len, _token_thresh_pct * 100,
                                )
                                try:
                                    _compressed = _cc2.compress(
                                        _pre_messages, current_tokens=_last_prompt,
                                    )
                                except Exception as _cerr:
                                    logger.debug(
                                        'Token-threshold compress failed for '
                                        'session %s: %s', session_id, _cerr,
                                    )
                                    _compressed = None
                                if _compressed and len(_compressed) < len(_pre_messages):
                                    try:
                                        from api.routes import _archive_session_before_compress
                                        _archive_session_before_compress(s)
                                    except Exception:
                                        logger.debug(
                                            'Archive before token-threshold '
                                            'compress failed for session %s',
                                            session_id, exc_info=True,
                                        )
                                    _msg_before = len(_pre_messages)
                                    _msg_after = len(_compressed)
                                    _approx_tokens_after = sum(
                                        len(str(m.get('content', '')))
                                        for m in _compressed
                                        if isinstance(m, dict)
                                    ) // 4
                                    s.messages = _compressed
                                    _distribute_inline_reasoning(s.messages)
                                    s.tool_calls = []
                                    s.last_compression_time = time.time()
                                    tool_calls = []  # also update local for done event
                                    put('compressed', {
                                        'message': 'Context auto-compressed to continue the conversation',
                                        'saved_messages': max(0, _msg_before - _msg_after),
                                        'approx_tokens_before': _last_prompt,
                                        'approx_tokens_after': _approx_tokens_after,
                                    })
                                    logger.debug(
                                        '[webui] token-threshold compress done session=%s '
                                        'messages %d→%d tokens_approx %d→%d',
                                        session_id, _msg_before, _msg_after,
                                        _last_prompt, _approx_tokens_after,
                                    )
                s.save()
            # Sync to state.db for /insights (opt-in setting)
            try:
                from api.config import load_settings as _load_settings
                if _load_settings().get('sync_to_insights'):
                    from api.state_sync import sync_session_usage
                    sync_session_usage(
                        session_id=s.session_id,
                        input_tokens=s.input_tokens or 0,
                        output_tokens=s.output_tokens or 0,
                        estimated_cost=s.estimated_cost,
                        model=model,
                        title=s.title,
                        message_count=len(s.messages),
                    )
            except Exception:
                logger.debug("Failed to sync session to insights")
            usage = {'input_tokens': input_tokens, 'output_tokens': output_tokens, 'estimated_cost': estimated_cost}
            # Include context window data from the agent's compressor for the UI indicator
            _cc = getattr(agent, 'context_compressor', None)
            if _cc:
                usage['context_length'] = getattr(_cc, 'context_length', 0) or 0
                usage['threshold_tokens'] = getattr(_cc, 'threshold_tokens', 0) or 0
                usage['last_prompt_tokens'] = getattr(_cc, 'last_prompt_tokens', 0) or 0
            # (reasoning trace already attached + saved above, before s.save())
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
            put('done', {'session': redact_session_data(raw_session), 'usage': usage})
            # Emit metering stats for the header TPS label
            meter_stats = meter().get_stats()
            meter_stats['session_id'] = session_id
            put('metering', meter_stats)
            if _should_bg_title and _u0 and _a0:
                threading.Thread(
                    target=_run_background_title_update,
                    args=(s.session_id, _u0, _a0, str(s.title or '').strip(), put, agent),
                    daemon=True,
                ).start()
            else:
                # Use the original session_id parameter (never reassigned), not s.session_id
                # which may be rotated during context compression. The client captured
                # activeSid = original session_id so they must match for stream_end to close.
                put('stream_end', {'session_id': session_id})
                # Adaptive title refresh: re-generate title from latest exchange
                # every N exchanges (when enabled in settings). Runs after stream_end
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
                if old_session_key is None: os.environ.pop('HERMES_SESSION_KEY', None)
                else: os.environ['HERMES_SESSION_KEY'] = old_session_key
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
                                            "original_content": _goal_text,
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
                # After cancel_stream (interrupt mode), s.active_stream_id is
                # either None or points to the new stream that replaced us.
                if getattr(s, 'active_stream_id', None) != stream_id:
                    _owned = False
                if _owned:
                    # Persist the error so it survives page reload.
                    # _error=True ensures _sanitize_messages_for_api excludes it from subsequent
                    # API calls so the LLM never sees its own error as prior context on the next turn.
                    s.active_stream_id = None
                    s.pending_user_message = None
                    s.pending_attachments = []
                    s.pending_started_at = None
                    s.messages.append({
                        'role': 'assistant',
                        'content': f'**{_exc_label}:** {err_str}' + (f'\n\n*{_exc_hint}*' if _exc_hint else ''),
                        'timestamp': int(time.time()),
                        '_error': True,
                    })
                    try:
                        s.save()
                    except Exception:
                        pass
        _apperror_payload: dict = {'message': err_str, 'type': _exc_type}
        if _exc_hint:
            _apperror_payload['hint'] = _exc_hint
        put('apperror', _apperror_payload)
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
        with STREAMS_LOCK:
            STREAMS.pop(stream_id, None)
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
    if getattr(s, 'active_stream_id', None):
        with SESSION_MESSAGE_QUEUE_LOCK:
            q = SESSION_MESSAGE_QUEUE.get(session_id)
            if q is None:
                q = []
                SESSION_MESSAGE_QUEUE[session_id] = q
            q.insert(0, payload)
        return
    with STREAMS_LOCK:
        from api.config import STREAMS as _cfg_STREAMS
        _q = ReplayableStream()
        _cfg_STREAMS[stream_id] = _q
    with _get_session_agent_lock(session_id):
        s.active_stream_id = stream_id
        s.pending_user_message = payload.get("message", "")
        s.pending_attachments = payload.get("attachments", [])
        _oc = payload.get("original_content", "")
        if _oc:
            s._pending_original_message = _oc
        s.pending_started_at = time.time()
        s.save()
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
    active_stream_id = getattr(s, "active_stream_id", None) or None
    if not active_stream_id:
        return {"accepted": False, "fallback": "not_running", "stream_id": None}
    with _cfg.STREAMS_LOCK:
        stream_alive = active_stream_id in _cfg.STREAMS
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

    Eagerly releases the session lock (pops STREAMS/CANCEL_FLAGS/AGENT_INSTANCES
    and clears session.active_stream_id) so new /api/chat/start requests succeed
    immediately after cancel, even if the agent thread is still blocked.

    The worker thread's finally block uses .pop(key, None), so the double-pop is
    a safe no-op. Session cleanup runs outside STREAMS_LOCK to preserve lock
    ordering (streaming thread does LOCK → STREAMS_LOCK; inverting would deadlock).
    """
    from api import config as _live_config

    # Use module-level aliases (imported from api.config at startup).
    # In production these are always the same objects as api.config.STREAMS etc.
    # The fallback below handles a hypothetical future case where api.config's
    # state dicts are replaced at runtime (e.g. a future profile-reload path).
    # No production code currently does this; the fallback is defensive only.
    streams = STREAMS
    cancel_flags = CANCEL_FLAGS
    agent_instances = AGENT_INSTANCES
    partial_texts = STREAM_PARTIAL_TEXT
    streams_lock = STREAMS_LOCK
    if stream_id not in streams and getattr(_live_config, 'STREAMS', streams) is not streams:
        streams = _live_config.STREAMS
        cancel_flags = _live_config.CANCEL_FLAGS
        agent_instances = _live_config.AGENT_INSTANCES
        partial_texts = _live_config.STREAM_PARTIAL_TEXT
        streams_lock = _live_config.STREAMS_LOCK

    with streams_lock:
        if stream_id not in streams:
            return False

        # Set WebUI layer cancel flag
        flag = cancel_flags.get(stream_id)
        if flag:
            flag.set()

        # Interrupt the AIAgent instance to stop tool execution
        agent = agent_instances.get(stream_id)
        if agent:
            try:
                agent.interrupt("Cancelled by user")
            except Exception as e:
                # Log but don't block the cancel flow
                import logging
                logging.getLogger(__name__).debug(
                    f"Failed to interrupt agent for stream {stream_id}: {e}"
                )
        else:
            # Agent not yet stored - cancel_event flag will be checked by agent thread
            import logging
            logging.getLogger(__name__).debug(
                f"Cancel requested for stream {stream_id} before agent ready - "
                f"cancel_event flag set, will be checked on agent startup"
            )

        # Clear any pending clarify prompt so the blocked tool call can unwind.
        try:
            from api.clarify import clear_pending as _clear_clarify_pending

            if agent and getattr(agent, "session_id", None):
                _clear_clarify_pending(agent.session_id)
        except Exception:
            logger.debug("Failed to clear clarify prompt during cancel")

        # Put a cancel sentinel into the stream so the SSE handler wakes up and
        # the event is recorded in the replay history for any reconnecting client.
        q = streams.get(stream_id)
        if q:
            try:
                q.put('cancel', {'message': 'Cancelled by user'})
            except Exception:
                logger.debug("Failed to put cancel event to queue")

        # ── Eager session lock release (fixes #653) ──────────────────────────
        # Pop stream state now so the 409 guard in routes.py sees the session
        # as idle and allows new /api/chat/start immediately after cancel,
        # even if the agent thread is still blocked in a C-level syscall.
        # The worker thread's finally block uses .pop(key, None) too, so a
        # double-pop here is safe (no-op).
        streams.pop(stream_id, None)
        cancel_flags.pop(stream_id, None)
        agent_instances.pop(stream_id, None)
        # STREAM_PARTIAL_TEXT is intentionally NOT popped here — the agent thread may
        # still be appending tokens. We capture the snapshot two lines below; the
        # streaming finally block handles the cleanup when the thread exits.

        # Capture partial text and session_id while holding STREAMS_LOCK (avoids a
        # race where the agent thread deallocates the agent object or clears the
        # partial text after we release).
        # Session cleanup (get_session + save) must happen OUTSIDE the lock —
        # get_session() acquires LOCK, and the streaming thread does LOCK first
        # then STREAMS_LOCK, so inverting the order here would cause deadlock.
        _cancel_session_id = getattr(agent, 'session_id', None) if agent else None
        _cancel_partial_text = partial_texts.get(stream_id, '')
        # Fallback: check the live config's partial text map if we used an alias
        # and the text wasn't found in the alias (defensive, matches streams fallback above).
        if not _cancel_partial_text:
            live_partials = getattr(_live_config, 'STREAM_PARTIAL_TEXT', partial_texts)
            if live_partials is not partial_texts:
                _cancel_partial_text = live_partials.get(stream_id, '')

    # Session cleanup outside STREAMS_LOCK to preserve lock ordering.
    # Acquire the per-session _agent_lock too, mirroring every other session
    # writer (streaming success/error paths, periodic checkpoint, POST endpoints)
    # so the cancel-path mutation races neither the checkpoint thread nor
    # concurrent undo/retry calls.
    if _cancel_session_id:
        with _get_session_agent_lock(_cancel_session_id):
            # Evict the cached agent so the next stream for this session
            # (e.g. interrupt mode) creates a fresh instance.  Without this,
            # the new stream reuses the same agent object while the old
            # run_conversation() is still unwinding, producing silent failures
            # ("No response received").
            try:
                from api.config import SESSION_AGENT_CACHE, SESSION_AGENT_CACHE_LOCK
                with SESSION_AGENT_CACHE_LOCK:
                    SESSION_AGENT_CACHE.pop(_cancel_session_id, None)
            except Exception:
                pass
            try:
                _cs = get_session(_cancel_session_id)
                _cs.active_stream_id = None
                _cs.pending_user_message = None
                _cs.pending_attachments = []
                _cs.pending_started_at = None
                # Persist any partial assistant text that was streamed before cancel (#893).
                # Preserving partial content means the user sees what the agent had
                # produced rather than losing it entirely.  The marker is _partial=True
                # (for session/UI identification only) — NOT _error=True — so the partial
                # content IS kept in the history sent to the agent on the next user
                # message, letting the model continue from where it was cut off.
                # See the inner comment on the append call below for the rationale.
                partial_text = _cancel_partial_text.strip() if _cancel_partial_text else ''
                if partial_text:
                    import re as _re
                    # Strip thinking/reasoning markup from partial content before saving.
                    # First pass: remove complete <thinking>...</thinking> blocks.
                    _stripped = _re.sub(r'<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>',
                                        '', partial_text,
                                        flags=_re.DOTALL | _re.IGNORECASE).strip()
                    # Second pass: strip trailing UNCLOSED think/thinking block (the common
                    # cancel case — user stops mid-reasoning before the close tag appears).
                    _stripped = _re.sub(r'<think(?:ing)?\b[^>]*>.*',
                                        '', _stripped,
                                        flags=_re.DOTALL | _re.IGNORECASE).strip()
                    if _stripped:
                        # Mark _partial=True for session/UI identification only.
                        # Deliberately NOT _error=True — the partial content is real model
                        # output and should be visible in conversation history so the model
                        # can continue from it on the next turn (#893).
                        _cs.messages.append({
                            'role': 'assistant',
                            'content': _stripped,
                            '_partial': True,
                            'timestamp': int(time.time()),
                        })
                # Cancel marker — flagged _error=True so it is stripped from conversation
                # history on the next turn (prevents model from seeing "Task cancelled."
                # as a prior assistant reply).
                _cs.messages.append({
                    'role': 'assistant',
                    'content': '*Task cancelled.*',
                    '_error': True,
                    'timestamp': int(time.time()),
                })
                _cs.save()
            except Exception:
                logger.debug("Failed to clear session state on cancel for %s", _cancel_session_id)

    # After cancel, check for queued messages
    _dequeue_next(_cancel_session_id)
    return True
