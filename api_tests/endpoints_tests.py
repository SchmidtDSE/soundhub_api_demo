"""

Smoke test runner for the soundhub api_dock demo.

Endpoints, discovery steps, and target URLs are all defined in a YAML config
file (default: endpoints_config.yaml in the same directory).

Usage:
  python api_tests/endpoints_tests.py                         # default target
  python api_tests/endpoints_tests.py -n deployed             # named target
  python api_tests/endpoints_tests.py -c my_config.yaml       # custom config

Results are written to api_tests/results/endpoints-<target>.md.

License: BSD 3-Clause

"""

#
# IMPORTS
#
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import httpx
import yaml


#
# CONSTANTS
#
INTERESTING_HEADERS: frozenset = frozenset({
    "access-control-allow-origin",
    "cache-control",
    "etag",
    "expires",
    "last-modified",
    "location",
    "vary",
    "x-correlation-id",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-request-id",
})

DEFAULT_CONFIG: str = "endpoints_config.yaml"

GREEN: str = "\033[32m"
RED: str = "\033[31m"
RESET: str = "\033[0m"
YELLOW: str = "\033[33m"
CYAN: str = "\033[36m"


#
# PUBLIC
#
@dataclass
class Result:
    """Captures the outcome of a single HTTP request."""
    label: str
    url: str
    section: str = ""
    status: Optional[int] = None
    content_type: str = ""
    preview: Optional[str] = None
    forwarded_headers: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None
    note: Optional[str] = None


@click.command()
@click.option(
    "--config", "-c",
    default=lambda: str(Path(__file__).parent / DEFAULT_CONFIG),
    show_default=True,
    help="Path to the YAML endpoints config file.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--name", "-n",
    default=None,
    help="Named target from the config (e.g. 'local', 'deployed'). Uses config default if omitted.",
)
@click.option(
    "--out", "-o",
    default=None,
    help="Output markdown path. Defaults to api_tests/results/endpoints-<target>.md.",
)
def main(config: str, name: Optional[str], out: Optional[str]) -> None:
    """Smoke-test the soundhub api_dock demo against a named target.

    All endpoints and targets are defined in the YAML config file.
    Run without --name to use the default target from the config.
    """
    cfg = yaml.safe_load(Path(config).read_text())

    base_url, resolved_name = _resolve_target(cfg, name)

    if out:
        out_path = Path(out)
    else:
        results_dir = Path(__file__).parent / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        out_path = results_dir / f"endpoints-{resolved_name}.md"

    log: List[str] = []

    _log(f"\nTarget : {CYAN}{resolved_name}{RESET} → {base_url}", log)
    _log(f"Config : {config}", log)
    _log(f"Output : {out_path}\n", log)

    global_cookies = _resolve_cookies(cfg.get("cookies", []))

    client = httpx.Client(base_url=base_url, follow_redirects=False, timeout=30)
    results: List[Result] = []

    _log("Discovering IDs from live data...", log)
    ids = _discover_ids(client, cfg.get("discovery", {}), global_cookies, log)
    for k, v in ids.items():
        _log(f"  {CYAN}{k}{RESET} = {v}", log)

    for suite in cfg.get("suites", []):
        _log(f"\nRunning: {suite['name']}...", log)
        _run_suite(client, suite, ids, global_cookies, results, log)

    client.close()

    status_counts = Counter(r.status for r in results if r.status is not None)
    skipped = sum(1 for r in results if r.url == "(skipped)")
    errs = sum(1 for r in results if r.error)

    _log(f"\n{'─' * 60}", log)

    parts = [f"Total: {len(results)}"]
    for code, count in sorted(status_counts.items()):
        color = GREEN if code < 400 else (YELLOW if code < 500 else RED)
        parts.append(f"{color}{code}{RESET}: {count}")
    if skipped:
        parts.append(f"skipped: {skipped}")
    if errs:
        parts.append(f"{YELLOW}conn err{RESET}: {errs}")

    _log("  |  ".join(parts), log)
    _log(f"Results → {out_path}\n", log)

    _write_markdown(results, base_url, out_path, log)
    srv_errs = sum(count for code, count in status_counts.items() if code >= 500)
    sys.exit(srv_errs + errs)


#
# INTERNAL
#
def _log(msg: str, log: List[str]) -> None:
    """Print msg to stdout and append a clean (ANSI-stripped) copy to log."""
    click.echo(msg)
    log.append(re.sub(r"\033\[[0-9;]*m", "", msg))


