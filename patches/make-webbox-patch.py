"""Run in the local webbox clone at f3a66e0; emit a normal git-apply patch.
No files are changed. Matrix expressions retain their local format.
"""
import difflib
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
if not sha.startswith("f3a66e0"):
    sys.exit("Expected HEAD f3a66e0 on relay-runners; refusing to guess the baseline")
changed = 0
for path in sorted((root / ".github/workflows").glob("*.yml")):
    old = path.read_text(encoding="utf-8")
    new = old
    # Scalar jobs in the supplied local branch use this exact label list.
    new = re.sub(r"runs-on: \[self-hosted, relay, (relay-(?:linux|windows))\]",
                 r"runs-on: [\1, 'run-${{ github.run_id }}']", new)
    # Matrix jobs can store the old labels inside matrix.os. Preserve the
    # matrix rather than changing anything unrelated to runner selection.
    new = re.sub(r"\[self-hosted, relay, (relay-(?:linux|windows))\]",
                 r"[\1, 'run-${{ github.run_id }}']", new)
    # Or construct runs-on from a scalar matrix label/expression.
    new = re.sub(
        r"runs-on: \[self-hosted, relay, (\$\{\{.*?\}\})\]",
        r"runs-on: [\1, 'run-${{ github.run_id }}']", new)
    # A common local matrix uses relay-linux/relay-windows as scalar os values.
    if "runs-on: ${{ matrix.os }}" in new and re.search(r"os:\s*\[relay-linux,\s*relay-windows\]", new):
        new = new.replace("runs-on: ${{ matrix.os }}",
                          "runs-on: [${{ matrix.os }}, 'run-${{ github.run_id }}']")
    if path.name == "player-e2e.yml":
        # Remove only automatic triggers; keep workflow_dispatch and its inputs.
        new = re.sub(r"(?m)^  (push|pull_request):[^\n]*\n(?:^    .*\n|^\n)*", "", new)
    if re.search(r"runs-on:.*(?:self-hosted|\brelay,|ubuntu-latest|windows-latest)", new):
        sys.exit(f"Unhandled runner declaration in {path}; inspect it before applying")
    # Catch unconverted matrix values as well.
    if re.search(r"\[self-hosted, relay,", new):
        sys.exit(f"Unhandled matrix declaration in {path}")
    if old != new:
        relative = path.relative_to(root).as_posix()
        sys.stdout.writelines(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile="a/" + relative, tofile="b/" + relative))
        changed += 1
if changed != 6:
    sys.exit(f"Expected edits in 6 workflows, got {changed}; inspect the patch")
