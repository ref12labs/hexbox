# Runner relay setup and diagnosis

Private webbox jobs run on disposable self-hosted JIT runners supplied by
standard public hexbox jobs. A repository webhook creates an agentless ADO
pipeline run for each queued job. Only relay labels cause a dispatch.
No settings, secrets, service connections or webhooks were created by this change.

## Before enabling it

1. Review/merge the hexbox `relay-runners` branch yourself. GitHub requires a
   dispatch workflow to exist on the default branch. Until then the new workflow
   cannot be tested by dispatch merely by specifying the feature branch.
   `ado/relay-trigger.yml` dispatches `main`. Do not dispatch untrusted branch code
   with the token. No change to main was made by the implementation.
2. Only run trusted private workflows. A private job runs as the same OS user as
   the supervisor; it can inspect process memory, JIT credentials and the public
   runner's filesystem. Environment stripping and output filtering are hygiene,
   **not a security boundary** against malicious private code.
   Do not enable relay workflows for untrusted PRs/forks without review.
3. Budget/concurrency limits still apply. These are standard ubuntu-latest and
   windows-latest jobs, not larger runners. Public Actions logs show private repo,
   run/job IDs, OS and result; they do not include private step logs. Artifacts,
   caches and storage can still have their own billing limits.
4. Run labels isolate a **run**, not an individual job. Parallel same-OS jobs in
   that run can take any matching runner. Names include the triggering job ID
   for diagnosis, but `requested-job` is not a guarantee about which job it served.
   JIT runners accept one job then unregister. Delivery retries are serialized
   per triggering job and skip when that job is no longer queued.

## User setup (in order)

### 1. One fine-grained GitHub PAT

URL: https://github.com/settings/personal-access-tokens/new

- Name: `runner-relay`
- Resource owner: `ref12labs`
- Repository access: **Only select repositories**: `webbox`, `hexbox`
- Repository permissions: **Administration: Read and write** and
  **Actions: Read and write**. Metadata read is implicit.
- Set an expiry and record a rotation date. Approve in the org if policy requires it.
- Fine-grained PAT repository permissions apply to ALL selected repositories;
  they cannot grant Administration only on webbox and Actions only on hexbox.
  Thus this single-token design also grants Administration on hexbox and
  Actions write on webbox. Actions read on webbox is used to check queued job status.
  If that scope is unacceptable, the one-PAT design needs to change.
- Save the same token in both places below; no extra GitHub token is needed.

URL: https://github.com/ref12labs/hexbox/settings/secrets/actions/new

- Name: `RELAY_GITHUB_TOKEN`
- Value: that PAT
- Repository Actions secret (not a public variable).

### 2. Incoming WebHook connection in ADO

Project: **PublicIngestion**, organization **ref12**.
URL: https://dev.azure.com/ref12/PublicIngestion/_settings/adminservices

New service connection → **Incoming WebHook**:

- Service connection name: `relay-webbox-incoming`
- WebHook Name: `relay-webbox` (this is the URL name, not the connection name)
- Secret: one randomly generated HMAC secret, e.g. output of
  `python -c "import secrets; print(secrets.token_hex(32))"`.
  Do not put this value in YAML, source or public logs.
- Http Header: **`X-Hub-Signature`**
- Use the SAME secret on GitHub below.
- ADO's documented check is HMAC-SHA1; don't choose `X-Hub-Signature-256`.
- Restrict pipeline permissions to `runner-relay-trigger` when created;
  avoid “Grant access permission to all pipelines”.

### 3. Generic GitHub dispatch connection

Same ADO service connections URL → New → **Generic**:

- Service connection name: `relay-github-dispatch`
- Server URL: `https://api.github.com` (no trailing slash)
- User name: the GitHub login that owns the PAT
- Password/Token: the SAME PAT saved in `RELAY_GITHUB_TOKEN`
- This uses the Generic connection's Basic authorization; token goes in password.
- Restrict pipeline permissions to `runner-relay-trigger`.
- No additional secret pipeline variables or explicit Authorization header.
  The YAML overrides InvokeRESTAPI's default headers so ADO's System.AccessToken
  is **not** sent to GitHub.

### 4. Create the YAML pipeline

URL: https://dev.azure.com/ref12/PublicIngestion/_build