def _resolve_target(cfg: Dict[str, Any], name: Optional[str]) -> tuple:
    """Return (base_url, resolved_name) for the named target or config default."""
    targets = cfg.get("targets", {})
    if not targets:
        raise click.ClickException("No 'targets' section in config.")

    resolved_name = name or targets.get("default")
    if not resolved_name:
        raise click.ClickException("No --name given and no 'default' set in config targets.")

    url = targets.get(resolved_name)
    if not url:
        available = [k for k in targets if k != "default"]
        raise click.ClickException(f"Target '{resolved_name}' not found. Available: {available}")

    return url.rstrip("/"), resolved_name


def _discover_ids(
    client: httpx.Client,
    discovery: Dict[str, Any],
    cookies: Dict[str, str],
    log: List[str],
) -> Dict[str, Any]:
    """Run discovery steps in YAML order, substituting already-found IDs into paths."""
    ids: Dict[str, Any] = {}

    for id_name, step in discovery.items():
        requires = step.get("requires", [])
        missing = [r for r in requires if ids.get(r) is None]
        if missing:
            _log(f"  {YELLOW}SKIP{RESET}  discover {id_name} — missing: {missing}", log)
            ids[id_name] = None
            continue

        path = _substitute(step["path"], ids)
        params = {k: _substitute(str(v), ids) for k, v in step.get("params", {}).items()}
        field_name = step.get("field", "id")

        try:
            r = client.get(path, params=params, cookies=cookies, timeout=15)
            ids[id_name] = _extract_first_field(r.json(), field_name)
        except Exception:
            ids[id_name] = None

    return ids


def _run_suite(
    client: httpx.Client,
    suite: Dict[str, Any],
    ids: Dict[str, Any],
    global_cookies: Dict[str, str],
    results: List[Result],
    log: List[str],
) -> None:
    """Run all tests in a suite, substituting IDs and skipping where required IDs are absent."""
    section = suite["name"]
    cookies = {**global_cookies, **_resolve_cookies(suite.get("cookies", []))}

    for test in suite.get("tests", []):
        requires = test.get("requires", [])
        missing = [r for r in requires if ids.get(r) is None]
        if missing:
            label = _substitute(test.get("label", test["path"]), ids)
            _skip(results, label, f"missing IDs: {missing}", section=section, log=log)
            continue

        path = _substitute(test["path"], ids)
        label = _substitute(test.get("label", test["path"]), ids)
        params = {k: _substitute(str(v), ids) for k, v in test.get("params", {}).items()}

        _hit(client, results, label, path, params or None, test.get("note"), section=section, cookies=cookies, log=log)


def _hit(
    client: httpx.Client,
    results: List[Result],
    label: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    note: Optional[str] = None,
    section: str = "",
    cookies: Optional[Dict[str, str]] = None,
    log: Optional[List[str]] = None,
) -> Optional[httpx.Response]:
    """Make a GET request, append a Result, and print progress."""
    full_url = str(client.base_url).rstrip("/") + path
    if params:
        full_url += "?" + "&".join(f"{k}={v}" for k, v in params.items())

    try:
        resp = client.get(path, params=params, cookies=cookies or {})
    except httpx.RequestError as exc:
        results.append(Result(label=label, url=full_url, section=section, error=str(exc), note=note))
        _log(f"  {RED}ERR{RESET}  {label}", log or [])
        return None

    ct = resp.headers.get("content-type", "")
    results.append(Result(
        label=label,
        url=full_url,
        section=section,
        status=resp.status_code,
        content_type=ct,
        preview=_build_preview(resp, ct),
        forwarded_headers={k: v for k, v in resp.headers.items() if k.lower() in INTERESTING_HEADERS},
        note=note,
    ))

    color = GREEN if resp.status_code < 400 else (YELLOW if resp.status_code < 500 else RED)
    _log(f"  {color}{resp.status_code}{RESET}  {label}", log or [])
    return resp


def _resolve_cookies(cookie_list: List[Dict[str, str]]) -> Dict[str, str]:
    """Resolve a cookies list from config, expanding env: prefixed values from the environment."""
    result = {}
    for entry in cookie_list:
        key = entry.get("key", "")
        value = entry.get("value", "")
        if value.startswith("env:"):
            value = os.environ.get(value[4:], "")
        if key and value:
            result[key] = value
    return result


