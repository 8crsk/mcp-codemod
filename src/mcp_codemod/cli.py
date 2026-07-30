"""Command line interface.

Defaults to a dry run. A tool that rewrites a user's source tree the first
time they try it, before they know whether it is any good, does not get a
second try.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from .transform import run


def _iter_python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    skip = {".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", "build"}
    return sorted(
        p
        for p in root.rglob("*.py")
        if not any(part in skip for part in p.parts)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mcp-codemod",
        description=(
            "Rewrite MCP Python SDK v1 code for v2 (spec 2026-07-28). "
            "Dry run by default; pass --write to apply."
        ),
    )
    parser.add_argument("path", type=Path, help="file or directory to migrate")
    parser.add_argument(
        "--write", action="store_true", help="apply changes in place"
    )
    args = parser.parse_args(argv)

    if not args.path.exists():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return 2

    files = _iter_python_files(args.path)
    changed_files = 0
    total_findings = 0

    for path in files:
        source = path.read_text(encoding="utf-8")
        try:
            new_source, findings, changed = run(source)
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the run
            print(f"{path}: skipped (could not parse: {exc})", file=sys.stderr)
            continue

        if changed:
            changed_files += 1
            if args.write:
                path.write_text(new_source, encoding="utf-8")
                print(f"rewrote {path}")
            else:
                diff = difflib.unified_diff(
                    source.splitlines(keepends=True),
                    new_source.splitlines(keepends=True),
                    fromfile=f"{path} (v1)",
                    tofile=f"{path} (v2)",
                )
                sys.stdout.writelines(diff)

        for finding in findings:
            total_findings += 1
            print(
                f"{path}:{finding.line}: {finding.code} {finding.message}",
                file=sys.stderr,
            )

    verb = "rewrote" if args.write else "would rewrite"
    print(
        f"\n{verb} {changed_files} file(s) of {len(files)} scanned; "
        f"{total_findings} finding(s) need review."
    )
    if not args.write and changed_files:
        print("Re-run with --write to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
