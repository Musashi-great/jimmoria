from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from crypto_research_agents.connectors.base import failed, missing_input, success
from crypto_research_agents.connectors.supervisor_tools import assign_task
from crypto_research_agents.connectors.url_fetcher import fetch_url
from crypto_research_agents.core.scheduler import CronRegistry
from crypto_research_agents.core.time import utc_now


PROJECT_ROOT = Path(__file__).resolve().parents[2]
READ_ROOTS = [PROJECT_ROOT]
WRITE_ROOTS = [
    PROJECT_ROOT / "data",
    PROJECT_ROOT / "reports",
    PROJECT_ROOT / "vault",
]
SENSITIVE_PATH_PARTS = {".git", ".venv", "venv", "__pycache__"}
SENSITIVE_NAME_HINTS = {
    ".env",
    "model_settings",
    "company_settings",
    "oauth",
    "access_token",
    "refresh_token",
    "bearer_token",
    "secret",
    "api_key",
    "private_key",
    "seed",
}


def skill_view(skill_id: str | None = None, *, name: str | None = None) -> dict[str, Any]:
    selected = (skill_id or name or "").strip()
    if not selected:
        return missing_input("skill_view", "skill_id or name is required")
    normalized = selected.lower().replace("_", "-")
    if normalized in {"early-token-discovery", "early-token", "web3-project-diligence"}:
        return _read_known_skill(
            "skill_view",
            "representative_web3_project_diligence",
            PROJECT_ROOT / "research_playbooks" / "representative_web3_project_diligence.md",
        )
    if normalized == "xurl":
        return success(
            "skill_view",
            {
                "skill_id": "xurl",
                "status": "mapped",
                "mapped_tools": [
                    "x_search_posts",
                    "x_get_user_timeline",
                    "x_build_kol_list",
                    "web_search",
                ],
                "note": "X/Twitter live search uses X_BEARER_TOKEN when configured; public web search is the fallback.",
            },
            "xurl bridge loaded",
        )
    candidate = PROJECT_ROOT / "research_playbooks" / f"{normalized}.md"
    if candidate.exists():
        return _read_known_skill("skill_view", normalized, candidate)
    config_skill = PROJECT_ROOT / "config" / "skills" / f"{normalized.replace('-', '_')}.yaml"
    if config_skill.exists():
        return _read_known_skill("skill_view", normalized, config_skill)
    return failed("skill_view", f"skill not found: {selected}", {"known": ["early-token-discovery", "xurl"]})


def read_file(path: str | None = None, *, max_chars: int = 12000) -> dict[str, Any]:
    target = _safe_path(path, roots=READ_ROOTS, purpose="read_file")
    if isinstance(target, dict):
        return target
    if not target.exists() or not target.is_file():
        return failed("read_file", f"file not found: {target}", {"path": str(target)})
    text = target.read_text(encoding="utf-8", errors="replace")
    capped = max(1, min(max_chars, 50000))
    return success(
        "read_file",
        {
            "path": str(target),
            "text": text[:capped],
            "truncated": len(text) > capped,
            "chars": len(text),
        },
        "file read",
    )


def search_files(
    query: str | None = None,
    *,
    root: str | None = None,
    glob: str = "*",
    limit: int = 50,
) -> dict[str, Any]:
    if not query:
        return missing_input("search_files", "query is required")
    target_root = _safe_path(root or ".", roots=READ_ROOTS, purpose="search_files")
    if isinstance(target_root, dict):
        return target_root
    if not target_root.exists() or not target_root.is_dir():
        return failed("search_files", f"directory not found: {target_root}", {"root": str(target_root)})
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results: list[dict[str, Any]] = []
    capped_limit = max(1, min(limit, 200))
    for path in target_root.rglob(glob):
        if len(results) >= capped_limit:
            break
        if not path.is_file() or _is_sensitive_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = pattern.search(text) or pattern.search(str(path.relative_to(PROJECT_ROOT)))
        if not match:
            continue
        line_no, excerpt = _match_excerpt(text, pattern)
        results.append(
            {
                "path": str(path),
                "line": line_no,
                "excerpt": excerpt,
            }
        )
    return success("search_files", {"query": query, "root": str(target_root), "results": results}, "files searched")


