#!/usr/bin/env python3
"""Web 演示界面（零依赖，标准库实现）。

用法（项目根目录）：
  python web_demo.py            # http://127.0.0.1:8000

功能：
  - 首页列出 output/ 下全部已有案例报告，点击直接查看
  - 粘贴或上传客户材料（md），选择 mock / 真实调用，在线跑完整分析
  - 报告页渲染 report.md（marked.js，离线时退化为等宽文本）
"""
import html
import json
import re
import sys
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

UPLOAD_DIR = PROJECT_ROOT / "test_data" / "web_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

PAGE_CSS = """
  :root { --bg:#f7f8fc; --panel:#fff; --panel2:#eef1f8; --line:#e2e6f0;
    --blue:#2b5cff; --blues:#2f54eb; --text:#1f2430; --muted:#5f6b80; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:"PingFang SC","Microsoft YaHei",sans-serif;
    line-height:1.7; padding:40px 20px 80px; }
  .wrap { max-width:900px; margin:0 auto; }
  .tag { display:inline-block; padding:2px 12px; border:1px solid var(--blue); color:var(--blues);
    border-radius:999px; font-size:13px; margin-bottom:14px; letter-spacing:2px; background:#fff; }
  h1 { font-size:26px; margin-bottom:6px; }
  .sub { color:var(--muted); font-size:14px; margin-bottom:24px; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:20px 22px; margin-bottom:18px; box-shadow:0 1px 4px rgba(31,36,48,.05); }
  h2 { font-size:16px; margin-bottom:12px; padding-left:10px; border-left:4px solid var(--blue); }
  textarea { width:100%; min-height:180px; border:1px solid var(--line); border-radius:8px;
    padding:12px; font-size:13px; font-family:inherit; resize:vertical; background:var(--panel2); }
  textarea:focus { outline:2px solid var(--blue); border-color:transparent; }
  .row { display:flex; gap:12px; align-items:center; margin-top:12px; flex-wrap:wrap; }
  select, input[type=file] { border:1px solid var(--line); border-radius:8px; padding:7px 10px;
    font-size:13px; background:#fff; color:var(--text); }
  button { background:var(--blue); color:#fff; border:none; border-radius:8px; padding:9px 26px;
    font-size:14px; cursor:pointer; font-weight:600; }
  button:hover { background:var(--blues); }
  button:disabled { background:#a9b6d6; cursor:wait; }
  .list a { display:flex; justify-content:space-between; padding:10px 12px; border-bottom:1px solid var(--line);
    color:var(--text); text-decoration:none; font-size:14px; border-radius:6px; }
  .list a:hover { background:var(--panel2); }
  .list .meta { color:var(--muted); font-size:12px; }
  .hint { font-size:12.5px; color:var(--muted); margin-top:10px; }
  .md table { border-collapse:collapse; width:100%; font-size:13.5px; margin:10px 0; }
  .md th,.md td { border:1px solid var(--line); padding:7px 10px; text-align:left; }
  .md th { background:var(--panel2); }
  .md h1{font-size:22px} .md h2{font-size:18px;border:none;padding:0;margin:20px 0 10px}
  .md blockquote { border-left:3px solid var(--line); padding:4px 14px; color:var(--muted); margin:8px 0; }
  .back { color:var(--blues); text-decoration:none; font-size:13px; }
  .err { color:#c9404e; background:rgba(201,64,78,.08); border:1px solid rgba(201,64,78,.25);
    border-radius:8px; padding:10px 14px; font-size:13.5px; margin-top:12px; display:none; }
"""

HOME_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 客户需求分析助手 · Web 演示</title><style>{css}</style></head>
<body><div class="wrap">
<div class="tag">产 品 演 示</div>
<h1>AI 客户需求分析助手</h1>
<div class="sub">上传一份客户材料（会议转写 / 邮件 / 微信记录），自动生成结构化需求分析报告</div>

<div class="panel">
<h2>① 已有分析报告（点击查看）</h2>
<div class="list">{cases}</div>
</div>

<div class="panel">
<h2>② 跑一份新的（真实分析）</h2>
<form id="f" action="/run" method="post" enctype="multipart/form-data">
<textarea name="material" placeholder="把客户材料粘贴到这里。格式：每段一行，段前标 P1、P2、P3……（可参照已有案例格式）"></textarea>
<div class="row">
  <span style="font-size:13px;color:var(--muted)">或上传文件：</span>
  <input type="file" name="file" accept=".md,.txt">
  <button type="submit" id="btn">开始分析</button>
