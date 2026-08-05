from __future__ import annotations

import argparse
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from urllib.parse import parse_qs, urlparse

from ._settings import (
    ALL_RULE_IDS,
    FIXED_BLOCKERS,
    RULE_GROUPS,
    load_settings,
    save_settings,
)


def settings_html(
    token: str,
    *,
    saved: bool = False,
) -> str:
    settings = load_settings()
    enabled = set(settings.enabled_rules)
    groups = []
    for group in RULE_GROUPS:
        rows = "".join(
            (
                '<label class="rule">'
                f'<input type="checkbox" name="rule" value="{escape(rule.id)}"'
                f'{" checked" if rule.id in enabled else ""}>'
                f"<span><strong>{escape(rule.label)}</strong>"
                f"<small>{escape(rule.description)}</small></span></label>"
            )
            for rule in group.rules
        )
        groups.append(
            '<section class="group">'
            f"<header><div><h2>{escape(group.label)}</h2>"
            f"<p>{escape(group.description)}</p></div>"
            f'<button type="button" class="select-group">全选</button></header>'
            f'<div class="rules">{rows}</div></section>'
        )
    blockers = "".join(
        f"<div class=\"locked\"><strong>{escape(label)}</strong>"
        f"<small>{escape(description)}</small><b>固定</b></div>"
        for _kind, label, description in FIXED_BLOCKERS
    )
    credential_block = " checked" if settings.credential_action == "block" else ""
    credential_tokenize = " checked" if settings.credential_action == "tokenize" else ""
    saved_notice = (
        '<div class="saved">设置已保存在本机，将从下一次扫描或模型请求开始生效。</div>'
        if saved
        else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; form-action 'self'; base-uri 'none'">
  <title>安心AI Agent 保护设置</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:#f4f8f6; color:#15362b; font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif; }}
    main {{ width:min(960px,calc(100% - 28px)); margin:30px auto 80px; }}
    h1 {{ margin:0; font-size:30px; }} h2 {{ margin:0; font-size:17px; }}
    .lead,.group p {{ color:#64786e; margin:3px 0 0; }}
    .card {{ background:white; border:1px solid #dce8e1; border-radius:17px; padding:20px; margin-top:18px; box-shadow:0 8px 28px #153a2810; }}
    .summary {{ display:inline-block; margin:12px 0; padding:5px 11px; border-radius:999px; background:#e9f8f0; color:#087747; font-weight:700; }}
    .group {{ border-top:1px solid #e5eee9; padding:18px 0; }} .group:first-of-type {{ border-top:0; }}
    header {{ display:flex; justify-content:space-between; gap:16px; align-items:center; }}
    .rules,.locked-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:9px; margin-top:12px; }}
    .rule {{ display:grid; grid-template-columns:auto 1fr; gap:10px; min-height:70px; padding:11px 12px; border:1px solid #dce8e1; border-radius:11px; cursor:pointer; }}
    .rule:has(input:checked) {{ border-color:#15935c; background:#f0fbf5; }}
    .policy {{ display:grid; grid-template-columns:auto 1fr; gap:10px; padding:12px; border:1px solid #dce8e1; border-radius:11px; cursor:pointer; }}
    .policy:has(input:checked) {{ border-color:#15935c; background:#f0fbf5; }}
    .policy-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:9px; margin-top:12px; }}
    input {{ accent-color:#087b4b; margin-top:5px; }} strong,small {{ display:block; }} small {{ color:#6a7c73; }}
    button {{ border:1px solid #cad9d1; border-radius:9px; padding:8px 12px; background:white; color:inherit; cursor:pointer; }}
    .locked {{ position:relative; padding:11px 48px 11px 12px; border:1px solid #eedad4; border-radius:11px; background:#fff8f6; }}
    .locked b {{ position:absolute; right:11px; top:10px; color:#a44b39; font-size:12px; }}
    .savebar {{ position:sticky; bottom:0; display:flex; justify-content:space-between; align-items:center; gap:12px; padding:14px 18px; border:1px solid #dce8e1; border-radius:14px; background:#fffffff2; box-shadow:0 -8px 28px #153a2814; }}
    .primary {{ color:white; background:#087b4b; border-color:#087b4b; font-weight:700; }}
    .saved {{ margin-top:16px; padding:12px 14px; border-radius:11px; background:#e8f8ef; color:#087747; }}
    @media(max-width:720px) {{ .rules,.locked-grid,.policy-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>◆ 安心AI Agent 保护设置</h1>
  <p class="lead">社区版配置只保存在本机，不包含激活、支付或遥测逻辑。</p>
  {saved_notice}
  <form method="post" action="/settings">
    <input type="hidden" name="token" value="{escape(token)}">
    <section class="card">
      <h2>可配置脱敏规则</h2>
      <p class="lead">每条规则均可单独启停；分类全选只是快捷操作。</p>
      <span class="summary" id="count"></span>
      {''.join(groups)}
    </section>
    <section class="card">
      <h2>密钥与账号凭据</h2>
      <p class="lead">密码、Token 和 API Key 默认阻断；如业务确需交给 AI，可降级为本地替换，并在结果返回后恢复。</p>
      <div class="policy-grid">
        <label class="policy"><input type="radio" name="credential_action" value="block"{credential_block}><span><strong>阻断（推荐）</strong><small>发现明确凭据后停止本次处理，并提示调整策略。</small></span></label>
        <label class="policy"><input type="radio" name="credential_action" value="tokenize"{credential_tokenize}><span><strong>脱敏并可恢复</strong><small>只替换密钥值，保留字段名称；本地映射支持恢复。</small></span></label>
      </div>
    </section>
    <section class="card">
      <h2>固定安全阻断</h2>
      <p class="lead">命中后停止本次处理，不提供关闭开关。</p>
      <div class="locked-grid">{blockers}</div>
    </section>
    <div class="savebar"><span>配置只写入 ~/.anxinai/settings.json</span><button class="primary" type="submit">保存设置</button></div>
  </form>
</main>
<script>
  const inputs=[...document.querySelectorAll('input[name="rule"]')], count=document.querySelector('#count');
  const refresh=()=>count.textContent=`已启用 ${{inputs.filter(x=>x.checked).length}} / {len(ALL_RULE_IDS)} 条脱敏规则`;
  for(const input of inputs) input.addEventListener('change',refresh);
  for(const button of document.querySelectorAll('.select-group')) button.addEventListener('click',()=>{{
    const group=button.closest('.group'), items=[...group.querySelectorAll('input[name="rule"]')];
    const next=!items.every(item=>item.checked); for(const item of items)item.checked=next; refresh();
  }});
  refresh();
</script>
</body>
</html>"""


class SettingsHandler(BaseHTTPRequestHandler):
    server_version = "AnxinAISettings/0.1"

    @property
    def access_token(self) -> str:
        return str(getattr(self.server, "access_token"))

    def _is_local_request(self) -> bool:
        try:
            client = ipaddress.ip_address(str(self.client_address[0]))
        except ValueError:
            return False
        if not client.is_loopback:
            return False
        parsed = urlparse(f"//{self.headers.get('Host', '')}")
        try:
            host = ipaddress.ip_address(parsed.hostname or "")
        except ValueError:
            return (parsed.hostname or "").lower() == "localhost"
        return host.is_loopback

    def _send_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        token = parse_qs(parsed.query).get("token", [""])[0]
        if (
            not self._is_local_request()
            or parsed.path != "/settings"
            or token != self.access_token
        ):
            self._send_html("Not found", HTTPStatus.NOT_FOUND)
            return
        self._send_html(settings_html(self.access_token))

    def do_POST(self) -> None:  # noqa: N802
        if not self._is_local_request() or self.path != "/settings":
            self._send_html("Not found", HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 64 * 1024:
            self._send_html("Bad request", HTTPStatus.BAD_REQUEST)
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        if form.get("token", [""])[0] != self.access_token:
            self._send_html("Forbidden", HTTPStatus.FORBIDDEN)
            return
        save_settings(
            form.get("rule", []),
            credential_action=form.get("credential_action", ["block"])[0],
        )
        self._send_html(settings_html(self.access_token, saved=True))

    def log_message(self, _format: str, *_args: object) -> None:
        return


def serve_settings(
    access_token: str,
    *,
    port: int = 0,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), SettingsHandler)
    server.access_token = access_token  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("access_token")
    parser.add_argument("port", type=int)
    args = parser.parse_args(argv)
    server = serve_settings(args.access_token, port=args.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