def write_file(path: str | None = None, *, content: str | None = None, append: bool = False) -> dict[str, Any]:
    if content is None:
        return missing_input("write_file", "content is required")
    target = _safe_path(path, roots=WRITE_ROOTS, purpose="write_file")
    if isinstance(target, dict):
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if append:
        existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        target.write_text(existing + content, encoding="utf-8")
    else:
        target.write_text(content, encoding="utf-8")
    return success("write_file", {"path": str(target), "bytes": len(content.encode("utf-8"))}, "file written")


def browser_navigate(url: str | None = None, *, timeout: int = 20) -> dict[str, Any]:
    if not url:
        return missing_input("browser_navigate", "url is required")
    fetched = fetch_url(url, timeout=timeout)
    fetched["tool"] = "browser_navigate"
    return fetched


def browser_console(url: str | None = None, *, max_chars: int = 12000) -> dict[str, Any]:
    if not url:
        return missing_input("browser_console", "url is required")
    fetched = fetch_url(url)
    data = fetched.get("data") if isinstance(fetched.get("data"), dict) else {}
    if fetched.get("status") != "success":
        fetched["tool"] = "browser_console"
        return fetched
    text = str(data.get("text") or "")
    links = data.get("links") if isinstance(data.get("links"), list) else []
    return success(
        "browser_console",
        {
            "url": data.get("final_url") or url,
            "document_body_innerText": text[:max_chars],
            "links": links[:200],
            "official_links": data.get("official_links") or {},
            "truncated": len(text) > max_chars,
        },
        "document text and links extracted",
    )


def browser_snapshot(url: str | None = None, *, max_chars: int = 6000) -> dict[str, Any]:
    if not url:
        return missing_input("browser_snapshot", "url is required")
    console = browser_console(url, max_chars=max_chars)
    if console.get("status") != "success":
        console["tool"] = "browser_snapshot"
        return console
    data = dict(console.get("data") or {})
    return success("browser_snapshot", data, "browser snapshot captured")


def browser_scroll(url: str | None = None, *, direction: str = "down", max_chars: int = 6000) -> dict[str, Any]:
    if not url:
        return missing_input("browser_scroll", "url is required because JIMMORIA uses stateless browser fetches")
    result = browser_snapshot(url, max_chars=max_chars)
    result["tool"] = "browser_scroll"
    result["message"] = f"stateless browser scroll approximated by refetching URL ({direction})"
    return result


def browser_click(url: str | None = None, *, link_url: str | None = None, max_chars: int = 6000) -> dict[str, Any]:
    target_url = link_url or url
    if not target_url:
        return missing_input("browser_click", "url or link_url is required")
    result = browser_snapshot(target_url, max_chars=max_chars)
    result["tool"] = "browser_click"
    result["message"] = "stateless browser click approximated by navigating to link_url"
    return result


def browser_vision(url: str | None = None, *, image_path: str | None = None) -> dict[str, Any]:
    return failed(
        "browser_vision",
        "browser vision is not configured in the read-only public web runtime",
        {"url": url, "image_path": image_path, "status": "external_connector_required"},
    )


def vision_analyze(image_path: str | None = None, *, prompt: str | None = None) -> dict[str, Any]:
    return failed(
        "vision_analyze",
        "vision analysis requires a dedicated vision connector and is not enabled for agent runtime yet",
        {"image_path": image_path, "prompt": prompt, "status": "external_connector_required"},
    )


def terminal(command: str | None = None, *, purpose: str | None = None) -> dict[str, Any]:
    return failed(
        "terminal",
        "arbitrary terminal execution is disabled for agents; use specific read-only connectors instead",
        {
            "command": command,
            "purpose": purpose,
            "allowed_replacements": [
                "web_search",
                "dexscreener_search_pairs",
                "coingecko_coin_metadata",
                "read_github_repo",
                "rpc_read_contract",
                "explorer_lookup",
            ],
        },
    )


