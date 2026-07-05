#!/usr/bin/env python3
"""Merge Cursor Cloud secrets and optional .env.local into repo-root .env.

Never prints secret values — only key names and set/missing status.
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
ENV_LOCAL = ROOT / ".env.local"
ENV_EXAMPLE = ROOT / ".env.example"

# Keys written into .env for Docker Compose (order preserved in output).
SYNC_KEYS: tuple[str, ...] = (
    "OCR_PROVIDER",
    "LLM_PROVIDER",
    "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
    "AZURE_DOCUMENT_INTELLIGENCE_KEY",
    "AZURE_OCR_CHUNK_PAGES",
    "AZURE_OCR_CHUNK_MAX_BYTES",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "SPLIT_MIN_CONFIDENCE",
    "MAX_PAGES_PER_BATCH",
    "MAX_PAGES_PER_DAY",
    "METADATA_DATE_MIN_CONF",
    "METADATA_ORIGINATOR_MIN_CONF",
    "PAPERLESS_BOOTSTRAP_TYPES",
    "PAPERLESS_CONTENT_DATE_FIELD_NAME",
    "PAPERLESS_TOKEN",
    "LANDING_HOOK_SECRET",
)


def parse_env_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        out[key.strip()] = value
    return out


def format_env_file(existing_text: str, values: dict[str, str]) -> str:
    """Update or append SYNC_KEYS in place; preserve comments and unrelated keys."""
    lines = existing_text.splitlines()
    seen: set[str] = set()
    out_lines: list[str] = []

    key_re = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
    for line in lines:
        m = key_re.match(line.strip())
        if m and m.group(1) in values:
            key = m.group(1)
            out_lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out_lines.append(line)

    missing = [k for k in SYNC_KEYS if k in values and k not in seen]
    if missing:
        if out_lines and out_lines[-1].strip():
            out_lines.append("")
        out_lines.append("# --- synced from Cursor Cloud Secrets / .env.local ---")
        for key in missing:
            out_lines.append(f"{key}={values[key]}")
    return "\n".join(out_lines).rstrip() + "\n"


def collect_values() -> dict[str, str]:
    """Merge env sources. Highest priority last: .env → Cursor secrets → .env.local."""
    merged: dict[str, str] = {}

    if ENV_FILE.exists():
        merged.update(parse_env_lines(ENV_FILE.read_text(encoding="utf-8")))
    elif ENV_EXAMPLE.exists():
        merged.update(parse_env_lines(ENV_EXAMPLE.read_text(encoding="utf-8")))

    for key in SYNC_KEYS:
        env_val = os.environ.get(key)
        if env_val is not None and env_val != "":
            merged[key] = env_val

    # .env.local wins over Cursor-injected fakes (e.g. OCR_PROVIDER=fake in Cloud Secrets).
    if ENV_LOCAL.exists():
        merged.update(parse_env_lines(ENV_LOCAL.read_text(encoding="utf-8")))

    return {k: merged[k] for k in SYNC_KEYS if k in merged}


def live_ready(values: dict[str, str]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if values.get("OCR_PROVIDER") != "azure":
        issues.append("OCR_PROVIDER must be azure (got {!r})".format(values.get("OCR_PROVIDER", "")))
    if values.get("LLM_PROVIDER") != "openai":
        issues.append("LLM_PROVIDER must be openai (got {!r})".format(values.get("LLM_PROVIDER", "")))
    for key in live_provider_keys():
        if not values.get(key):
            issues.append(f"{key} is missing")
    return (len(issues) == 0, issues)


def live_provider_keys() -> tuple[str, ...]:
    return (
        "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT",
        "AZURE_DOCUMENT_INTELLIGENCE_KEY",
        "OPENAI_API_KEY",
    )


def status_report(values: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for key in live_provider_keys():
        val = values.get(key, "")
        lines.append(f"{key}: {'set' if val else 'missing'}")
    ocr = values.get("OCR_PROVIDER", "")
    llm = values.get("LLM_PROVIDER", "")
    lines.append(f"OCR_PROVIDER: {ocr or 'unset'}")
    lines.append(f"LLM_PROVIDER: {llm or 'unset'}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print set/missing status only; do not write .env",
    )
    parser.add_argument(
        "--check-live",
        action="store_true",
        help="Exit 1 unless azure + openai providers and all API keys are set",
    )
    args = parser.parse_args()

    values = collect_values()

    if args.check or args.check_live:
        for line in status_report(values):
            print(line)
        if args.check_live:
            ok, issues = live_ready(values)
            if not ok:
                print("")
                print("Live providers NOT ready:")
                for issue in issues:
                    print(f"  - {issue}")
                print("")
                print("Fastest fix on this VM:")
                print("  cp .env.local.example .env.local")
                print("  # edit .env.local — paste Azure endpoint, Azure key, OpenAI key")
                print("  make env-sync && make live-up")
                return 1
            print("")
            print("Live providers ready.")
        return 0

    if not ENV_FILE.exists():
        if ENV_EXAMPLE.exists():
            ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            ENV_FILE.write_text("# Local runtime config\n", encoding="utf-8")

    existing = ENV_FILE.read_text(encoding="utf-8")
    ENV_FILE.write_text(format_env_file(existing, values), encoding="utf-8")

    updated = [k for k in SYNC_KEYS if k in values and os.environ.get(k)]
    if updated:
        print(f"sync-env: updated {len(updated)} key(s) from environment")
    else:
        print("sync-env: .env ready (no new values from environment)")
    for line in status_report(values):
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
