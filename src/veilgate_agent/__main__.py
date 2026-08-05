from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from ._api import OPF
from ._types import RedactionResult


def _parser(prog: str = "opf") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="OpenAI Privacy Filter-compatible Chinese PII redaction.",
    )
    parser.add_argument("positional_text", nargs="?", help="Text input to filter.")
    parser.add_argument("-t", "--text", action="append", help="Text input; repeatable.")
    parser.add_argument(
        "-f",
        "--text-file",
        action="append",
        default=[],
        help="Text file path; repeatable.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--json-indent", type=int, default=2)
    parser.add_argument("--output-mode", choices=("typed", "redacted"), default="typed")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--decode-mode", choices=("viterbi", "argmax"), default="viterbi")
    parser.add_argument("--model", "--checkpoint", dest="model")
    return parser


def _inputs(args: argparse.Namespace):
    if args.positional_text is not None:
        yield args.positional_text
    for text in args.text or ():
        yield text
    for file_path in args.text_file:
        yield Path(file_path).read_text(encoding="utf-8")
    if args.positional_text is None and not args.text and not args.text_file:
        if not sys.stdin.isatty():
            yield sys.stdin.read()
            return
        print("OPF redaction. Type '/exit' (or 'quit') to stop.", file=sys.stderr)
        while True:
            try:
                value = input("text> ")
            except EOFError:
                return
            if value.strip().lower() in {"/exit", "quit"}:
                return
            yield value


def main(argv: Sequence[str] | None = None) -> None:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "redact":
        values = values[1:]
        prog = "opf redact"
    elif values and values[0] in {"eval", "train"}:
        raise SystemExit(
            f"opf {values[0]} is not available in AnxinAI Community."
        )
    else:
        prog = "opf"
    args = _parser(prog).parse_args(values)
    if args.json_indent < 0:
        raise SystemExit("json_indent must be >= 0")
    redactor = OPF(
        model=args.model,
        device=args.device,
        output_mode=args.output_mode,
        decode_mode=args.decode_mode,
        output_text_only=args.format == "text",
    )
    for text in _inputs(args):
        result = redactor.redact(text)
        if isinstance(result, RedactionResult):
            print(json.dumps(result.to_dict(), indent=args.json_indent, ensure_ascii=False))
        else:
            print(result)


if __name__ == "__main__":
    main()
