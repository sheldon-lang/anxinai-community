from __future__ import annotations

import argparse
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import mimetypes
from pathlib import Path
import threading
from urllib.parse import parse_qs, quote, urlparse

from ._workbuddy import (
    WorkBuddyReviewError,
    delete_restore_session,
    load_restore_session,
    update_restore_session,
)


def _restore_html(payload: dict[str, object], token: str) -> str:
    source_name = str(payload.get("source_name", ""))
    result_kind = str(payload.get("result_kind", "text"))
    restored_text = str(payload.get("restored_text", ""))
    session_id = str(payload.get("session_id", ""))
    restored_file_name = str(payload.get("restored_file_name", ""))
    if result_kind == "file":
        result_area = f"""
        <section class="card">
          <h2>完整恢复文件已准备好</h2>
          <p class="file-name">{escape(restored_file_name)}</p>
          <p>文件中的安全占位符已在本机替换回原值。Agent 只处理过安全版，
          不会获得这个恢复文件的路径。</p>
          <a class="primary" href="/download/{escape(session_id)}?token={escape(token)}">
            打开或下载完整恢复文件
          </a>
        </section>
        """
        explanation = (
            "这是 Agent 输出文件的本地恢复版本，不是重新把原文件发送给模型。"
        )
    else:
        result_area = (
            f'<section class="card"><pre>{escape(restored_text)}</pre></section>'
        )
        explanation = "这是 Agent 占位符回复在本机恢复后的正式内容。"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
  <title>AnxinAI 本地恢复结果</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f3f8f5; color: #10251d; }}
    main {{ width: min(920px, calc(100% - 28px)); margin: 28px auto; }}
    .badge {{ color: #087a4f; font-weight: 750; }}
    h1 {{ margin: 12px 0 8px; font-size: clamp(28px, 5vw, 44px); }}
    .lead {{ color: #52655d; }}
    .card {{ margin-top: 22px; background: white; border: 1px solid #dce8e1; border-radius: 18px; padding: 22px; box-shadow: 0 18px 60px rgba(18,63,44,.08); }}
    h2 {{ margin-top: 0; }}
    pre {{ margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 15px/1.75 ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .notice {{ margin-top: 18px; padding: 13px 15px; border-radius: 12px; background: #e6f7ef; color: #087a4f; }}
    .steps {{ display: grid; gap: 10px; padding-left: 22px; color: #42564d; }}
    .file-name {{ padding: 12px 14px; border-radius: 10px; background: #f1f7f3; overflow-wrap: anywhere; }}
    .primary {{ display: inline-block; margin-top: 10px; padding: 13px 18px; border-radius: 12px; background: #078252; color: white; text-decoration: none; font-weight: 750; }}
  </style>
</head>
<body>
  <main>
    <div class="badge">AnxinAI · 基础模式 · 仅在本机恢复</div>
    <h1>这是恢复后的正式结果</h1>
    <p class="lead">来源：{escape(source_name)}。{escape(explanation)}</p>
    <section class="card">
      <h2>这次处理发生了什么</h2>
      <ol class="steps">
        <li>原文件只在本机扫描，并生成带安全占位符的版本。</li>
        <li>Agent 只读取和处理安全版，模型没有获得本地原值。</li>
        <li>任务完成后，AnxinAI 在本机把占位符恢复为原值。</li>
      </ol>
    </section>
    {result_area}
    <div class="notice">页面不加载外部资源。下载入口保留 30 分钟；已经保存到本机的完整恢复文件不会自动删除。</div>
  </main>
</body>
</html>"""


class RestoreHandler(BaseHTTPRequestHandler):
    server_version = "AnxinAIRestore/0.1"

    def _is_local_request(self) -> bool:
        try:
            if not ipaddress.ip_address(str(self.client_address[0])).is_loopback:
                return False
        except ValueError:
            return False
        port = int(self.server.server_address[1])
        try:
            parsed = urlparse(f"//{self.headers.get('Host', '')}")
            hostname = (parsed.hostname or "").rstrip(".").lower()
            host_ok = parsed.port == port and (
                hostname == "localhost"
                or ipaddress.ip_address(hostname).is_loopback
            )
        except ValueError:
            host_ok = False
        return host_ok

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _send_restored_file(self, payload: dict[str, object]) -> None:
        path = Path(str(payload.get("restored_file_path", "")))
        if (
            payload.get("result_kind") != "file"
            or not path.is_file()
            or path.is_symlink()
        ):
            self._send_html("<h1>完整恢复文件不存在或已移动</h1>", HTTPStatus.GONE)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header(
            "Content-Disposition",
            f"attachment; filename=\"anxinai-restored{path.suffix}\"; "
            f"filename*=UTF-8''{quote(path.name)}",
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                self.wfile.write(chunk)

    def do_GET(self) -> None:  # noqa: N802
        if not self._is_local_request():
            self._send_html("<h1>仅允许本机访问</h1>", HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        token = parse_qs(parsed.query).get("token", [""])[0]
        session_id = str(getattr(self.server, "session_id"))
        access_token = str(getattr(self.server, "access_token"))
        if token != access_token:
            self._send_html("<h1>本地结果链接无效</h1>", HTTPStatus.FORBIDDEN)
            return
        try:
            payload = load_restore_session(session_id)
        except WorkBuddyReviewError as error:
            self._send_html(f"<h1>{escape(str(error))}</h1>", HTTPStatus.GONE)
            return
        if parsed.path == f"/result/{session_id}":
            self._send_html(_restore_html(payload, access_token))
            return
        if parsed.path == f"/download/{session_id}":
            self._send_restored_file(payload)
            return
        self._send_html("<h1>未找到本地结果</h1>", HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return


def serve_restore(
    session_id: str,
    access_token: str,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), RestoreHandler)
    setattr(server, "session_id", session_id)
    setattr(server, "access_token", access_token)
    actual_port = int(server.server_address[1])
    update_restore_session(
        session_id,
        restore_url=(
            f"http://{host}:{actual_port}/result/{session_id}"
            f"?token={access_token}"
        ),
    )
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("session_id")
    parser.add_argument("access_token")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = serve_restore(args.session_id, args.access_token, port=args.port)

    def expire_and_shutdown() -> None:
        delete_restore_session(args.session_id)
        server.shutdown()

    expiry = threading.Timer(30 * 60, expire_and_shutdown)
    expiry.daemon = True
    expiry.start()
    server.serve_forever(poll_interval=0.25)
    expiry.cancel()
    delete_restore_session(args.session_id)
    server.server_close()


if __name__ == "__main__":
    main()
