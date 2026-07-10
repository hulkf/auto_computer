# Repository Instructions

## CodeGraph

In repositories indexed by CodeGraph (a `.codegraph/` directory exists at the repository root), use CodeGraph before grep/find or direct file reading when locating or understanding code:

- Prefer the `codegraph_explore` MCP tool when available.
- Otherwise run `codegraph explore "<symbol names or question>"`.
- If `.codegraph/` does not exist, skip CodeGraph; indexing is the user's decision.

## Automatic GitHub synchronization

After every completed code, configuration, test, or documentation change:

1. Run checks proportional to the change and do not push failing work.
2. Stage only files that belong to the current task; preserve unrelated user changes.
3. Commit the completed change with a concise, descriptive message.
4. Push the current branch to `origin` automatically without waiting for another request.
5. Never commit or push secrets, `.env`, browser profiles, logs, runtime snapshots, virtual environments, or generated credentials.

On this machine, if the configured `127.0.0.1:3424` Git proxy is unavailable, use per-command `git -c http.proxy= -c https.proxy= ...` for GitHub network operations instead of changing the user's global proxy configuration.
