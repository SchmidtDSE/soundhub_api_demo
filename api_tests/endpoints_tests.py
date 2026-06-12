"""

Smoke test runner for the soundhub api_dock demo.

Endpoints, discovery steps, and target URLs are all defined in a YAML config
file (default: endpoints_config.yaml in the same directory).

Usage:
  python api_tests/endpoints_tests.py                         # default target
  python api_tests/endpoints_tests.py -t deployed             # named target
  python api_tests/endpoints_tests.py -c my_config.yaml       # custom config
  python api_tests/endpoints_tests.py -b smoke                # override basename
  python api_tests/endpoints_tests.py -d last                 # discovery index
  python api_tests/endpoints_tests.py -d 3                    # Nth discovered item
  python api_tests/endpoints_tests.py --kill-cache            # bust upstream cache

Results are written to api_tests/results/<basename>-<target>.d-<index>.md, where
<basename> comes from the config's `basename` key (default "endpoints") and
<index> is the resolved discovery index (first/last/random/N).

License: BSD 3-Clause

"""

#
# IMPORTS
#
import json
import os
import random
import re
import string
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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

# Discovery index selection. discovery_index / discovery_over_index accept one
# of these words or a 1-based integer N. _OVER is the sentinel returned when an
# integer index exceeds the number of available items.
INDEX_WORDS: frozenset = frozenset({"first", "last", "random"})
DEFAULT_DISCOVERY_INDEX: str = "random"
DEFAULT_DISCOVERY_OVER_INDEX: str = "last"
KILL_CACHE_PARAM: str = "kill_cache"
KILL_CACHE_LEN: int = 8
_OVER: object = object()

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
    """Captures the outcome of a single HTTP request.

    `key` is the stable, un-substituted test label (e.g. "GET /deployments/
    {deployment_id}") used to aggregate the same logical endpoint across runs
    where discovered IDs differ. `label` is the display label with IDs filled in.
    """
    label: str
    url: str
    key: str = ""
    section: str = ""
    status: Optional[int] = None
    content_type: str = ""
    preview: Optional[str] = None
    forwarded_headers: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None
    note: Optional[str] = None
    elapsed: Optional[float] = None


@click.command()
@click.option(
    "--config", "-c",
    default=lambda: str(Path(__file__).parent / DEFAULT_CONFIG),
    show_default=True,
    help="Path to the YAML endpoints config file.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--target", "-t",
    default=None,
    help="Named target from the config (e.g. 'local', 'deployed'). Uses config default if omitted.",
)
@click.option(
    "--basename", "-b",
    default=None,
    help="Output filename base. Overrides the config 'basename' (default 'endpoints').",
)
@click.option(
    "--discovery-index", "-d",
    default=None,
    help="Which discovered item to pick: first/last/random or a 1-based integer N. "
         "Overrides the config 'discovery_index' (default 'random').",
)
@click.option(
    "--discovery-over-index",
    default=None,
    help="Fallback used when discovery_index > item count: first/last/random or an "
         "integer (errors if that integer also exceeds the count). Overrides config.",
)
@click.option(
    "--kill-cache/--no-kill-cache",
    default=None,
    help=f"Append {KILL_CACHE_PARAM}=<random {KILL_CACHE_LEN}-char> to every request to "
         "bust caches. Overrides the config 'kill_cache' (default false).",
)
@click.option(
    "--out", "-o",
    default=None,
    help="Output markdown path. Defaults to api_tests/results/<basename>-<target>.d-<index>.md.",
)
def main(
    config: str,
    target: Optional[str],
    basename: Optional[str],
    discovery_index: Optional[str],
    discovery_over_index: Optional[str],
    kill_cache: Optional[bool],
    out: Optional[str],
) -> None:
    """Smoke-test the soundhub api_dock demo against a named target.

    All endpoints and targets are defined in the YAML config file.
    Run without --target to use the default target from the config.
    """
    cfg = yaml.safe_load(Path(config).read_text())

    base_url, resolved_name = _resolve_target(cfg, target)

    idx = _parse_index_spec(
        discovery_index if discovery_index is not None
        else cfg.get("discovery_index", DEFAULT_DISCOVERY_INDEX)
    )
    over_idx = _parse_index_spec(
        discovery_over_index if discovery_over_index is not None
        else cfg.get("discovery_over_index", DEFAULT_DISCOVERY_OVER_INDEX)
    )
    kill = kill_cache if kill_cache is not None else bool(cfg.get("kill_cache", False))

    if out:
        out_path = Path(out)
    else:
        resolved_basename = basename or cfg.get("basename", "endpoints")
        results_dir = Path(__file__).parent / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        out_path = results_dir / f"{resolved_basename}-{resolved_name}.d-{_index_label(idx)}.md"

    log: List[str] = []
    _log(f"\nTarget : {CYAN}{resolved_name}{RESET} → {base_url}", log)
    _log(f"Config : {config}", log)
    _log(f"Discovery index : {_index_label(idx)} (over: {_index_label(over_idx)})", log)
    _log(f"Kill cache : {kill}", log)
    _log(f"Output : {out_path}\n", log)

    results, _ = run_once(cfg, base_url, resolved_name, idx, over_idx, kill, log)

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


