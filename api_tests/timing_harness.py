"""

Multi-run timing harness for the soundhub api_dock demo.

Runs the same endpoint suite against several sources (local, deployed, direct)
N times each — with cache-busting and randomised discovery by default — then
writes a single timing-comparison doc. The cross-source comparison uses the
MEAN of each endpoint's N runs; a per-source detail section reports the spread
(n / mean / std / min / max) and any failures or flaky statuses.

Sources (default):
  direct   → endpoints_config_direct.yaml, target 'soundhub' (upstream, no proxy)
  local    → endpoints_config.yaml,        target 'local'    (api_dock @ localhost)
  deployed → endpoints_config.yaml,        target 'deployed' (api_dock @ App Runner)

Usage:
  pixi run python api_tests/timing_harness.py                       # 3 random runs/source
  pixi run python api_tests/timing_harness.py -n 5                  # 5 random runs/source
  pixi run python api_tests/timing_harness.py --discovery-indices 1,2,3
  pixi run python api_tests/timing_harness.py --no-kill-cache

License: BSD 3-Clause

"""

#
# IMPORTS
#
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import click
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import endpoints_tests as et  # noqa: E402


#
# CONSTANTS
#
PREFIX: str = "/core/latest"
RESULTS_DIR: Path = Path(__file__).parent / "results"
HERE: Path = Path(__file__).parent

DEFAULT_OVER_INDEX: str = "last"
DEFAULT_RUNS: int = 3

# (label, config filename, target). Direct first so it is the comparison baseline.
SOURCES: List[Tuple[str, str, str]] = [
    ("direct", "endpoints_config_direct.yaml", "soundhub"),
    ("local", "endpoints_config.yaml", "local"),
    ("deployed", "endpoints_config.yaml", "deployed"),
]


#
# PUBLIC
#
@click.command()
@click.option(
    "--runs", "-n",
    default=DEFAULT_RUNS,
    show_default=True,
    help="Number of runs per source (each with discovery_index=random).",
)
@click.option(
    "--discovery-indices",
    default=None,
    help="Comma list of discovery indices, e.g. '1,2,3' or 'first,last,random'. "
         "One run per entry; overrides --runs.",
)
@click.option(
    "--kill-cache/--no-kill-cache",
    default=True,
    show_default=True,
    help="Append kill_cache=<random> to every request to bust caches.",
)
@click.option(
    "--config",
    default=lambda: str(HERE / "endpoints_config.yaml"),
    help="Config for the local/deployed sources.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--direct-config",
    default=lambda: str(HERE / "endpoints_config_direct.yaml"),
    help="Config for the direct (upstream) source.",
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--out", "-o",
    default=None,
    help="Output markdown path. Defaults to results/timing-harness.md.",
)
def main(
    runs: int,
    discovery_indices: Optional[str],
    kill_cache: bool,
    config: str,
    direct_config: str,
    out: Optional[str],
) -> None:
    """Run all sources N times and write a mean-based timing comparison."""
    specs = _resolve_index_specs(runs, discovery_indices)
    config_for = {
        "direct": direct_config,
        "local": config,
        "deployed": config,
    }

    sources: List[dict] = []
    for label, _default_cfg, target in SOURCES:
        cfg_path = config_for[label]
        bar = "#" * 60
        click.echo(f"\n{bar}\n# SOURCE: {label}  ({Path(cfg_path).name} → {target})\n{bar}")
        sources.append(_run_source(label, cfg_path, target, specs, kill_cache))

    out_path = Path(out) if out else (RESULTS_DIR / "timing-harness.md")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    lines = _render(sources, specs, kill_cache)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    click.echo(f"\nWrote {out_path}")


#
# INTERNAL
#
def _resolve_index_specs(runs: int, discovery_indices: Optional[str]) -> List[Union[str, int]]:
    """Build the per-run index list: parsed --discovery-indices, or N × 'random'."""
    if discovery_indices:
        specs = [et._parse_index_spec(s) for s in discovery_indices.split(",") if s.strip()]
        if not specs:
            raise click.ClickException("--discovery-indices was empty after parsing.")
        return specs
    if runs < 1:
        raise click.ClickException("--runs must be >= 1.")
    return ["random"] * runs


def _run_source(
    label: str,
    config_path: str,
    target: str,
    specs: List[Union[str, int]],
    kill_cache: bool,
) -> dict:
    """Run one source `len(specs)` times, returning aggregated per-endpoint timings."""
    cfg = yaml.safe_load(Path(config_path).read_text())
    base_url, resolved = et._resolve_target(cfg, target)
    over_idx = et._parse_index_spec(cfg.get("discovery_over_index", DEFAULT_OVER_INDEX))

    per_key: Dict[str, dict] = {}
    failures: List[str] = []

    for i, spec in enumerate(specs, 1):
        click.echo(f"\n--- {label} run {i}/{len(specs)} (d={et._index_label(spec)}) ---")
        log: List[str] = []
        try:
            results, _ids = et.run_once(cfg, base_url, resolved, spec, over_idx, kill_cache, log)
        except Exception as exc:
            failures.append(f"`{label}` run {i}: aborted — {exc}")
            continue

        for r in results:
            if r.url == "(skipped)":
                continue
            key = r.key.replace(PREFIX, "", 1)
            slot = per_key.setdefault(key, {"times": [], "statuses": []})
            if r.error:
                failures.append(f"`{label}` run {i}: `{key}` connection error — {r.error}")
                slot["statuses"].append("ERR")
                continue
            slot["statuses"].append(r.status)
            if r.status is not None and r.status >= 500:
                failures.append(f"`{label}` run {i}: `{key}` → {r.status}")
            if r.elapsed is not None:
                slot["times"].append(r.elapsed)

    # Flag endpoints whose status changed between runs (excluding pure ERR noise).
    for key, slot in per_key.items():
        distinct = sorted(map(str, set(slot["statuses"])))
        if len(distinct) > 1:
            failures.append(f"`{label}`: `{key}` status varied across runs: {distinct}")

    return {
        "label": label,
        "target": resolved,
        "base_url": base_url,
        "per_key": per_key,
        "failures": failures,
    }


