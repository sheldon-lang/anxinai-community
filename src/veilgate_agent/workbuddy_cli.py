from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import webbrowser
import secrets
import socket
from urllib.request import urlopen

from . import __version__
from ._workbuddy import (
    WorkBuddyReviewError,
    create_restore_file_session,
    create_restore_session,
    create_review,
    delete_restore_session,
    load_ticket,
    load_restore_session,
    public_summary,
    update_ticket,
    validate_ticket_files,
)


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _start_server(payload: dict[str, object]) -> str:
    ticket_id = str(payload["ticket_id"])
    access_token = str(payload["access_token"])
    log_directory = Path(
        os.environ.get("ANXINAI_HOME", str(Path.home() / ".anxinai"))
    ).expanduser() / "workbuddy" / "logs"
    log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path = log_directory / f"{ticket_id}.log"
    process_options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        process_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        process_options["start_new_session"] = True
    with log_path.open("ab") as log:
        process_options["stdout"] = log
        process_options["stderr"] = log
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "veilgate_agent.review_server",
                ticket_id,
                access_token,
            ],
            **process_options,
        )
    for _ in range(50):
        current = load_ticket(ticket_id)
        if current.get("review_url"):
            return str(current["review_url"])
        time.sleep(0.05)
    update_ticket(
        ticket_id,
        status="invalid",
        message="本地审核页启动失败，请重新检查文件。",
        access_token="",
        mappings=[],
    )
    safe_path = Path(str(payload["safe_path"]))
    if safe_path.is_file():
        safe_path.unlink()
    raise WorkBuddyReviewError("本地审核页启动失败。")


def _scan(args: argparse.Namespace) -> int:
    payload = create_review(args.path)
    review_url = _start_server(payload)
    current = load_ticket(str(payload["ticket_id"]))
    if not args.no_open:
        webbrowser.open(review_url, new=2)
    _print(public_summary(current).to_dict())
    return 0


def _status(args: argparse.Namespace) -> int:
    payload = load_ticket(args.ticket_id)
    if payload.get("status") == "approved":
        valid, message = validate_ticket_files(payload)
        if not valid:
            payload = update_ticket(
                args.ticket_id,
                status="invalid",
                message=message,
                mappings=[],
            )
    _print(public_summary(payload).to_dict())
    return 0


def _open(args: argparse.Namespace) -> int:
    payload = load_ticket(args.ticket_id)
    review_url = payload.get("review_url")
    if payload.get("status") != "pending" or not review_url:
        raise WorkBuddyReviewError("该审核已结束或本地审核页不可用。")
    webbrowser.open(str(review_url), new=2)
    _print(public_summary(payload).to_dict())
    return 0


def _start_restore_server(payload: dict[str, object]) -> str:
    session_id = str(payload["session_id"])
    access_token = str(payload["access_token"])
    log_directory = Path(
        os.environ.get("ANXINAI_HOME", str(Path.home() / ".anxinai"))
    ).expanduser() / "workbuddy" / "logs"
    log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    log_path = log_directory / f"restore-{session_id}.log"
    process_options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        process_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        process_options["start_new_session"] = True
    with log_path.open("ab") as log:
        process_options["stdout"] = log
        process_options["stderr"] = log
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "veilgate_agent.restore_server",
                session_id,
                access_token,
            ],
            **process_options,
        )
    for _ in range(50):
        current = load_restore_session(session_id)
        if current.get("restore_url"):
            return str(current["restore_url"])
        time.sleep(0.05)
    delete_restore_session(session_id)
    raise WorkBuddyReviewError("本地恢复结果页启动失败。")


def _restore(args: argparse.Namespace) -> int:
    payload = create_restore_session(args.ticket_id, args.safe_response_path)
    restore_url = _start_restore_server(payload)
    if not args.no_open:
        webbrowser.open(restore_url, new=2)
    _print(
        {
            "schema_version": 1,
            "status": "restored_locally",
            "ticket_id": args.ticket_id,
            "message": "恢复后的正式结果已在本地页面打开，未写回 Agent。",
        }
    )
    return 0


def _restore_file(args: argparse.Namespace) -> int:
    payload = create_restore_file_session(
        args.ticket_id,
        args.safe_output_path,
    )
    restore_url = _start_restore_server(payload)
    if not args.no_open:
        webbrowser.open(restore_url, new=2)
    _print(
        {
            "schema_version": 1,
            "status": "restored_locally",
            "result_kind": "file",
            "ticket_id": args.ticket_id,
            "message": (
                "完整恢复文件已在本机生成；本地页面已打开下载入口，"
                "恢复文件路径未返回给 Agent。"
            ),
        }
    )
    return 0


def _settings(args: argparse.Namespace) -> int:
    access_token = secrets.token_urlsafe(24)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    log_directory = Path(
        os.environ.get("ANXINAI_HOME", str(Path.home() / ".anxinai"))
    ).expanduser() / "workbuddy" / "logs"
    log_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    process_options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        process_options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        process_options["start_new_session"] = True
    with (log_directory / "settings.log").open("ab") as log:
        process_options["stdout"] = log
        process_options["stderr"] = log
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "veilgate_agent.settings_server",
                access_token,
                str(port),
            ],
            **process_options,
        )
    url = f"http://127.0.0.1:{port}/settings?token={access_token}"
    for _ in range(50):
        try:
            with urlopen(url, timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    else:
        raise WorkBuddyReviewError("本地设置页启动失败，请检查 settings.log。")
    if not args.no_open:
        webbrowser.open(url, new=2)
    _print(
        {
            "schema_version": 1,
            "status": "settings_opened",
            "message": "Agent 保护设置页已在本机打开。",
        }
    )
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="anxin-gate",
        description="AnxinAI Agent 本地文件安全闸门",
    )
    root.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = root.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="本地检查文件并打开审核页")
    scan.add_argument("path")
    scan.add_argument(
        "--no-open",
        action="store_true",
        help="启动审核服务但不自动打开浏览器",
    )
    scan.set_defaults(handler=_scan)

    status = commands.add_parser("status", help="查询本地审核状态")
    status.add_argument("ticket_id")
    status.set_defaults(handler=_status)

    open_review = commands.add_parser("open", help="重新打开待处理的审核页")
    open_review.add_argument("ticket_id")
    open_review.set_defaults(handler=_open)

    restore = commands.add_parser(
        "restore",
        help="把 Agent 的占位符回复恢复到一次性本地页面",
    )
    restore.add_argument("ticket_id")
    restore.add_argument("safe_response_path")
    restore.add_argument("--no-open", action="store_true")
    restore.set_defaults(handler=_restore)

    restore_file = commands.add_parser(
        "restore-file",
        help="在本机恢复 Agent 生成的完整占位符文件并打开下载页",
    )
    restore_file.add_argument("ticket_id")
    restore_file.add_argument("safe_output_path")
    restore_file.add_argument("--no-open", action="store_true")
    restore_file.set_defaults(handler=_restore_file)

    settings = commands.add_parser("settings", help="打开本地 Agent 保护设置页")
    settings.add_argument("--no-open", action="store_true")
    settings.set_defaults(handler=_settings)
    return root


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        raise SystemExit(args.handler(args))
    except WorkBuddyReviewError as error:
        _print(
            {
                "schema_version": 1,
                "status": "error",
                "message": str(error),
            }
        )
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
