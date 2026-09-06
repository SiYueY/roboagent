# RoboAgent V1.3 coding reference agent

This example layers a coding harness over the canonical RoboAgent Runtime. The
provider generates either final text or one strict fenced Python action. Python
runs in a persistent, separate worker process; every exposed Tool call returns
to the host over length-prefixed JSON IPC and enters the canonical ToolExecutor
through nested execution.

## Install and run

```bash
uv sync --extra coding
cp config.example.yaml config.yaml
uv run python -m examples.coding --workspace /path/to/repository "Run the tests and fix the failure."
```

Use `--interactive` (or omit the task) for multiple runs. While a run is active:

- `/steer TEXT` queues steering at the next canonical turn boundary.
- `/follow-up TEXT` queues a follow-up.
- `/cancel` cancels the canonical Run and settles nested work.
- Ctrl-C cancels and exits with code 2.

Exit codes are 0 for completed, 1 for failed, 2 for cancelled, and 3 for
startup/configuration errors.

Configuration loads the `.env` next to `config.yaml`, but an already exported
shell variable takes precedence. If a newly updated key still returns
`provider_authentication_error` / HTTP 401, clear the stale shell value before
retrying:

```bash
unset DASHSCOPE_API_KEY
uv run python -m examples.coding --workspace . "Inspect the repository and run the tests."
```

Alternatively, use `env -u DASHSCOPE_API_KEY` for a single invocation. Ensure
the API key belongs to the region selected by the provider `base_url`.

Side-effecting Tools use the Rich approval prompt unless `--yes` is explicitly
provided. The prompt includes bounded arguments, execution lineage, effect
capability, and delegation depth. Approving a composite/Agent Tool does not
pre-approve nested side effects.

## Python modes

Restricted mode is the default. It uses a fixed import allowlist and removes
normal filesystem/process/reflection entry points. It is intended to stop
ordinary model code from bypassing RoboAgent capabilities; it is not a security
sandbox for hostile code.

`--unsafe-python` enables trusted AST execution. Trusted Python is not
sandboxed, may access host resources outside the workspace, is statically
side-effecting, and adds a `TRUSTED_EXECUTION` retry blocker as soon as user code
starts.

## Evaluation and provenance

Deterministic CI tests cover repository understanding, edit/test workflows,
failure recovery, steering, cancellation, long-context compaction, and claim
verification. Real-provider evaluation is reserved for manual/nightly/release
runs because ordinary CI does not require an API key.

See [UPSTREAM_AUDIT.md](UPSTREAM_AUDIT.md) and [NOTICE.md](NOTICE.md) for the
smolagents reuse decision, exact commit, source paths, license, and local
modifications.
