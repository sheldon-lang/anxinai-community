from __future__ import annotations

import argparse
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

from ._workbuddy import (
    WorkBuddyReviewError,
    decide_review,
    expire_review,
    load_ticket,
    update_ticket,
)


def _review_html(payload: dict[str, Any], token: str) -> str:
    status = str(payload.get("status", "pending"))
    counts = dict(payload.get("counts", {}))
    count_rows = "".join(
        f"<li><span>{escape(str(label))}</span><strong>{int(count)}</strong></li>"
        for label, count in counts.items()
    )
    mapping_rows = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('label_name', item.get('label', '敏感信息'))))}</td>"
        f"<td class=\"original\">{escape(str(item.get('original', '')))}</td>"
        f"<td><code>{escape(str(item.get('placeholder', '')))}</code>"
        f"<small>{'Nym' if item.get('source') == 'nym' else '规则'}</small></td>"
        "</tr>"
        for item in payload.get("mappings", [])
    )
    if not mapping_rows:
        mapping_rows = (
            '<tr><td colspan="3" class="empty">未检测到规则覆盖的敏感信息，'
            "仍请人工检查安全版。</td></tr>"
        )
    decision_area = ""
    engine_status = dict(payload.get("engine_status", {}))
    nym_state = "已启用" if engine_status.get("nym") else "未启用"
    enabled_rule_count = int(engine_status.get("enabled_rule_count", 0))
    warning_items = [
        *(str(item) for item in payload.get("warnings", [])),
    ]
    warnings = (
        '<div class="warnings">'
        + "".join(f"<p>⚠ {escape(item)}</p>" for item in warning_items)
        + "</div>"
        if warning_items
        else ""
    )
    if status == "pending":
        decision_area = f"""
        <form method="post" action="/decision">
          <input type="hidden" name="token" value="{escape(token)}">
          <button class="secondary" name="decision" value="cancelled">取消</button>
          <button class="primary" name="decision" value="approved">确认使用安全版</button>
        </form>
        """
    elif status == "approved":
        decision_area = '<div class="result approved">已确认。现在可以回到 Agent 继续任务。</div>'
    elif status == "cancelled":
        decision_area = '<div class="result cancelled">已取消。Agent 不会获得安全版路径。</div>'
    else:
        decision_area = (
            f'<div class="result cancelled">{escape(str(payload.get("message", "审核已失效。")))}</div>'
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'">
  <title>AnxinAI 本地文件审核</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f3f8f5; color: #10251d; }}
    main {{ width: min(920px, calc(100% - 32px)); margin: 32px auto; }}
    .badge {{ display: inline-flex; gap: 8px; align-items: center; color: #087a4f; font-weight: 700; }}
    .badge::before {{ content: ""; width: 9px; height: 9px; border-radius: 50%; background: #08aa70; }}
    h1 {{ margin: 14px 0 8px; font-size: clamp(28px, 5vw, 46px); }}
    .lead {{ color: #52655d; margin-bottom: 24px; }}
    .card {{ background: white; border: 1px solid #dce8e1; border-radius: 18px; padding: 22px; box-shadow: 0 18px 60px rgba(18, 63, 44, .08); margin-bottom: 18px; }}
    .file {{ font-size: 18px; font-weight: 750; word-break: break-all; }}
    ul {{ list-style: none; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
    li {{ background: #f1f7f3; padding: 12px 14px; border-radius: 12px; display: flex; justify-content: space-between; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e4ece7; padding: 12px 8px; vertical-align: top; }}
    .original {{ color: #9b2c23; word-break: break-all; }}
    code {{ color: #087a4f; word-break: break-all; }}
    small {{ display: block; margin-top: 5px; color: #728079; }}
    .notice {{ padding: 14px 16px; border-radius: 12px; background: #fff7e7; color: #704b00; margin: 16px 0; }}
    .warnings {{ padding: 10px 16px; border-radius: 12px; background: #fff0ee; color: #8c3027; margin: 16px 0; }}
    .warnings p {{ margin: 6px 0; }}
    form {{ display: flex; justify-content: flex-end; gap: 12px; }}
    button {{ border-radius: 12px; padding: 13px 18px; font-weight: 750; cursor: pointer; }}
    .secondary {{ border: 1px solid #cfdad4; background: white; color: #20352c; }}
    .primary {{ border: 1px solid #078252; background: #078252; color: white; }}
    .result {{ padding: 15px 18px; border-radius: 12px; font-weight: 700; }}
    .approved {{ background: #e6f7ef; color: #087a4f; }}
    .cancelled {{ background: #fff0ee; color: #9b2c23; }}
    .empty {{ color: #65756e; text-align: center; padding: 28px; }}
    footer {{ color: #6b7c74; font-size: 13px; text-align: center; margin: 20px; }}
  </style>
</head>
<body>
  <main>
    <div class="badge">AnxinAI · 仅在本机处理</div>
    <h1>发送给 Agent 之前，先检查安全版本</h1>
    <p class="lead">原文件尚未交给 Agent。下面的原始值只显示在这个本地页面。</p>
    <section class="card">
      <div class="file">{escape(str(payload.get("source_name", "")))}</div>
      <p>共检测到 <strong>{int(payload.get("finding_count", 0))}</strong> 项敏感信息</p>
      <p>检测引擎：已启用 {enabled_rule_count} 条中文脱敏规则 · Nym Edge INT8 {nym_state} · 安全版格式 {escape(str(payload.get("output_format", "")))}</p>
      <p><code>如需调整，请运行 anxin-gate settings；新设置从下一次扫描生效。</code></p>
      {warnings}
      <ul>{count_rows or "<li><span>未命中规则</span><strong>0</strong></li>"}</ul>
    </section>
    <section class="card">
      <h2>本地替换明细</h2>
      <div class="notice">请确认替换范围是否合理。确认后 Agent 只会获得安全版路径。</div>
      <table>
        <thead><tr><th>类型</th><th>本地原值</th><th>安全占位符</th></tr></thead>
        <tbody>{mapping_rows}</tbody>
      </table>
    </section>
    <section class="card">{decision_area}</section>
    <footer>页面由 127.0.0.1 提供，不加载任何外部资源。</footer>
  </main>
</body>
</html>"""


class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "AnxinAIReview/0.1"

    @property
    def ticket_id(self) -> str:
        return str(getattr(self.server, "ticket_id"))

    @property
    def access_token(self) -> str:
        return str(getattr(self.server, "access_token"))

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

    def _is_local_request(self) -> bool:
        port = int(self.server.server_address[1])
        try:
            client_is_loopback = ipaddress.ip_address(
                str(self.client_address[0])
            ).is_loopback
        except ValueError:
            client_is_loopback = False
        if not client_is_loopback:
            return False

        def is_loopback_authority(authority: str) -> bool:
            try:
                parsed = urlparse(f"//{authority}")
                authority_port = parsed.port
            except ValueError:
                return False
            hostname = (parsed.hostname or "").rstrip(".").lower()
            if authority_port != port:
                return False
            if hostname == "localhost":
                return True
            try:
                return ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                return False

        if not is_loopback_authority(self.headers.get("Host", "")):
            return False
        origin = self.headers.get("Origin")
        if origin is None or origin == "null":
            return True
        parsed_origin = urlparse(origin)
        return (
            parsed_origin.scheme == "http"
            and not parsed_origin.username
            and not parsed_origin.password
            and is_loopback_authority(parsed_origin.netloc)
        )

    def do_GET(self) -> None:  # noqa: N802
        if not self._is_local_request():
            self._send_html("<h1>仅允许本机访问</h1>", HTTPStatus.FORBIDDEN)
            return
        parsed = urlparse(self.path)
        token = parse_qs(parsed.query).get("token", [""])[0]
        if parsed.path != f"/review/{self.ticket_id}" or token != self.access_token:
            self._send_html("<h1>审核链接无效</h1>", HTTPStatus.FORBIDDEN)
            return
        try:
            payload = load_ticket(self.ticket_id)
        except WorkBuddyReviewError as error:
            self._send_html(f"<h1>{escape(str(error))}</h1>", HTTPStatus.NOT_FOUND)
            return
        self._send_html(_review_html(payload, self.access_token))

    def do_POST(self) -> None:  # noqa: N802
        if not self._is_local_request():
            self._send_html("<h1>仅允许本机提交</h1>", HTTPStatus.FORBIDDEN)
            return
        if self.path != "/decision":
            self._send_html("<h1>未找到操作</h1>", HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        token = form.get("token", [""])[0]
        decision = form.get("decision", [""])[0]
        try:
            payload = decide_review(self.ticket_id, token, decision)
        except WorkBuddyReviewError as error:
            self._send_html(f"<h1>{escape(str(error))}</h1>", HTTPStatus.FORBIDDEN)
            return
        self._send_html(_review_html(payload, self.access_token))
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args: object) -> None:
        return


def serve_review(
    ticket_id: str,
    access_token: str,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), ReviewHandler)
    setattr(server, "ticket_id", ticket_id)
    setattr(server, "access_token", access_token)
    actual_port = int(server.server_address[1])
    review_url = (
        f"http://{host}:{actual_port}/review/{ticket_id}?token={access_token}"
    )
    update_ticket(ticket_id, review_url=review_url)
    return server


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticket_id")
    parser.add_argument("access_token")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server = serve_review(args.ticket_id, args.access_token, port=args.port)

    def expire_and_shutdown() -> None:
        try:
            expire_review(args.ticket_id, args.access_token)
        finally:
            server.shutdown()

    expiry = threading.Timer(
        30 * 60,
        expire_and_shutdown,
    )
    expiry.daemon = True
    expiry.start()
    server.serve_forever(poll_interval=0.25)
    expiry.cancel()
    server.server_close()


if __name__ == "__main__":
    main()
