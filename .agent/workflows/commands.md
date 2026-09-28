# Commands

| Command | Purpose |
|---|---|
| `docdelta run --repos R --tasks T --workdir W [--agent doc-reader\|opencode --model M] [--conditions with_docs,without_docs,patched] [--repeats N] [--repo X] [--task Y] [--patch-dir P] [--cap-usd D --reserve-usd d] [--cap-tokens N --reserve-tokens n] [--timeout S] [--keep-checkouts] [--allow-unreviewed] [--builtin-provider] [--rate-limit stop\|wait --rate-limit-run-wait S --rate-limit-backoff 300,900,2700]` | Run the matrix |
| `docdelta judge --workdir W --tasks T --env-file E [--model gpt-6-luna] [--cap-usd 0.10]` | LLM-judge guardrails and points; writes `judged.json` per run |
| `docdelta report --workdir W --repo X [--out F]` | Markdown scorecard |
| `docdelta badge --workdir W --repo X [--out F]` | shields.io endpoint JSON |
| `docdelta list-agent-docs PATH` | Agent docs a checkout contains |

Exit codes: 0 ok, 1 usage/setup error, 2 budget exhausted, 3 rate limited.