def execute_code(operation: str | None = None, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not operation:
        return missing_input("execute_code", "operation is required")
    payload = payload or {}
    if operation == "timestamp_kst":
        return success("execute_code", {"utc": utc_now(), "timezone": "Asia/Seoul"}, "timestamp generated")
    if operation == "score_sum":
        components = payload.get("components") if isinstance(payload.get("components"), dict) else {}
        numeric = {key: value for key, value in components.items() if isinstance(value, (int, float))}
        return success("execute_code", {"score": sum(numeric.values()), "components": numeric}, "score calculated")
    if operation == "json_summary":
        return success(
            "execute_code",
            {
                "type": type(payload).__name__,
                "keys": sorted(payload)[:50],
                "size": len(json.dumps(payload, ensure_ascii=False)),
            },
            "json summarized",
        )
    return failed(
        "execute_code",
        "arbitrary code execution is disabled; use supported operations only",
        {"supported_operations": ["timestamp_kst", "score_sum", "json_summary"]},
    )


def delegate_task(
    *,
    research_room_id: str,
    task_id: str,
    assigned_agent_id: str,
    objective: str = "",
    expected_output: str = "",
    priority: str = "normal",
) -> dict[str, Any]:
    result = assign_task(
        research_room_id=research_room_id,
        task_id=task_id,
        assigned_agent_id=assigned_agent_id,
        objective=objective,
        expected_output=expected_output,
        priority=priority,
    )
    result["tool"] = "delegate_task"
    return result


def cronjob(action: str | None = None, *, job_id: str | None = None, signal: dict[str, Any] | None = None) -> dict[str, Any]:
    registry = CronRegistry.load()
    selected_action = action or "list"
    if selected_action == "list":
        return success("cronjob", {"jobs": [job.to_dict() for job in registry.list_jobs()]}, "cron jobs listed")
    if selected_action == "run":
        if not job_id:
            return missing_input("cronjob", "job_id is required for action=run")
        return success("cronjob", registry.run_job(job_id, signal=signal).to_dict(), "cron job evaluated")
    return failed("cronjob", f"unsupported action: {selected_action}", {"supported_actions": ["list", "run"]})


def send_message(channel: str | None = None, *, message: str | None = None) -> dict[str, Any]:
    return failed(
        "send_message",
        "external message sending is disabled by default; JIMMORIA returns results in CLI/Web and local reports",
        {"channel": channel, "message_preview": (message or "")[:200]},
    )


def multi_tool_parallel(tasks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return success(
        "multi_tool_use.parallel",
        {
            "tasks": tasks or [],
            "note": "JIMMORIA records parallel intent here; full parallel research_swarm execution is handled by the workflow/concurrency layer.",
        },
        "parallel tool intent recorded",
    )


def _read_known_skill(tool: str, skill_id: str, path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return success(
        tool,
        {"skill_id": skill_id, "path": str(path), "content": text},
        "skill loaded",
    )


def _safe_path(path: str | None, *, roots: list[Path], purpose: str) -> Path | dict[str, Any]:
    if not path:
        return missing_input(purpose, "path is required")
    raw = Path(path)
    target = (PROJECT_ROOT / raw if not raw.is_absolute() else raw).resolve()
    if _is_sensitive_path(target):
        return failed(purpose, f"sensitive path is blocked: {target}", {"path": str(target)})
    allowed = any(target.is_relative_to(root.resolve()) for root in roots)
    if not allowed:
        return failed(purpose, f"path is outside allowed roots: {target}", {"allowed_roots": [str(root) for root in roots]})
    return target


def _is_sensitive_path(path: Path) -> bool:
    lowered_parts = {part.lower() for part in path.parts}
    if lowered_parts.intersection(SENSITIVE_PATH_PARTS):
        return True
    lowered_name = path.name.lower()
    return any(hint in lowered_name for hint in SENSITIVE_NAME_HINTS)


def _match_excerpt(text: str, pattern: re.Pattern[str]) -> tuple[int, str]:
    for index, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return index, line.strip()[:300]
    return 0, ""
