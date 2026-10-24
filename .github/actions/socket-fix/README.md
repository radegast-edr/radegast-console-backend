# Socket Fix action

Runs the Socket CLI to scan project dependencies, filter discovered vulnerabilities by severity using the GitHub Advisory Database, and automatically generate pull requests with optional auto-merge.

## Usage

### In external repositories (such as `radegast-console-web`)

```yaml
- uses: actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510 # v3
  id: app-token
  with:
    client-id: ${{ vars.APP_CLIENT_ID }}
    private-key: ${{ secrets.APP_PRIVATE_KEY }}

- uses: actions/checkout@08c6903cd8c0fde910a37f88322edcfb5dd907a8 # v5
  with:
    token: ${{ steps.app-token.outputs.token }}

- name: Run Socket Fix
  uses: radegast-edr/radegast-console-backend/.github/actions/socket-fix@main
  with:
    github-token: ${{ steps.app-token.outputs.token }}
    socket-api-token: ${{ secrets.SOCKET_CLI_API_TOKEN }}
    min-severity: high
```

### In the backend repository

```yaml
- name: Run Socket Fix
  uses: ./.github/actions/socket-fix
  with:
    github-token: ${{ steps.app-token.outputs.token }}
    socket-api-token: ${{ secrets.SOCKET_CLI_API_TOKEN }}
    min-severity: ${{ inputs.min_severity || 'low' }}
```

## Inputs

| Name | Description | Required | Default |
|------|-------------|----------|---------|
| `socket-api-token` | Socket API token for authentication | Yes | - |
| `github-token` | GitHub token for querying advisories and creating PRs | No | `${{ github.token }}` |
| `min-severity` | Minimum advisory severity to fix (`low`, `medium`, `high`, `critical`) | No | `low` |
| `autopilot` | Enable auto-merge for pull requests opened by Socket | No | `'true'` |
| `git-user-name` | Git commit author name | No | `'socket-fix[bot]'` |
| `git-user-email` | Git commit author email | No | `'socket-fix[bot]@users.noreply.github.com'` |
| `node-version` | Node.js version | No | `'26'` |
| `project-dir` | Directory of the project to scan and fix | No | `'.'` |

## Outputs

| Name | Description |
|------|-------------|
| `fix-ids` | Comma-separated list of advisory IDs that were targeted for fixes |