def _substitute(template: str, ids: Dict[str, Any]) -> str:
    """Replace {var_name} placeholders with discovered ID values."""
    result = template
    for key, value in ids.items():
        if value is not None:
            result = result.replace(f"{{{key}}}", str(value))
    return result


def _extract_first_field(body: Any, field_name: str) -> Optional[Any]:
    """Pull a named field from the first element of a list or a dict response."""
    if isinstance(body, list) and body:
        row = body[0]
        return row.get(field_name) if isinstance(row, dict) else None
    if isinstance(body, dict):
        if field_name in body:
            return body[field_name]
        for key in ("results", "data"):
            rows = body.get(key, [])
            if rows and isinstance(rows, list) and isinstance(rows[0], dict):
                return rows[0].get(field_name)
    return None


def _skip(
    results: List[Result],
    label: str,
    reason: str,
    section: str = "",
    log: Optional[List[str]] = None,
) -> None:
    results.append(Result(label=label, url="(skipped)", section=section, note=reason))
    _log(f"  {YELLOW}SKIP{RESET}  {label} — {reason}", log or [])


def _build_preview(resp: httpx.Response, content_type: str) -> Optional[str]:
    """Return a ≤1000-char JSON preview: first element if list, full dict otherwise."""
    if "application/json" not in content_type and "application/geo+json" not in content_type:
        return None
    try:
        body = resp.json()
        target = body[0] if isinstance(body, list) and body else body
        text = json.dumps(target, indent=2)
        return text[:1000] + ("…" if len(text) > 1000 else "")
    except Exception:
        return resp.text[:500]


def _write_markdown(results: List[Result], base_url: str, out_path: Path, log: List[str]) -> None:
    """Write all results to a markdown file grouped by suite section."""
    status_counts = Counter(r.status for r in results if r.status is not None)
    skipped = sum(1 for r in results if r.url == "(skipped)")
    errs = sum(1 for r in results if r.error)

    status_headers = [str(code) for code in sorted(status_counts)]
    status_values = [str(status_counts[int(h)]) for h in status_headers]

    extra_headers = (["conn err"] if errs else []) + (["skipped"] if skipped else [])
    extra_values = ([str(errs)] if errs else []) + ([str(skipped)] if skipped else [])

    all_headers = ["Total"] + status_headers + extra_headers
    all_values = [str(len(results))] + status_values + extra_values

    lines: List[str] = [
        "# API Test Results",
        "",
        f"**Base URL**: `{base_url}`  ",
        "",
        "## Run Log",
        "",
        "```",
        *log,
        "```",
        "",
        "## Summary",
        "",
        "| " + " | ".join(all_headers) + " |",
        "|" + "|".join("---" for _ in all_headers) + "|",
        "| " + " | ".join(all_values) + " |",
        "",
        "---",
        "",
        "## Results",
        "",
    ]

    current_section: Optional[str] = None

    for r in results:
        if r.section and r.section != current_section:
            current_section = r.section
            lines += [f"### {current_section}", ""]

        if r.url == "(skipped)":
            lines += [f"#### ⏭ `{r.label}`", ""]
            if r.note:
                lines += [f"_Skipped: {r.note}_", ""]
            continue

        if r.error:
            lines += [f"#### ❌ `{r.label}`", "", f"**URL**: `{r.url}`  ", f"**Error**: {r.error}", ""]
            continue

        icon = "✅" if r.status and r.status < 400 else ("⚠️" if r.status and r.status < 500 else "❌")
        lines += [
            f"#### {icon} `{r.label}` → **{r.status}**",
            "",
            f"**URL**: `{r.url}`  ",
            f"**Content-Type**: `{r.content_type or 'none'}`  ",
        ]
        if r.note:
            lines += [f"**Note**: {r.note}  "]

        fwd = "  |  ".join(f"`{k}: {v}`" for k, v in sorted(r.forwarded_headers.items()))
        lines += [f"**Forwarded Headers**: {fwd or '_(none of interest)_'}  ", ""]

        if r.preview:
            lines += [
                "<details><summary>Response Preview</summary>",
                "",
                "```json",
                r.preview,
                "```",
                "",
                "</details>",
            ]
        elif r.status and r.status < 500:
            lines += [f"_Non-JSON response — content-type: `{r.content_type or 'unknown'}`_"]

        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