New pipeline → GitHub → select `ref12labs/hexbox` → Existing Azure Pipelines
YAML file → branch `main` after review/merge → path **`/ado/relay-trigger.yml`**.
Name the pipeline **`runner-relay-trigger`**. Save without running a job.
Use an existing authorized GitHub source connection if available. If the wizard
requires authorizing another GitHub/Azure Pipelines app or source connection,
that is a user action, separate from the two relay connections described above.

Set pipeline Settings → **Default branch for manual and scheduled builds** to
`refs/heads/main` (or the reviewed branch where the webhook YAML lives).
Authorize the two connections for this pipeline. No hosted agent is required.

Pipeline 80 / `ci/refresh-drives.yml` could not be inspected: the granted
`ADO_REF12_TOKEN` connection was not signed in. That path is not in hexbox.
Do not repoint pipeline 80. The new pipeline can read the public hexbox YAML
directly, subject to your project's existing source authorization.

### 5. Add webbox repository webhook

URL: https://github.com/ref12labs/webbox/settings/hooks/new

- Payload URL:
  `https://dev.azure.com/ref12/_apis/public/distributedtask/webhooks/relay-webbox?api-version=6.0-preview`
- Content type: **application/json**
- Secret: the SAME HMAC secret from step 2
- SSL verification: enabled
- Events: **Let me select individual events → Workflow jobs** (`workflow_job`) only
- Active: yes
- Repository-level on webbox; **not** org-wide, and no webhook on hexbox.

The initial ping does not dispatch a runner. `action=queued` is the resource filter;
in_progress/completed events do not create relay runs.

### 6. Apply the webbox label edits locally

Local clone: `Q:\src\workspace\wb-merge`, branch `relay-runners`, HEAD `f3a66e0`.

That commit is local-only and was not accessible from this runner. Therefore an
exact all-workflow patch against it cannot honestly be claimed as verified.
`patches/webbox-relay-scalars.patch` contains exact replacement lines for eight
scalar jobs in five files; line numbers use GitHub main, so apply with
`git apply --unidiff-zero --check` first. It intentionally does not change the
already-manual-only Player e2e trigger. **This partial patch omits C# matrix jobs.**

Preferred: copy `patches/make-webbox-patch.py` locally. In the webbox clone run:

```powershell
git switch relay-runners
git rev-parse --short HEAD  # must be f3a66e0
python PATH\TO\make-webbox-patch.py . > webbox-relay.patch
git apply --check webbox-relay.patch
git apply webbox-relay.patch
git diff -- .github/workflows
```

The generator emits an ordinary patch without editing workflows; it preserves
supported local matrix layouts and removes automatic Player e2e triggers if
present. It fails on unhandled layouts. If it fails, supply the local workflow
files to finish an exact patch. Do not apply partial stdout on failure.

Every job, including every C# matrix expansion, must request these two labels:

```yaml
runs-on: [relay-linux, 'run-${{ github.run_id }}']
# or
runs-on: [relay-windows, 'run-${{ github.run_id }}']
```

A straightforward matrix form, if your local C# matrix needs conversion:

```yaml
strategy:
  fail-fast: false
  matrix:
    os: [relay-linux, relay-windows]
runs-on: [${{ matrix.os }}, 'run-${{ github.run_id }}']
```

Keep Player e2e `on: workflow_dispatch` only. Commit and push the webbox branch
yourself; do not turn on existing jobs until the smoke test below passes.

## Validation

Offline tests:
`python -m unittest discover -s tests -v`
`actionlint .github/workflows/relay.yml`

The implementation tests request body, both OS label sets, bad inputs, stale
queued events, credential environment stripping, output suppression, and process
failure/deadline. Linux fake runner tested locally; real Windows/JIT registration
and real ADO compilation/dispatch require setup and are not yet tested.

ADO preview was **not run**: the granted Azure connection was not signed in.
After signing in and creating the pipeline/connections, preview with a
synthetic event. The preview API accepts `previewRun` and `yamlOverride`, but
webhook payload injection may depend on ADO's resource resolution; the definitive
test is a real signed queued webhook. Example (PAT held only in your environment):

