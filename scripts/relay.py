"""One disposable JIT runner. Never forward its stdout/stderr to public logs."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile

VERSION = "2.338.0"
CHECKSUMS = {
    "linux": "af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32",
    "windows": "f48e0750a21812bca5f82de5f7f5aeae71abee647fab5a582f1742d07eba455f",
}
# Add repos deliberately here AND in the ADO webhook filter before enabling them.
ALLOWED_REPOS = {"ref12labs/webbox"}
MAX_SECONDS = 345 * 60


def request_body(repo, run_id, job_id, system):
    if repo not in ALLOWED_REPOS:
        raise ValueError("Repository is not allowlisted")
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", job_id):
        raise ValueError("Run and job IDs must be positive decimal strings")
    if system not in CHECKSUMS:
        raise ValueError("OS must be linux or windows")
    return {
        "name": f"relay-{run_id}-{job_id}",
        "runner_group_id": 1,
        "labels": [f"relay-{system}", f"run-{run_id}"],
        "work_folder": "_work",
    }


def api(token, path, body=None):
    request = urllib.request.Request(
        "https://api.github.com" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hexbox-runner-relay",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Do not print response bodies, headers or credentials.
        raise RuntimeError(f"GitHub API {request.get_method()} {path}: HTTP {error.code}") from None


def download(system, root):
    platform = "win" if system == "windows" else "linux"
    suffix = "zip" if system == "windows" else "tar.gz"
    name = f"actions-runner-{platform}-x64-{VERSION}.{suffix}"
    archive = root / name
    url = f"https://github.com/actions/runner/releases/download/v{VERSION}/{name}"
    with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != CHECKSUMS[system]:
        raise RuntimeError("Official runner download checksum mismatch")
    runner = root / "runner"
    runner.mkdir()
    if system == "windows":
        with zipfile.ZipFile(archive) as package:
            package.extractall(runner)
    else:
        with tarfile.open(archive) as package:
            package.extractall(runner, filter="data")
    return runner


def child_environment(source):
    # The inner runner must not inherit the public job's context or credentials.
    return {
        key: value for key, value in source.items()
        if not key.upper().startswith(("GITHUB_", "ACTIONS_", "RUNNER_", "RELAY_"))
        and key.upper() not in {"GH_TOKEN", "GH_ENTERPRISE_TOKEN"}
    }


def serve(runner, jit, system, timeout=MAX_SECONDS):
    # JIT config is deliberately never written to GITHUB_ENV or an output file.
    command = (["cmd.exe", "/d", "/c", "run.cmd", "--jitconfig", jit]
               if system == "windows" else ["./run.sh", "--jitconfig", jit])
    result = []
    process = subprocess.Popen(
        command, cwd=runner, env=child_environment(os.environ),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    )

    def drain():
        for line in process.stdout:
            # Only recognize fixed runner lifecycle messages; echo no job names,
            # job output, error text, URLs or diagnostic lines.
            if re.search(r": Running job: ", line):
                print("Private job started (output stays in private Actions logs)", flush=True)
            match = re.search(r": Job .+ completed with result: (Succeeded|Failed|Canceled)\s*$", line)
            if match:
                result.append(match.group(1))
        process.stdout.close()

    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("Runner deadline exceeded; inspect/cancel the private run") from None
    thread.join(timeout=10)
    if code != 0:
        raise RuntimeError(f"Runner process exited with code {code}; diagnostics suppressed")
    if not result:
        raise RuntimeError("Runner exited without a completed private job")
    return result[-1]


def summary(text):
    print(text, flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as output:
            output.write(text + "\n")


def main():
    repo, run_id, job_id, system = [os.environ.get("RELAY_" + key, "")
                                    for key in ("REPO", "RUN_ID", "JOB_ID", "OS")]
    body = request_body(repo, run_id, job_id, system)
    token = os.environ.pop("RELAY_GITHUB_TOKEN", "")
    if not token:
        raise ValueError("RELAY_GITHUB_TOKEN is missing")
    expected_system = "windows" if os.name == "nt" else "linux"
    if system != expected_system:
        raise ValueError("Requested OS does not match this hosted runner")
    identity = f"{repo} run={run_id} requested-job={job_id} os={system}"
    print("Relay requested: " + identity, flush=True)
    job = api(token, f"/repos/{repo}/actions/jobs/{job_id}")
    if str(job["run_id"]) != run_id:
        raise ValueError("Job does not belong to the requested run")
    if job["status"] != "queued":
        summary(f"Relay skipped: {identity}; job status={job['status']}")
        return 0
    if not all(label in job["labels"] for label in body["labels"]):
        raise ValueError("Private job labels do not match the requested relay")
    with tempfile.TemporaryDirectory(prefix="hexbox-relay-") as temp:
        runner = download(system, Path(temp))
        print(f"Official runner v{VERSION} downloaded and checksum verified", flush=True)
        response = api(token, f"/repos/{repo}/actions/runners/generate-jitconfig", body)
        jit = response["encoded_jit_config"]
        print("::add-mask::" + jit, flush=True)
        del token, response
        print("JIT runner created; listening once; diagnostic output suppressed", flush=True)
        result = serve(runner, jit, system)
        summary(f"Relay completed: {identity}; private-job-result={result}")
        return 0 if result == "Succeeded" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # All exceptions above contain controlled text, never raw API/runner output.
        # Network exceptions can contain environment URLs; only print safe classes.
        safe = str(error) if type(error) in (ValueError, RuntimeError) else type(error).__name__
        summary("Relay failed: " + safe)
        sys.exit(1)
