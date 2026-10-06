# Rustinel integration action

Runs the canonical `.github/tests` scripts on Linux, macOS, and Windows. The
test installs the agent and Rustinel through the backend, starts real services,
and verifies that a native healthcheck alert reaches the backend through the agent.
The action reports failures and uploads diagnostics even when the test fails.
It requires a native hosted runner with administrator access (passwordless sudo
on Linux and macOS).

To test a candidate archive from another repository:

```yaml
- uses: radegast-edr/radegast-console-backend/.github/actions/rustinel-integration@main
  with:
    rustinel-zip: candidate.zip
    version: '1.8.0r3'
```

Alternatively, set `asset-dir` to a directory containing
`upstream-{os}-{arch}.zip` archives. The platform is detected automatically;
Apple Silicon uses the release architecture `m5`. A missing candidate archive
fails the test; it never falls back to a published release.

To test an agent checkout, check it out first and pass `agent-source: .` (or
its checkout directory). The action builds a wheel from a temporary copy,
assigns a unique integration version, and supplies a checksum-pinned localhost
wheel URL through the backend's existing `RADEGAST_AGENT_PACKAGE` setting.
The real installer installs that wheel. Dependencies can come from PyPI;
the agent itself must come from this checkout. Automatic agent updates are
disabled during the test.

Source mode checks the installed tool in an isolated Python interpreter:
its distribution version, `direct_url.json` installation URL, the served wheel's
checksum, and every installed agent Python source file must match the checkout.
The running service must report
the same integration version to the backend before its healthcheck can pass.
Build logs and source/provenance manifests are uploaded with diagnostics.

With neither archive input, the action downloads the latest upstream release
from the public backend manifest, including its version and checksum metadata.

By default the backend source comes from the same revision as the action,
including when GitHub downloads it for a remote caller. `backend-dir` can select
another checkout. Paths supplied by the caller are relative to its working
directory or absolute. Additional inputs are `timeout` (180 seconds),
`output-dir`, `artifact-name`, and `retention-days` (7). Outputs are `output-dir`,
`os`, and `arch`.

The backend workflow uses the local action to test the current checkout. The
releaser follows `@main` so test improvements take effect without copying
scripts. Publish changes to the backend's main branch before running the
updated releaser workflow.
