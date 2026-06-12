"""

Multi-run timing harness for the soundhub api_dock demo.

Runs the same endpoint suite against several sources N times each — with
cache-busting and randomised discovery by default — then writes a single
timing-comparison doc. The cross-source table reports each endpoint's mean time
plus the per-run overhead vs Direct (mean/max/min/std); a per-source detail
section reports each source's own spread and any failures or flaky statuses.

Sources:
  direct   → endpoints_config_direct.yaml, target 'soundhub' (upstream, no proxy)
  deployed → endpoints_config.yaml,        target 'deployed' (api_dock @ App Runner)
  local    → endpoints_config.yaml,        target 'local'    (api_dock @ localhost)

By default only `direct` and `deployed` run — `local` is opt-in via --include-local.

Usage:
  pixi run python api_tests/timing_harness.py                       # 3 runs (direct+deployed)
  pixi run python api_tests/timing_harness.py -n 5                  # 5 random runs/source
  pixi run python api_tests/timing_harness.py -l                   # also run local
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

# label → (config-key, target). config-key picks --direct-config vs --config.
ALL_SOURCES: Dict[str, Tuple[str, str]] = {
    "direct": ("direct", "soundhub"),
    "deployed": ("main", "deployed"),
    "local": ("main", "local"),
}
# Order used for running and for display columns/sections.
SOURCE_ORDER: List[str] = ["direct", "local", "deployed"]


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
    "--include-local/--no-include-local", "-l",
    default=False,
    show_default=True,
    help="Also run the local api_dock source (off by default; direct + deployed only).",
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
    include_local: bool,
    config: str,
    direct_config: str,
    out: Optional[str],
) -> None:
    """Run the selected sources N times and write a comparison vs the Direct baseline.

    Discovery is shared: each run discovers IDs once and reuses them across every
    source, so direct and deployed exercise the SAME records that run (making the
    per-run Δ stats real overhead rather than noise from different random IDs).
    By default only `direct` and `deployed` run; pass --include-local to add local.
    Each individual run is also saved under results/timing_run-<YYMMDD_HHMM>/ as
    <basename>-<target>.d-<index>.n<i>.md (<i> = 1-based run index).
    """
    specs = _resolve_index_specs(runs, discovery_indices)
    config_for = {"direct": direct_config, "main": config}
    labels = [lbl for lbl in SOURCE_ORDER if lbl != "local" or include_local]

    run_dir = RESULTS_DIR / f"timing_run-{datetime.now().strftime('%y%m%d_%H%M')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Load + resolve each source once.
    src: Dict[str, dict] = {}
    for label in labels:
        cfg_key, target = ALL_SOURCES[label]
        cfg = yaml.safe_load(Path(config_for[cfg_key]).read_text())
        base_url, resolved = et._resolve_target(cfg, target)
        src[label] = {
            "cfg": cfg,
            "base_url": base_url,
            "target": resolved,
            "over": et._parse_index_spec(cfg.get("discovery_over_index", DEFAULT_OVER_INDEX)),
            "basename": cfg.get("basename", "endpoints"),
        }

    # Shared discovery uses the most complete config (the main config carries the
    # full id set incl. birdnet/owl), via the deployed target.
    disc = src["deployed"] if "deployed" in src else src[labels[0]]
    accs = {lbl: _new_acc(lbl, src[lbl]["base_url"], src[lbl]["target"]) for lbl in labels}

    for i, spec in enumerate(specs, 1):
        bar = "#" * 60
        click.echo(f"\n{bar}")
        click.echo(f"# RUN {i}/{len(specs)}  (discovery index: {et._index_label(spec)})")
        click.echo(bar)
        disc_log: List[str] = []
        try:
            ids = et.discover(disc["cfg"], disc["base_url"], disc["target"],
                              spec, disc["over"], kill_cache, disc_log)
        except Exception as exc:
            for lbl in labels:
                accs[lbl]["failures"].append(f"run {i}: shared discovery aborted — {exc}")
            continue

        for label in labels:
            s = src[label]
            click.echo(f"\n--- {label} run {i}/{len(specs)} ---")
            log: List[str] = []
            et._log(f"Target : {s['target']} → {s['base_url']}", log)
            et._log(f"Discovery index : {et._index_label(spec)} (shared)", log)
            et._log(f"Kill cache : {kill_cache}   Run : {i}/{len(specs)}\n", log)
            try:
                results, _ = et.run_once(s["cfg"], s["base_url"], s["target"],
                                         spec, s["over"], kill_cache, log, ids=ids)
            except Exception as exc:
                accs[label]["failures"].append(f"`{label}` run {i}: aborted — {exc}")
                continue
            et._append_summary(results, log)
            out_path = run_dir / f"{s['basename']}-{s['target']}.d-{et._index_label(spec)}.n{i}.md"
            et._write_markdown(results, s["base_url"], out_path, log)
            _record_run(accs[label], results, i)

    for label in labels:
        _finalize_failures(accs[label])

    sources = [accs[label] for label in labels]
    out_path = Path(out) if out else (run_dir / "timing-harness.md")
    lines = _render(sources, specs, kill_cache)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    click.echo(f"\nWrote comparison → {out_path}")
    click.echo(f"Per-run files in → {run_dir}/")


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


def _new_acc(label: str, base_url: str, target: str) -> dict:
    """Create an empty per-source accumulator the render step consumes."""
    return {"label": label, "base_url": base_url, "target": target,
            "per_key": {}, "failures": []}


def _record_run(acc: dict, results: List, run_i: int) -> None:
    """Fold one run's results into a source accumulator (times, statuses, failures)."""
    label = acc["label"]
    for r in results:
        if r.url == "(skipped)":
            continue
        key = r.key.replace(PREFIX, "", 1)
        slot = acc["per_key"].setdefault(key, {"times": [], "statuses": []})
        if r.error:
            acc["failures"].append(f"`{label}` run {run_i}: `{key}` connection error — {r.error}")
            slot["statuses"].append("ERR")
            continue
        slot["statuses"].append(r.status)
        if r.status is not None and r.status >= 500:
            acc["failures"].append(f"`{label}` run {run_i}: `{key}` → {r.status}")
        if r.elapsed is not None:
            slot["times"].append(r.elapsed)