def run_once(
    cfg: Dict[str, Any],
    base_url: str,
    resolved_name: str,
    discovery_index: Union[str, int],
    discovery_over_index: Union[str, int],
    kill_cache: bool,
    log: List[str],
) -> Tuple[List[Result], Dict[str, Any]]:
    """Run discovery + all suites once against base_url.

    Returns (results, discovered_ids). Reusable by the multi-run timing harness;
    main() wraps this with summary printing and markdown output.
    """
    global_cookies = _resolve_cookies(cfg.get("cookies", []))
    client = httpx.Client(base_url=base_url, follow_redirects=False, timeout=30)
    results: List[Result] = []

    _log("Discovering IDs from live data...", log)
    ids = _discover_ids(
        client, cfg.get("discovery", {}), global_cookies, log,
        discovery_index, discovery_over_index, kill_cache,
    )
    for k, v in ids.items():
        _log(f"  {CYAN}{k}{RESET} = {v}", log)

    for suite in cfg.get("suites", []):
        _log(f"\nRunning: {suite['name']}...", log)
        _run_suite(client, suite, ids, global_cookies, results, log, kill_cache)

    client.close()
    return results, ids


#
# INTERNAL
#
def _log(msg: str, log: List[str]) -> None:
    """Print msg to stdout and append a clean (ANSI-stripped) copy to log."""
    click.echo(msg)
    log.append(re.sub(r"\033\[[0-9;]*m", "", msg))


def _resolve_target(cfg: Dict[str, Any], target: Optional[str]) -> tuple:
    """Return (base_url, resolved_name) for the named target or config default."""
    targets = cfg.get("targets", {})
    if not targets:
        raise click.ClickException("No 'targets' section in config.")

    resolved_name = target or targets.get("default")
    if not resolved_name:
        raise click.ClickException("No --target given and no 'default' set in config targets.")

    url = targets.get(resolved_name)
    if not url:
        available = [k for k in targets if k != "default"]
        raise click.ClickException(f"Target '{resolved_name}' not found. Available: {available}")

    return url.rstrip("/"), resolved_name


def _parse_index_spec(value: Any) -> Union[str, int]:
    """Normalise a discovery index spec to 'first'/'last'/'random' or a 1-based int."""
    if isinstance(value, bool):
        raise click.ClickException(f"Invalid discovery index: {value!r}")
    if isinstance(value, int):
        if value < 1:
            raise click.ClickException(f"Discovery index must be >= 1, got {value}")
        return value
    text = str(value).strip().lower()
    if text in INDEX_WORDS:
        return text
    try:
        n = int(text)
    except ValueError:
        raise click.ClickException(
            f"Invalid discovery index {value!r}; expected first/last/random or an integer."
        )
    if n < 1:
        raise click.ClickException(f"Discovery index must be >= 1, got {n}")
    return n


def _index_label(spec: Union[str, int]) -> str:
    """Render an index spec for filenames/logs ('random', 'first', '3', ...)."""
    return str(spec)


def _kill_cache_params(enabled: bool) -> Dict[str, str]:
    """Return a fresh {kill_cache: <random>} param dict when enabled, else empty."""
    if not enabled:
        return {}
    token = "".join(random.choices(string.ascii_lowercase + string.digits, k=KILL_CACHE_LEN))
    return {KILL_CACHE_PARAM: token}


