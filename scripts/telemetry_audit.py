#!/usr/bin/env python3
"""Telemetry audit for the stripped fork. Stdlib only; reads files, runs nothing from the repo.

Two kinds of check:
  invariants  hard requirements of the strip (must always hold; no baseline)
  drift       anything NEW since the reviewed baseline (hosts, egress-primitive files,
              telemetry-ish dependencies, catalog disclosures)

Usage (from any directory, never import the repo):
  python3 -I scripts/telemetry_audit.py --root /path/to/hermes-agent
  python3 -I scripts/telemetry_audit.py --root . --update-baseline   # after reviewing a diff

Exit 0 = clean, 1 = findings. Baseline: scripts/telemetry_audit_baseline.json.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

CODE_EXT = {".py", ".mjs", ".js", ".ts", ".tsx", ".rs"}
SKIP_PARTS = {"tests", "test", "website", "skills", "optional-skills", "docs", "node_modules",
              "contributors", "evals", "assets", "i18n", "fixtures", "__tests__"}
SKIP_NAME = re.compile(r"(\.test\.|\.spec\.|_test\.py$|^test_|/i18n/|package-lock\.json$)")

HOST_RE = re.compile(r"https?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
PRIMITIVE_RE = re.compile(
    r"\burlopen\(|\bhttpx\.(?:Async)?(?:Client|get|post|request)\b|\brequests\.(?:get|post|put|request|Session)\b"
    r"|\baiohttp\.ClientSession\b|\bfetch\(|\bsendBeacon\b|\bXMLHttpRequest\b|\bhttp\.client\b|\bsocket\.create_connection\b")
DEP_RE = re.compile(
    r"opentelemetry|sentry|posthog|segment|mixpanel|amplitude|statsig|datadog|langfuse|langsmith|wandb|"
    r"bugsnag|rollbar|honeycomb|newrelic|logrocket|plausible|umami|rudderstack|logfire|helicone|arize|"
    r"mlflow|traceloop|scarf|telemetry|analytics", re.I)
DISCLOSE_RE = re.compile(r"telemetry|analytics|usage (?:data|statistics)|crash report", re.I)


def git_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def is_code(rel: str) -> bool:
    p = Path(rel)
    return (p.suffix in CODE_EXT and not (set(p.parts[:-1]) & SKIP_PARTS)
            and not SKIP_NAME.search(rel))


def read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def scan(root: Path) -> dict:
    files = git_files(root)
    hosts: dict[str, set[str]] = {}
    primitives: set[str] = set()
    for rel in filter(is_code, files):
        text = read(root, rel)
        for h in HOST_RE.findall(text):
            h = h.lower().rstrip(".")
            if h not in ("localhost", "example.com"):
                hosts.setdefault(h, set()).add(rel)
        if PRIMITIVE_RE.search(text):
            primitives.add(rel)

    deps: set[str] = set()
    for rel in files:
        name = Path(rel).name
        text = read(root, rel) if name in ("pyproject.toml", "package.json") else ""
        for line in text.splitlines():
            m = re.search(r'"([@A-Za-z0-9_./-]+)"\s*[:=<>~^,]|^\s*"([A-Za-z0-9_.-]+)[=<>~! \[]', line)
            tok = next((g for g in (m.groups() if m else ()) if g), "")
            if tok and DEP_RE.search(tok):
                deps.add(f"{rel}:{tok}")
    lock = root / "uv.lock"
    if lock.exists():
        for n in re.findall(r'^name = "([^"]+)"', lock.read_text(errors="replace"), re.M):
            if DEP_RE.search(n):
                deps.add(f"uv.lock:{n}")

    disclosures: set[str] = set()
    for rel in files:
        if rel.startswith("plugin-catalog/") and rel.endswith((".yaml", ".yml")):
            if DISCLOSE_RE.search(read(root, rel)):
                disclosures.add(rel)

    return {"hosts": {h: sorted(v) for h, v in sorted(hosts.items())},
            "primitive_files": sorted(primitives), "deps": sorted(deps),
            "catalog_disclosures": sorted(disclosures)}


def invariants(root: Path) -> list[str]:
    bad: list[str] = []

    def need(rel: str, pattern: str, why: str, absent: bool = False) -> None:
        text = read(root, rel)
        hit = re.search(pattern, text, re.S) is not None
        if hit == absent:
            bad.append(f"{rel}: {why}")

    need("hermes_cli/observability/shared_metrics_send_config.py",
         r"SendConfig\(enabled=False, send=False, endpoint=\"\"\)", "send config must be hard-wired off")
    need("hermes_cli/observability/shared_metrics_send_config.py", r"https?://", "no URL allowed", absent=True)
    need("hermes_cli/observability/shared_metrics_sender.py", r"urlopen|urllib\.request",
         "sender must have no network transport", absent=True)
    need("hermes_cli/observability/shared_metrics_sender.py",
         r"def _post\(.*?\n    raise RuntimeError", "_post must raise")
    need("agent/monitoring/otlp_exporter.py",
         r"def _require_sdk\(.*?\n    raise OTLPUnavailable", "_require_sdk must always raise")
    need("hermes_cli/observability/shared_metrics_consent.py",
         r'return \{"enabled": False, "send": False, "decided": True, "reask": False\}',
         "consent_state must be decided+off")
    need("hermes_cli/config_defaults.py",
         r'"shared_metrics": \{\s*"enabled": False,\s*"send": False,\s*"endpoint": "",', "telemetry defaults must be off/empty")
    need("hermes_cli/config_defaults.py", r'"updates": \{[^}]*?"check": False', "updates.check must default False")
    need("plugins/platforms/photon/sidecar/index.mjs", r"const telemetry = false;", "photon telemetry must be off")
    need("pyproject.toml", r"^otlp\s*=", "otlp extra must stay removed", absent=True)
    for rel in git_files(root):
        if is_code(rel) and re.search(r"telemetry\.(staging-)?nousresearch\.com", read(root, rel)):
            bad.append(f"{rel}: Nous telemetry endpoint reintroduced")
    return bad


def drift(current: dict, base: dict) -> list[str]:
    out: list[str] = []
    for h, where in current["hosts"].items():
        if h not in base.get("hosts", []):
            out.append(f"NEW HOST {h}  (in {', '.join(where[:3])}{'...' if len(where) > 3 else ''})")
    for key, label in (("primitive_files", "NEW EGRESS-PRIMITIVE FILE"), ("deps", "NEW TELEMETRY-ISH DEP"),
                       ("catalog_disclosures", "NEW CATALOG DISCLOSURE")):
        for item in current[key]:
            if item not in base.get(key, []):
                out.append(f"{label} {item}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args()
    root = args.root.resolve()
    baseline_path = root / "scripts" / "telemetry_audit_baseline.json"

    current = scan(root)
    if args.update_baseline:
        baseline_path.write_text(json.dumps(
            {"hosts": sorted(current["hosts"]), "primitive_files": current["primitive_files"],
             "deps": current["deps"], "catalog_disclosures": current["catalog_disclosures"]},
            indent=1) + "\n")
        print(f"baseline written: {len(current['hosts'])} hosts, {len(current['primitive_files'])} files, "
              f"{len(current['deps'])} deps, {len(current['catalog_disclosures'])} disclosures")
        return 0

    findings = [f"INVARIANT BROKEN {m}" for m in invariants(root)]
    if baseline_path.exists():
        findings += drift(current, json.loads(baseline_path.read_text()))
    else:
        findings.append("no baseline: run with --update-baseline after reviewing the tree")
    for f in findings:
        print(f)
    print(f"\n{'FAIL' if findings else 'CLEAN'}: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
