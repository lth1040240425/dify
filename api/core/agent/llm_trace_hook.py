import json
import logging
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)

_MAX_PENDING_EVENTS = 100
_TRACE_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dify-llm-trace")
_PENDING_EVENTS = threading.BoundedSemaphore(_MAX_PENDING_EVENTS)


def emit_llm_trace(
    *,
    event: str,
    runner: Any,
    prompt_messages: list[Any],
    model_parameters: dict[str, Any] | None,
    tools: list[Any] | None,
    stop: list[str] | None,
    iteration_step: int,
    error: Exception | None = None,
    response: Any = None,
    usage: Any = None,
    tool_calls: Any = None,
) -> None:
    """Queue an optional Agent LLM trace without delaying the user request."""
    if os.getenv("LLM_TRACE_ENABLED", "false").lower() != "true":
        return

    bridge_url = os.getenv("LLM_TRACE_BRIDGE_URL", "").strip()
    if not bridge_url:
        return

    try:
        payload = _build_payload(
            event=event,
            runner=runner,
            prompt_messages=prompt_messages,
            model_parameters=model_parameters,
            tools=tools,
            stop=stop,
            iteration_step=iteration_step,
            error=error,
            response=response,
            usage=usage,
            tool_calls=tool_calls,
        )
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        timeout = _positive_float(os.getenv("LLM_TRACE_TIMEOUT", "1"), 1.0)
    except Exception as exc:
        logger.warning("Failed to prepare LLM trace hook (%s)", type(exc).__name__)
        return

    if not _PENDING_EVENTS.acquire(blocking=False):
        logger.warning("Dropped LLM trace hook because the pending queue is full")
        return

    try:
        future = _TRACE_EXECUTOR.submit(_post_trace, bridge_url, data, timeout)
        future.add_done_callback(_release_pending_event)
    except Exception as exc:
        _PENDING_EVENTS.release()
        logger.warning("Failed to queue LLM trace hook (%s)", type(exc).__name__)


def _post_trace(bridge_url: str, data: bytes, timeout: float) -> None:
    try:
        request = urllib.request.Request(
            bridge_url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "dify-agentchat-llm-trace/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout):
            pass
    except Exception as exc:
        logger.warning("Failed to emit LLM trace hook (%s)", type(exc).__name__)


def _release_pending_event(_: Any) -> None:
    _PENDING_EVENTS.release()


def _build_payload(
    *,
    event: str,
    runner: Any,
    prompt_messages: list[Any],
    model_parameters: dict[str, Any] | None,
    tools: list[Any] | None,
    stop: list[str] | None,
    iteration_step: int,
    error: Exception | None,
    response: Any,
    usage: Any,
    tool_calls: Any,
) -> dict[str, Any]:
    app_generate_entity = getattr(runner, "application_generate_entity", None)
    model_instance = getattr(runner, "model_instance", None)
    app_config = getattr(runner, "app_config", None)
    conversation = getattr(runner, "conversation", None)
    message = getattr(runner, "message", None)
    include_content = os.getenv("LLM_TRACE_INCLUDE_CONTENT", "false").lower() == "true"

    return {
        "event": event,
        "emitted_at": int(time.time() * 1000),
        "app_id": _safe_str(getattr(app_config, "app_id", None)),
        "tenant_id": _safe_str(getattr(runner, "tenant_id", None)),
        "conversation_id": _safe_str(getattr(conversation, "id", None)),
        "message_id": _safe_str(getattr(message, "id", None)),
        "user_id": _safe_str(getattr(runner, "user_id", None)),
        "invoke_from": _safe_str(getattr(app_generate_entity, "invoke_from", None)),
        "agent_strategy": _safe_str(getattr(getattr(runner, "config", None), "strategy", None)),
        "iteration_step": iteration_step,
        "provider": _safe_str(getattr(getattr(model_instance, "provider_model_bundle", None), "provider", None)),
        "model": _safe_str(getattr(model_instance, "model_name", None)),
        "model_parameters": _safe_jsonable(model_parameters or {}),
        "stop": _safe_jsonable(stop or []),
        "tool_count": len(tools or []),
        "tools": _summarize_tools(tools or []),
        "prompt": _summarize_prompt_messages(prompt_messages, include_content),
        "response": _summarize_response(response, include_content),
        "usage": _summarize_usage(usage),
        "tool_calls": _summarize_tool_calls(tool_calls, include_content),
        "error": _summarize_error(error, include_content),
    }


def _summarize_prompt_messages(prompt_messages: list[Any], include_content: bool) -> dict[str, Any]:
    messages = []
    total_chars = 0
    text_parts = []

    for message in prompt_messages or []:
        role = _safe_str(getattr(message, "role", message.__class__.__name__))
        text = _message_text(message)
        total_chars += len(text)
        item = {"role": role, "type": message.__class__.__name__, "text_chars": len(text)}
        if include_content:
            item["text"] = text
            text_parts.append(f"[{role}] {text}")
        messages.append(item)

    return {
        "message_count": len(prompt_messages or []),
        "text_chars": total_chars,
        "text": "\n\n".join(text_parts) if include_content else None,
        "messages": messages,
    }


def _message_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_safe_str(getattr(item, "data", None) or getattr(item, "text", None) or item) for item in content)
    return _safe_str(content)


def _summarize_response(response: Any, include_content: bool) -> dict[str, Any]:
    text = _safe_str(response)
    return {"text_chars": len(text), "text": text if include_content else None}


def _summarize_usage(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None

    fields = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_unit_price",
        "prompt_price_unit",
        "prompt_price",
        "completion_unit_price",
        "completion_price_unit",
        "completion_price",
        "total_price",
        "currency",
        "latency",
    )
    summarized = {field: _safe_jsonable(getattr(usage, field)) for field in fields if hasattr(usage, field)}
    if summarized:
        return summarized
    if hasattr(usage, "model_dump"):
        return _safe_jsonable(usage.model_dump())
    if isinstance(usage, dict):
        return _safe_jsonable(usage)
    return {"raw": _safe_str(usage)}


def _summarize_tools(tools: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "name": _safe_str(getattr(tool, "name", None) or getattr(tool, "function", None) or tool),
            "type": tool.__class__.__name__,
        }
        for tool in tools
    ]


def _summarize_tool_calls(tool_calls: Any, include_content: bool) -> Any:
    if include_content:
        return _safe_jsonable(tool_calls or [])

    calls = tool_calls or []
    if not isinstance(calls, list):
        calls = [calls]
    return [
        {
            "name": _safe_str(
                getattr(call, "action_name", None)
                or getattr(call, "name", None)
                or (call[1] if isinstance(call, tuple) and len(call) > 1 else "")
            ),
            "type": call.__class__.__name__,
        }
        for call in calls
    ]


def _summarize_error(error: Exception | None, include_content: bool) -> dict[str, str] | None:
    if error is None:
        return None
    result = {"type": error.__class__.__name__}
    if include_content:
        result["message"] = str(error)
    return result


def _positive_float(value: str, default: float) -> float:
    try:
        return max(float(value), 0.01)
    except ValueError:
        return default


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def _safe_jsonable(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return _safe_str(value)