def _discover_ids(
    client: httpx.Client,
    discovery: Dict[str, Any],
    cookies: Dict[str, str],
    log: List[str],
    discovery_index: Union[str, int],
    discovery_over_index: Union[str, int],
    kill_cache: bool,
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
        params.update(_kill_cache_params(kill_cache))
        field_name = step.get("field", "id")

        try:
            r = client.get(path, params=params, cookies=cookies, timeout=15)
            body = r.json()
        except Exception:
            ids[id_name] = None
            continue

        # Selection (incl. discovery_over_index range errors) is intentionally
        # outside the network try: an over-index error should surface, not be
        # silently swallowed as "id not found".
        ids[id_name] = _extract_field_by_index(
            body, field_name, discovery_index, discovery_over_index, id_name
        )

    return ids


def _run_suite(
    client: httpx.Client,
    suite: Dict[str, Any],
    ids: Dict[str, Any],
    global_cookies: Dict[str, str],
    results: List[Result],
    log: List[str],
    kill_cache: bool,
) -> None:
    """Run all tests in a suite, substituting IDs and skipping where required IDs are absent."""
    section = suite["name"]
    cookies = {**global_cookies, **_resolve_cookies(suite.get("cookies", []))}

    for test in suite.get("tests", []):
        raw_label = test.get("label", test["path"])
        requires = test.get("requires", [])
        missing = [r for r in requires if ids.get(r) is None]
        if missing:
            _skip(results, _substitute(raw_label, ids), f"missing IDs: {missing}",
                  section=section, log=log, key=raw_label)
            continue

        path = _substitute(test["path"], ids)
        label = _substitute(raw_label, ids)
        params = {k: _substitute(str(v), ids) for k, v in test.get("params", {}).items()}

        _hit(client, results, label, path, params or None, test.get("note"),
             section=section, cookies=cookies, log=log, key=raw_label, kill_cache=kill_cache)


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
    key: str = "",
    kill_cache: bool = False,
) -> Optional[httpx.Response]:
    """Make a GET request, append a Result, and print progress."""
    req_params = dict(params or {})
    req_params.update(_kill_cache_params(kill_cache))

    full_url = str(client.base_url).rstrip("/") + path
    if req_params:
        full_url += "?" + "&".join(f"{k}={v}" for k, v in req_params.items())

    start = time.perf_counter()
    try:
        resp = client.get(path, params=req_params or None, cookies=cookies or {})
    except httpx.RequestError as exc:
        elapsed = time.perf_counter() - start
        results.append(Result(
            label=label, url=full_url, key=key, section=section,
            error=str(exc), note=note, elapsed=elapsed,
        ))
        _log(f"  {RED}ERR{RESET}  {label}  {elapsed:05.2f}", log or [])
        return None

    elapsed = time.perf_counter() - start
    ct = resp.headers.get("content-type", "")
    results.append(Result(
        label=label,
        url=full_url,
        key=key,
        section=section,
        status=resp.status_code,
        content_type=ct,
        preview=_build_preview(resp, ct),
        forwarded_headers={k: v for k, v in resp.headers.items() if k.lower() in INTERESTING_HEADERS},
        note=note,
        elapsed=elapsed,
    ))

    color = GREEN if resp.status_code < 400 else (YELLOW if resp.status_code < 500 else RED)
    _log(f"  {color}{resp.status_code}{RESET}  {label}  {elapsed:05.2f}", log or [])
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


def _select_row(
    rows: List[Any],
    discovery_index: Union[str, int],
    discovery_over_index: Union[str, int],
    ctx: str,
) -> Any:
    """Pick one row from a non-empty list per the index spec, falling back on over-index."""
    chosen = _pick_row(rows, discovery_index)
    if chosen is not _OVER:
        return chosen

    chosen = _pick_row(rows, discovery_over_index)
    if chosen is _OVER:
        raise click.ClickException(
            f"discovery_over_index ({_index_label(discovery_over_index)}) exceeds the "
            f"{len(rows)} item(s) available for '{ctx}'."
        )
    return chosen


def _pick_row(rows: List[Any], spec: Union[str, int]) -> Any:
    """Return the selected row, or _OVER when an integer spec exceeds the item count."""
    if spec == "first":
        return rows[0]
    if spec == "last":
        return rows[-1]
    if spec == "random":
        return random.choice(rows)
    # 1-based integer
    if spec <= len(rows):
        return rows[spec - 1]
    return _OVER


def _extract_field_by_index(
    body: Any,
    field_name: str,
    discovery_index: Union[str, int],
    discovery_over_index: Union[str, int],
    ctx: str,
) -> Optional[Any]:
    """Pull `field_name` from the index-selected row of a list/paginated response."""
    rows: Optional[List[Any]] = None
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        if field_name in body:
            return body[field_name]
        for key in ("results", "data"):
            candidate = body.get(key, [])
            if isinstance(candidate, list) and candidate:
                rows = candidate
                break

    if not rows:
        return None

    row = _select_row(rows, discovery_index, discovery_over_index, ctx)
    return row.get(field_name) if isinstance(row, dict) else None


def _skip(
    results: List[Result],
    label: str,
    reason: str,
    section: str = "",
    log: Optional[List[str]] = None,
    key: str = "",
) -> None:
    results.append(Result(label=label, url="(skipped)", key=key, section=section, note=reason))
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
            lines += [f"#### ❌ `{r.label}`", "", f"**URL**: `{r.url}`  "]
            if r.elapsed is not None:
                lines += [f"**Time**: {r.elapsed:.2f}s  "]
            lines += [f"**Error**: {r.error}", ""]
            continue

        icon = "✅" if r.status and r.status < 400 else ("⚠️" if r.status and r.status < 500 else "❌")
        lines += [
            f"#### {icon} `{r.label}` → **{r.status}**",
            "",
            f"**URL**: `{r.url}`  ",
            f"**Content-Type**: `{r.content_type or 'none'}`  ",
        ]
        if r.elapsed is not None:
            lines += [f"**Time**: {r.elapsed:.2f}s  "]
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