```powershell
$payload = @{
  previewRun = $true
  yamlOverride = Get-Content ado/relay-trigger.yml -Raw
  resources = @{ repositories = @{ self = @{ refName = 'refs/heads/main' } } }
} | ConvertTo-Json -Depth 20
Invoke-RestMethod -Method Post `
  -Uri "https://dev.azure.com/ref12/PublicIngestion/_apis/pipelines/PIPELINE_ID/runs?api-version=7.1" `
  -Headers @{ Authorization = "Bearer $env:ADO_TOKEN" } `
  -ContentType application/json -Body $payload
```

Do not treat YAML parsing/actionlint as ADO schema validation. Agentless log
entries are zero-minute `Delay@1` tasks: server jobs cannot execute shell scripts.
The dispatch uses API version `2026-03-10`, whose documented response is HTTP 200
with run URLs; older GitHub API versions return 204. No success criterion assumes
a field inside the response. Inspect the real InvokeRESTAPI HTTP result during
the first smoke test (connection Basic auth and response handling need live proof).

## First end-to-end test

On a throwaway webbox branch, add `.github/workflows/relay-smoke.yml`:

```yaml
name: Relay smoke
on:
  push:
    branches: [relay-smoke]
    paths: [.github/workflows/relay-smoke.yml]
  workflow_dispatch:
jobs:
  hello:
    runs-on: [relay-linux, 'run-${{ github.run_id }}']
    steps:
      - run: echo hello
```

Push branch `relay-smoke`. Using a push trigger allows the first test without
putting the smoke workflow on webbox main.

What you should see:

1. **webbox → Actions** https://github.com/ref12labs/webbox/actions:
   Relay smoke starts; hello waits for `relay-linux` and `run-<run_id>`.
2. **webbox → webhook Recent deliveries**: workflow_job/queued delivered,
   ADO returns success. If not: check URL, JSON content type, shared secret,
   `X-Hub-Signature`, active pipeline and its default YAML branch.
3. **ADO** https://dev.azure.com/ref12/PublicIngestion/_build:
   one `runner-relay-trigger` run. `Received` shows repo, run, job, labels.
   `Dispatch hexbox relay` reports HTTP success; `Dispatch accepted` follows.
   This green run means **dispatch accepted**, not private job completed.
4. **hexbox → Runner relay**:
   https://github.com/ref12labs/hexbox/actions/workflows/relay.yml
   Public ubuntu-latest job downloads the checksum-verified official runner,
   prints JIT created/listening, then a fixed “Private job started” message.
   No private job name, `hello`, step output or diagnostics are forwarded.
5. **webbox**: hello runs on `relay-<run>-<job>`, prints `hello`, turns green.
   Private step logs and artifacts stay here.
6. **hexbox**: summary identifies repo/run/requested-job and Succeeded; job exits.
   The ephemeral runner disappears from webbox Settings → Actions → Runners.
7. Repeat with `relay-windows`. Test two same-OS parallel jobs, reruns and a failing
   job. Run labels cannot distinguish same-OS jobs within the same run.
8. Negative tests: queued job with no relay labels gets a readable clean skip in
   ADO; completed/in_progress delivery triggers nothing; bad HMAC is rejected.
   Redeliver queued after completion: hexbox should skip rather than create JIT.
9. Cancel private and relay runs to test abandonment cleanup. If a job is canceled
   before a runner claims it, a runner can wait until the 345-minute internal
   deadline (360-minute outer limit); cancel its hexbox run promptly.
   This minimal implementation deliberately has no polling service.

Diagnosis:

- No ADO run: delivery/filter/signature/default YAML branch/connection authorization.
- ADO 401/403: token expired, org approval or Generic credentials/scopes.
- ADO 404/422: workflow missing on hexbox default branch or wrong dispatch ref/inputs.
- JIT 403: Administration write / selected repo / org policy.
- Runner waiting: private labels mismatch, canceled job, same-run job already claimed.
- Relay Failed: inspect private webbox logs. Runner diagnostics intentionally are
  suppressed and never uploaded as public artifacts.
- Hosted public runner time is free under the standard GitHub billing model;
  using a self-hosted runner does not waive unrelated artifacts/storage costs.

References:
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/rest/actions/self-hosted-runners#create-configuration-for-a-just-in-time-runner-for-a-repository
- https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
- https://learn.microsoft.com/en-us/azure/devops/pipelines/process/resources
- https://learn.microsoft.com/en-us/azure/devops/pipelines/tasks/reference/invoke-rest-api-v1
