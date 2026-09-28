"""Command line: ``docdelta run | judge | report | badge | list-agent-docs``.

Exit codes: 0 ok, 1 usage or setup error, 2 budget exhausted, 3 rate limited.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docdelta.agents import make_agent
from docdelta.budget import AccountingError, Budget, BudgetExhausted, RateLimited
from docdelta.conditions import CONDITIONS, DEFAULT_CONDITIONS, find_agent_docs
from docdelta.agents.opencode import load_api_keys
from docdelta.judge import (
    DEFAULT_JUDGE_MODEL,
    JudgeBudgetExhausted,
    JudgeRateLimited,
    judge_workdir,
    openai_call,
)
from docdelta.models import load_repos, load_tasks
from docdelta.report import badge, scorecard_markdown
from docdelta.checkout import CheckoutError
from docdelta.conditions import ConditionError
from docdelta.agents.opencode import IsolationError
from docdelta.runner import (
    MatrixConfig,
    StaleResults,
    UnreviewedTasks,
    load_results,
    run_matrix,
    spent_so_far,
)


def _conditions(value: str) -> tuple[str, ...]:
    names = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = [name for name in names if name not in CONDITIONS]
    if unknown or not names:
        raise argparse.ArgumentTypeError(f"conditions must be from {CONDITIONS}, got {value!r}")
    return names


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docdelta", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the task x condition x repeat matrix")
    run.add_argument("--repos", type=Path, required=True, help="repos.json with pinned commits")
    run.add_argument("--tasks", type=Path, required=True, help="directory of <repo>/<task>.json")
    run.add_argument("--workdir", type=Path, required=True)
    run.add_argument("--agent", default="doc-reader", help="doc-reader (free, fake) or opencode")
    run.add_argument("--model", default="", help="opencode model (default openai/gpt-6-luna)")
    run.add_argument("--env-file", type=Path, default=None,
                     help=".env whose *_API_KEY values reach only the agent process")
    run.add_argument("--builtin-provider", action="store_true",
                     help="use the model from OpenCode's built-in catalog without a key (free Zen models)")
    run.add_argument("--config-source", type=Path, default=None,
                     help="opencode.json to take the model's provider block from")
    run.add_argument("--conditions", type=_conditions, default=DEFAULT_CONDITIONS)
    run.add_argument("--repeats", type=int, default=3)
    run.add_argument("--repo", action="append", default=[], help="limit to these repos")
    run.add_argument("--task", action="append", default=[], help="limit to these task ids")
    run.add_argument("--patch-dir", type=Path, default=None)
    run.add_argument("--cap-usd", type=float, default=None)
    run.add_argument("--reserve-usd", type=float, default=0.10,
                     help="dollars held per run under --cap-usd; a run past it is killed")
    run.add_argument("--cap-tokens", type=int, default=None)
    run.add_argument("--reserve-tokens", type=int, default=400_000)
    run.add_argument("--timeout", type=float, default=900.0)
    run.add_argument("--keep-checkouts", action="store_true")
    run.add_argument("--allow-unreviewed", action="store_true")

    report = sub.add_parser("report", help="markdown scorecard from saved results")
    report.add_argument("--workdir", type=Path, required=True)
    report.add_argument("--repo", required=True)
    report.add_argument("--out", type=Path, default=None)

    badge_cmd = sub.add_parser("badge", help="shields.io endpoint JSON from saved results")
    badge_cmd.add_argument("--workdir", type=Path, required=True)
    badge_cmd.add_argument("--repo", required=True)
    badge_cmd.add_argument("--out", type=Path, default=None)

    judge_cmd = sub.add_parser("judge", help="LLM-judge guardrails and key points of saved runs")
    judge_cmd.add_argument("--workdir", type=Path, required=True)
    judge_cmd.add_argument("--tasks", type=Path, required=True)
    judge_cmd.add_argument("--env-file", type=Path, required=True, help=".env with OPENAI_API_KEY")
    judge_cmd.add_argument("--model", default=DEFAULT_JUDGE_MODEL)
    judge_cmd.add_argument("--cap-usd", type=float, default=0.10)

    docs = sub.add_parser("list-agent-docs", help="list the agent docs a checkout contains")
    docs.add_argument("path", type=Path)
    return parser


def _emit(text: str, out: Path | None) -> None:
    if out is None:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "list-agent-docs":
        _emit("\n".join(find_agent_docs(args.path)), None)
        return 0
    if args.command == "judge":
        key = load_api_keys(args.env_file).get("OPENAI_API_KEY")
        if not key:
            print(f"no OPENAI_API_KEY in {args.env_file}", file=sys.stderr)
            return 1
        try:
            count, spent = judge_workdir(
                args.workdir, args.tasks, openai_call(key, args.model), args.model, args.cap_usd
            )
        except JudgeBudgetExhausted as exc:
            print(f"stopped: judge budget: {exc}", file=sys.stderr)
            return 2
        except JudgeRateLimited as exc:
            print(f"stopped: judge rate limited, not retrying: {exc}", file=sys.stderr)
            return 3
        except (RuntimeError, OSError) as exc:  # HTTP errors, network failures
            print(f"error: judge failed: {exc}", file=sys.stderr)
            return 1
        print(f"{count} runs judged; spent ${spent:.4f}", file=sys.stderr)
        return 0
    if args.command in ("report", "badge"):
        results = load_results(args.workdir, args.repo)
        if not results:
            print(f"no results for {args.repo} under {args.workdir}", file=sys.stderr)
            return 1
        if args.command == "report":
            _emit(scorecard_markdown(results, args.repo), args.out)
        else:
            _emit(json.dumps(badge(results), indent=1), args.out)
        return 0

    repos = load_repos(args.repos)
    tasks = load_tasks(args.tasks, tuple(args.repo), tuple(args.task))
    if not tasks:
        print("no tasks selected", file=sys.stderr)
        return 1
    config = MatrixConfig(
        workdir=args.workdir,
        conditions=args.conditions,
        repeats=args.repeats,
        timeout_s=args.timeout,
        patch_dir=args.patch_dir,
        keep_checkouts=args.keep_checkouts,
        allow_unreviewed=args.allow_unreviewed,
    )
    # Caps cover everything this workdir has spent, including earlier invocations and runs
    # that were stopped or rate limited.
    spent_tokens, spent_usd = spent_so_far(args.workdir)
    budget = Budget(
        cap_tokens=args.cap_tokens,
        reserve_tokens=args.reserve_tokens,
        cap_usd=args.cap_usd,
        reserve_usd=args.reserve_usd,
        used_tokens=spent_tokens,
        used_usd=spent_usd,
    )
    try:
        if args.agent != "doc-reader" and args.cap_usd is None and args.cap_tokens is None:
            print("error: a paid agent needs --cap-usd or --cap-tokens", file=sys.stderr)
            return 1
        agent = make_agent(
            args.agent,
            args.model,
            env_file=args.env_file,
            config_source=args.config_source,
            builtin_provider=args.builtin_provider,
            reserve_tokens=args.reserve_tokens if args.cap_tokens is not None else None,
            reserve_usd=args.reserve_usd if args.cap_usd is not None else None,
        )
        results = run_matrix(tasks, repos, agent, config, budget)
    except (BudgetExhausted, AccountingError) as exc:
        print(f"stopped: budget: {exc}", file=sys.stderr)
        return 2
    except RateLimited as exc:
        print(f"stopped: rate limited, not retrying: {exc}", file=sys.stderr)
        return 3
    except (UnreviewedTasks, StaleResults, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (CheckoutError, ConditionError, IsolationError, OSError) as exc:
        print(f"error: setup failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"{len(results)} runs; workdir total spend {budget.used_tokens:,} tokens, ${budget.used_usd:.4f}",
        file=sys.stderr,
    )
    return 0