def _finalize_failures(acc: dict) -> None:
    """Flag endpoints whose status changed between runs (after all runs recorded)."""
    label = acc["label"]
    for key, slot in acc["per_key"].items():
        distinct = sorted(map(str, set(slot["statuses"])))
        if len(distinct) > 1:
            acc["failures"].append(f"`{label}`: `{key}` status varied across runs: {distinct}")


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


def _moments(values: List[float]) -> dict:
    """Return {n, mean, std, min, max} for a list of numbers (std=0 when n<2)."""
    n = len(values)
    return {
        "n": n,
        "mean": statistics.mean(values),
        "std": statistics.stdev(values) if n >= 2 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def _diff_block(title: str, values: List[float]) -> List[str]:
    """Signed mean/std/min/max bullets for a per-endpoint difference series."""
    if not values:
        return [f"**{title}** — no data", ""]
    m = _moments(values)
    return [
        f"**{title}**  (n={m['n']} endpoints)",
        "",
        f"- mean: {m['mean']:+.3f}s",
        f"- std:  {m['std']:.3f}s",
        f"- min:  {m['min']:+.3f}s",
        f"- max:  {m['max']:+.3f}s",
        "",
    ]


def _overall_line(values: List[float]) -> str:
    """One-line unsigned mean/std/min/max summary across a source's endpoints."""
    if not values:
        return "_Overall — no timing data._"
    m = _moments(values)
    return (
        f"**Overall** (across {m['n']} endpoints) — mean {m['mean']:.3f}s · "
        f"std {m['std']:.3f}s · min {m['min']:.3f}s · max {m['max']:.3f}s"
    )


def _paired_diffs(base_times: List[float], other_times: List[float]) -> List[float]:
    """Per-run differences (other − base), paired by run index (shorter list wins)."""
    return [o - b for b, o in zip(base_times, other_times)]


def _has_times(src: dict, key: str) -> bool:
    """True when a source has at least one timing sample for an endpoint key."""
    return bool(src["per_key"].get(key, {}).get("times"))


def _render(sources: List[dict], specs: List[Union[str, int]], kill_cache: bool) -> List[str]:
    """Build the full harness markdown."""
    by_label = {s["label"]: s for s in sources}
    direct = by_label["direct"]
    deployed = by_label["deployed"]
    local = by_label.get("local")
    has_local = local is not None

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
        "Direct/Local/Deployed columns are the **mean** of each endpoint's runs. "
        "Δ columns are per-run differences (source − Direct) summarised across the "
        "runs (mean/max/min/std). Per-source detail below shows each source's spread.",
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

    # Endpoints with timings in every source that ran.
    keys = [k for k in direct["per_key"]
            if _has_times(direct, k) and _has_times(deployed, k)
            and (not has_local or _has_times(local, k))]

    # Build per-endpoint rows; collect per-endpoint mean overheads for the headline.
    table_rows: List[str] = []
    d_means, l_means, p_means = [], [], []
    mean_overhead_dep, mean_overhead_loc = [], []
    for k in keys:
        d_times = direct["per_key"][k]["times"]
        p_times = deployed["per_key"][k]["times"]
        d, p = statistics.mean(d_times), statistics.mean(p_times)
        d_means.append(d)
        p_means.append(p)
        dep = _moments(_paired_diffs(d_times, p_times))
        mean_overhead_dep.append(dep["mean"])

        cells = [f"`{k}`", f"{d:.3f}"]
        if has_local:
            l_times = local["per_key"][k]["times"]
            ll = statistics.mean(l_times)
            l_means.append(ll)
            loc = _moments(_paired_diffs(d_times, l_times))
            mean_overhead_loc.append(loc["mean"])
            cells += [f"{ll:.3f}", f"{p:.3f}",
                      f"{loc['mean']:+.3f}", f"{loc['max']:+.3f}", f"{loc['min']:+.3f}",
                      f"{loc['std']:.3f}",
                      f"{dep['mean']:+.3f}", f"{dep['max']:+.3f}", f"{dep['min']:+.3f}",
                      f"{dep['std']:.3f}"]
        else:
            cells += [f"{p:.3f}",
                      f"{dep['mean']:+.3f}", f"{dep['max']:+.3f}", f"{dep['min']:+.3f}",
                      f"{dep['std']:.3f}"]
        table_rows.append("| " + " | ".join(cells) + " |")

    lines += ["## Cross-Source Comparison", ""]

    # Headline: distribution of per-endpoint mean overheads across endpoints.
    lines += ["### Proxy vs Direct — headline (per-endpoint mean overhead, across endpoints)", ""]
    lines += _diff_block("Deployed − Direct  (deployed api_dock overhead)", mean_overhead_dep)
    if has_local:
        lines += _diff_block("Local − Direct  (local api_dock overhead)", mean_overhead_loc)

    # Per-endpoint table with Δ mean/max/min/std across runs.
    if has_local:
        header = ("| Endpoint | Direct (s) | Local (s) | Deployed (s) "
                  "| Loc−Dir mean | Loc−Dir max | Loc−Dir min | Loc−Dir std "
                  "| Dep−Dir mean | Dep−Dir max | Dep−Dir min | Dep−Dir std |")
        sep = "|" + "|".join(["---"] * 12) + "|"
    else:
        header = ("| Endpoint | Direct (s) | Deployed (s) "
                  "| Dep−Dir mean | Dep−Dir max | Dep−Dir min | Dep−Dir std |")
        sep = "|" + "|".join(["---"] * 7) + "|"
    lines += ["### Per-endpoint (Δ stats across runs)", "", header, sep]
    lines += table_rows

    lines += ["", "### Summary (mean of per-endpoint means)", ""]
    lines += _stat_block("Direct", d_means)
    if has_local:
        lines += _stat_block("Local", l_means)
    lines += _stat_block("Deployed", p_means)

    # Per-source detail — overall stats lead each per-endpoint spread table.
    lines += ["## Per-Source Detail — spread across runs", ""]
    for s in sources:
        means = [st["mean"] for slot in s["per_key"].values()
                 if (st := _stats(slot["times"]))]
        lines += [
            f"### {s['label']} ({s['target']})",
            "",
            _overall_line(means),
            "",
            "| Endpoint | n | mean (s) | std (s) | min (s) | max (s) |",
            "|---|---|---|---|---|---|",
        ]
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
