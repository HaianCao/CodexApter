from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import config_path, load
from .diagnostics import doctor
from .errors import AdapterError
from .launcher import launch
from .web import serve


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-p", "--provider")
    parser.add_argument("-m", "--model")
    parser.add_argument("--reasoning")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-ca")
    parser.add_argument("--config", type=Path, default=None, help="Path to configuration TOML")
    commands = parser.add_subparsers(dest="command")
    setup = commands.add_parser("setup", help="Open interactive provider/model setup")
    setup.add_argument("codex_args", nargs=argparse.REMAINDER)
    run = commands.add_parser("run", help="Run Codex using resolved configuration")
    _add_selection(run); run.add_argument("codex_args", nargs=argparse.REMAINDER)
    execute = commands.add_parser("exec", help="Forward Codex exec arguments")
    _add_selection(execute); execute.add_argument("codex_args", nargs=argparse.REMAINDER)
    resume = commands.add_parser("resume", help="Forward Codex resume arguments")
    _add_selection(resume); resume.add_argument("codex_args", nargs=argparse.REMAINDER)
    providers = commands.add_parser("providers", help="Provider operations")
    providers.add_argument("action", choices=["list"])
    models = commands.add_parser("models", help="Model operations")
    models.add_argument("action", choices=["list"]); models.add_argument("-p", "--provider")
    validate = commands.add_parser("config", help="Configuration operations")
    validate.add_argument("action", choices=["validate"])
    check = commands.add_parser("doctor", help="Validate provider setup")
    _add_selection(check)
    migrate = commands.add_parser("migrate", help="Plan a cross-provider migrated session")
    migrate.add_argument("--from", dest="source", required=True, help="Source session ID")
    migrate.add_argument("--scope", choices=["recent", "all", "summary"], default="recent")
    _add_selection(migrate)
    return parser


def _without_separator(items: list[str]) -> list[str]:
    return items[1:] if items[:1] == ["--"] else items


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        config = load(args.config, allow_legacy_protocols=args.command == "setup")
        if args.command is None:
            provider, model, reasoning = config.resolve(None, None, None)
            return launch(config, provider, model, reasoning, [])
        if args.command == "setup":
            return serve(config)
        if args.command == "providers":
            for provider in config.providers.values():
                print(f"{provider.id}\t{provider.display_name}\t{provider.base_url}")
            return 0
        if args.command == "models":
            for alias in config.models.values():
                if not args.provider or alias.provider == args.provider:
                    print(f"{alias.name}\t{alias.provider}\t{alias.model_id}")
            return 0
        if args.command == "config":
            print(f"Configuration is valid: {config.path}")
            return 0
        provider, model, reasoning = config.resolve(args.provider, args.model, args.reasoning)
        if args.command == "doctor":
            results = doctor(config, provider, model)
            for name, status in results:
                print(f"{name}: {status}")
            return 1 if any(status.startswith("FAIL:") for _, status in results) else 0
        if args.command == "migrate":
            print(f"Migration plan: {args.source} -> new {provider.id}/{model}; scope={args.scope}.")
            print("Transcript extraction is intentionally not implemented until the installed Codex session format is contract-tested.")
            return 2
        extra = _without_separator(args.codex_args)
        if args.command in {"exec", "resume"}:
            extra = [args.command, *extra]
        return launch(config, provider, model, reasoning, extra)
    except AdapterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