</div>
<div class="err" id="err"></div>
<div class="hint">调用 DeepSeek 真实分析，约 1~2 分钟、约 0.3 元，页面会自动等待并跳转到报告。报告为草稿态，高风险字段需人工终审。想先看效果：直接点击上面任意已有报告。</div>
</form>
</div>
</div>
<script>
document.getElementById('f').addEventListener('submit', function() {{
  var b = document.getElementById('btn');
  b.disabled = true; b.textContent = '分析中，请稍候…（约 1~2 分钟）';
}});
</script>
</body></html>"""

REPORT_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} · 需求分析报告</title><style>{css}</style>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
</head>
<body><div class="wrap">
<p><a class="back" href="/">← 返回首页</a></p>
<div class="panel md" id="content"><em>报告加载中…</em></div>
<script>
var raw = {raw};
if (window.marked) {{ document.getElementById('content').innerHTML = marked.parse(raw); }}
else {{ document.getElementById('content').innerHTML = '<pre style="white-space:pre-wrap;font-size:13px">' + raw + '</pre>'; }}
</script>
</div></body></html>"""


def list_cases() -> str:
    out = PROJECT_ROOT / "output"
    rows = []
    for d in sorted(out.iterdir(), reverse=True):
        if not d.is_dir() or d.name == "evaluation":
            continue
        report = d / "report.md"
        if not report.exists():
            continue
        mtime = time.strftime("%m-%d %H:%M", time.localtime(report.stat().st_mtime))
        rows.append(f'<a href="/report/{html.escape(d.name)}"><span>{html.escape(d.name)}'
                    f'</span><span class="meta">{mtime}</span></a>')
    return "\n".join(rows) or '<span class="meta">暂无报告，先跑一份新的</span>'


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, code: int = 200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(HOME_PAGE.format(css=PAGE_CSS, cases=list_cases()))
        elif self.path.startswith("/report/"):
            name = unquote(urlparse(self.path).path.split("/report/", 1)[1])
            report = PROJECT_ROOT / "output" / name / "report.md"
            if not report.exists():
                self._send("<h1>报告不存在</h1><p><a href='/'>返回</a></p>", 404)
                return
            raw = json.dumps(report.read_text(encoding="utf-8"), ensure_ascii=False)
            self._send(REPORT_PAGE.format(title=html.escape(name), css=PAGE_CSS, raw=raw))
        else:
            self._send("<h1>404</h1>", 404)

    def do_POST(self):
        if self.path != "/run":
            self._send("<h1>404</h1>", 404)
            return
        ctype = self.headers.get("Content-Type", "")
        boundary = re.search(r"boundary=([^;]+)", ctype)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        fields, filename, filedata = {}, None, b""
        if boundary:
            for part in body.split(("--" + boundary.group(1)).encode()):
                m = re.search(rb'name="([^"]+)"(?:; filename="([^"]+)")?', part)
                if not m:
                    continue
                name, fname = m.group(1).decode(), m.group(2)
                _, _, val = part.partition(b"\r\n\r\n")
                val = val.rsplit(b"\r\n", 1)[0]
                if fname:
                    filename, filedata = fname, val
                else:
                    fields[name] = val.decode("utf-8", "replace")
        material = (fields.get("material") or "").strip()
        if filename and filedata.strip():
            safe = re.sub(r'[\\/:*?"<>|]', "_", Path(filename).stem)[:40]
            material = filedata.decode("utf-8", "replace")
        elif material:
            safe = "粘贴材料_" + time.strftime("%m%d_%H%M%S")
        if not material:
            self._send("<h1>材料为空</h1><p><a href='/'>返回重试</a></p>", 400)
            return
        case_path = UPLOAD_DIR / f"web_{safe}_{uuid.uuid4().hex[:6]}.md"
        case_path.write_text(material, encoding="utf-8")

        from pipeline import workflow
        try:
            out_dir = workflow.run_case(str(case_path), mock=False)
            self.send_response(303)
            self.send_header("Location", f"/report/{out_dir.name}")
            self.end_headers()
        except Exception as e:  # noqa: BLE001
            msg = html.escape(f"{type(e).__name__}: {e}")
            self._send(HOME_PAGE.format(css=PAGE_CSS, cases=list_cases())
                       .replace("</div>\n</div>\n<script>",
                                f'<div class="err" style="display:block">运行失败：{msg}</div></div>\n</div>\n<script>'))


def main():
    port = 8000
    print(f"[Web] AI 客户需求分析助手演示界面：http://127.0.0.1:{port}")
    print("[Web] Ctrl+C 退出")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