def _stats(times: List[float]) -> Optional[dict]:
    """Return {n, mean, std, min, max} for a list of seconds, or None if empty."""
    if not times:
        return None
    n = len(times)
    return {
        "n": n,
        "mean": statistics.mean(times),
        "std": statistics.stdev(times) if n >= 2 else 0.0,
        "min": min(times),
        "max": max(times),
    }


def _stat_block(title: str, values: List[float]) -> List[str]:
    """Summary-stat bullet list for a series of per-endpoint means."""
    if not values:
        return [f"**{title}** — no data", ""]
    return [
        f"**{title}**",
        "",
        f"- endpoints: {len(values)}",
        f"- mean: {statistics.mean(values):.3f}s",
        f"- median: {statistics.median(values):.3f}s",
        f"- min: {min(values):.3f}s",
        f"- max: {max(values):.3f}s",
        f"- total: {sum(values):.3f}s",
        "",
    ]


def _render(sources: List[dict], specs: List[Union[str, int]], kill_cache: bool) -> List[str]:
    """Build the full harness markdown."""
    by_label = {s["label"]: s for s in sources}
    direct, local, deployed = by_label["direct"], by_label["local"], by_label["deployed"]

    lines: List[str] = [
        "# Multi-Run Timing Comparison",
        "",
        f"- **Runs per source**: {len(specs)} "
        f"(discovery indices: {', '.join(et._index_label(s) for s in specs)})",
        f"- **kill_cache**: {kill_cache}",
        f"- **Generated**: {datetime.now().isoformat(timespec='seconds')}",
        "- **Sources**:",
    ]
    for s in sources:
        lines.append(f"  - `{s['label']}` → {s['base_url']} (target `{s['target']}`)")
    lines += [
        "",
        "Cross-source numbers are the **mean** of each endpoint's runs. Per-source "
        "detail below shows the spread. Δ columns are vs the Direct baseline.",
        "",
    ]

    # Failures & anomalies.
    all_failures = [f for s in sources for f in s["failures"]]
    lines += ["## Failures & Anomalies", ""]
    if all_failures:
        lines += [f"- {f}" for f in all_failures]
    else:
        lines += ["_None — all requests succeeded with stable statuses across runs._"]
    lines += [""]

    # Cross-source comparison (intersection of endpoints with timings in all three).
    keys = [k for k in direct["per_key"]
            if _stats(direct["per_key"][k]["times"])
            and k in local["per_key"] and _stats(local["per_key"][k]["times"])
            and k in deployed["per_key"] and _stats(deployed["per_key"][k]["times"])]

    lines += [
        "## Cross-Source Comparison — mean of runs",
        "",
        "| Endpoint | Direct (s) | Local (s) | Deployed (s) | Δ Local−Dir | Δ Dep−Dir |",
        "|---|---|---|---|---|---|",
    ]
    d_means, l_means, p_means = [], [], []
    for k in keys:
        d = _stats(direct["per_key"][k]["times"])["mean"]
        ll = _stats(local["per_key"][k]["times"])["mean"]
        p = _stats(deployed["per_key"][k]["times"])["mean"]
        d_means.append(d)
        l_means.append(ll)
        p_means.append(p)
        lines.append(
            f"| `{k}` | {d:.3f} | {ll:.3f} | {p:.3f} | {ll - d:+.3f} | {p - d:+.3f} |"
        )

    lines += ["", "### Summary (mean of per-endpoint means)", ""]
    lines += _stat_block("Direct", d_means)
    lines += _stat_block("Local", l_means)
    lines += _stat_block("Deployed", p_means)

    # Per-source detail.
    lines += ["## Per-Source Detail — spread across runs", ""]
    for s in sources:
        lines += [f"### {s['label']} ({s['target']})", "",
                  "| Endpoint | n | mean (s) | std (s) | min (s) | max (s) |",
                  "|---|---|---|---|---|---|"]
        for key, slot in s["per_key"].items():
            st = _stats(slot["times"])
            if not st:
                continue
            lines.append(
                f"| `{key}` | {st['n']} | {st['mean']:.3f} | {st['std']:.3f} | "
                f"{st['min']:.3f} | {st['max']:.3f} |"
            )
        lines.append("")

    return lines


if __name__ == "__main__":
    main()
