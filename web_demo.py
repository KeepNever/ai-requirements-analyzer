#!/usr/bin/env python3
"""Web 演示界面（零依赖，标准库实现，图表用 ECharts CDN）。

用法（项目根目录）：
  python web_demo.py            # http://127.0.0.1:8000

功能：
  - 首页列出 output/ 下全部已有案例报告，点击直接查看
  - 报告页：report.md 正文 + 四维雷达图 + 优先级矩阵散点 + 能力匹配分布
  - Dashboard（/dashboard）：六指标四数据集对比 + 逐案例表现
  - 粘贴或上传客户材料（md），在线跑真实分析
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

# 现役口径（Prompt v1.7.1 + GT v1.1/v1.0）的最新快照
METRIC_SNAPSHOTS = {
    "MAIN": "metrics_m4gtreview_main.json",
    "DEV": "metrics_m4gtreview_dev.json",
    "HOLD": "metrics_m4gtreview_hold.json",
    "FINAL": "metrics_m4final_final.json",
}
METRIC_KEYS = ["①提取F1均值", "②关键遗漏率均值", "③引用错位嫌疑均值",
               "⑤优先级一致率均值", "⑥能力匹配一致率均值", "⑦引用完整性均值"]
METRIC_SHORT = ["①提取F1", "②关键遗漏", "③引用错位", "⑤优先级一致", "⑥能力匹配", "⑦引用完整"]

PAGE_CSS = """
  :root { --bg:#f7f8fc; --panel:#fff; --panel2:#eef1f8; --line:#e2e6f0;
    --blue:#2b5cff; --blues:#2f54eb; --text:#1f2430; --muted:#5f6b80; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:"PingFang SC","Microsoft YaHei",sans-serif;
    line-height:1.7; padding:40px 20px 80px; }
  .wrap { max-width:960px; margin:0 auto; }
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
  input[type=file] { border:1px solid var(--line); border-radius:8px; padding:7px 10px;
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
  .chart { width:100%; height:360px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .err { color:#c9404e; background:rgba(201,64,78,.08); border:1px solid rgba(201,64,78,.25);
    border-radius:8px; padding:10px 14px; font-size:13.5px; margin-top:12px; display:none; }
  .topnav a { margin-right:16px; color:var(--blues); text-decoration:none; font-size:14px; font-weight:600; }
  @media (max-width:700px){ .grid2 { grid-template-columns:1fr; } }
"""

HOME_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 客户需求分析助手 · Web 演示</title><style>__CSS__</style></head>
<body><div class="wrap">
<div class="tag">产 品 演 示</div>
<h1>AI 客户需求分析助手</h1>
<div class="sub topnav"><a href="/">报告列表</a><a href="/dashboard">评测 Dashboard</a></div>
<div class="sub">上传一份客户材料（会议转写 / 邮件 / 微信记录），自动生成结构化需求分析报告</div>

<div class="panel">
<h2>① 已有分析报告（点击查看，报告页含可视化图表）</h2>
<div class="list">__CASES__</div>
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
document.getElementById('f').addEventListener('submit', function() {
  var b = document.getElementById('btn');
  b.disabled = true; b.textContent = '分析中，请稍候…（约 1~2 分钟）';
});
</script>
</body></html>"""

REPORT_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · 需求分析报告</title><style>__CSS__</style>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
</head>
<body><div class="wrap">
<p><a class="back" href="/">← 返回首页</a>　<a class="back" href="/dashboard">评测 Dashboard →</a></p>
<div class="panel">
<h2>优先级可视化（AI 评分）</h2>
<div class="grid2">
  <div id="radar" class="chart"></div>
  <div id="matrix" class="chart"></div>
</div>
<div id="capbar" class="chart" style="height:220px"></div>
</div>
<div class="panel md" id="content"><em>报告加载中…</em></div>
<script>
var raw = __RAW__;
var ana = __ANA__;
if (window.marked) { document.getElementById('content').innerHTML = marked.parse(raw); }
else { document.getElementById('content').innerHTML = '<pre style="white-space:pre-wrap;font-size:13px">' + raw + '</pre>'; }

if (window.echarts && ana.priority) {
  var C = { high:'#2f54eb', mid:'#b26a00', low:'#8a93a6' };
  var tierColor = function(t) { return C[t] || C.low; };
  var pri = ana.priority.slice().sort(function(a,b){ return b.weighted - a.weighted; }).slice(0, 6);

  var radar = echarts.init(document.getElementById('radar'));
  var radarData = pri.map(function(r) {
    return { name: r.id + ' ' + r.tier, value: [r.impact, r.urgency, r.coverage, r.signal],
      itemStyle: { color: tierColor(r.tier) }, lineStyle: { width: 1.5 }, areaStyle: { opacity: .08 } };
  });
  radar.setOption({
    title: { text: '四维评分雷达（Top6）', left: 'center', textStyle: { fontSize: 13, color: '#5f6b80' } },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    radar: { indicator: [{ name: '影响', max: 5 }, { name: '紧迫', max: 5 }, { name: '覆盖', max: 5 }, { name: '信号', max: 5 }], radius: '58%' },
    series: [{ type: 'radar', data: radarData }]
  });

  var byTier = {};
  ana.priority.forEach(function(r) { var t = r.tier || '低'; (byTier[t] = byTier[t] || []).push(r); });
  var matrix = echarts.init(document.getElementById('matrix'));
  var matrixSeries = Object.keys(byTier).map(function(t) {
    return { name: t, type: 'scatter', itemStyle: { color: tierColor(t), opacity: .85 },
      symbolSize: 14 + byTier[t][0].weighted * 3.5,
      data: byTier[t].map(function(r) { return [r.urgency, r.impact, r.id]; }),
      label: { show: true, formatter: function(p) { return p.data[2]; }, fontSize: 10, color: '#333' },
      labelLayout: { hideOverlap: true } };
  });
  matrix.setOption({
    title: { text: '优先级矩阵（紧迫 × 影响）', left: 'center', textStyle: { fontSize: 13, color: '#5f6b80' } },
    legend: { bottom: 0, textStyle: { fontSize: 11 } },
    grid: { top: 36, left: 44, right: 24, bottom: 52 },
    xAxis: { name: '紧迫', nameLocation: 'middle', min: 0, max: 5, nameTextStyle: { fontSize: 11 }, splitLine: { lineStyle: { color: '#e2e6f0' } } },
    yAxis: { name: '影响', min: 0, max: 5, splitLine: { lineStyle: { color: '#e2e6f0' } } },
    series: matrixSeries
  });

  var cnt = {};
  ((ana.node2 && ana.node2.capability_match) || []).forEach(function(c) {
    var v = c.verdict || '其他'; cnt[v] = (cnt[v] || 0) + 1;
  });
  var order = ['匹配', '部分匹配', '待确认', '不支持'].filter(function(k) { return cnt[k]; });
  var capColor = { '匹配': '#16855c', '部分匹配': '#b26a00', '待确认': '#8a93a6', '不支持': '#c9404e' };
  var cap = echarts.init(document.getElementById('capbar'));
  cap.setOption({
    title: { text: 'F7 能力匹配分布（对照产品能力清单）', left: 'center', textStyle: { fontSize: 13, color: '#5f6b80' } },
    grid: { top: 36, left: 60, right: 40, bottom: 24 },
    xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#e2e6f0' } } },
    yAxis: { type: 'category', data: order, textStyle: { fontSize: 12 } },
    series: [{ type: 'bar', barWidth: 22,
      data: order.map(function(k) {
        return { value: cnt[k], itemStyle: { color: capColor[k] }, label: { show: true, position: 'right' } };
      }) }]
  });
  window.addEventListener('resize', function() { radar.resize(); matrix.resize(); cap.resize(); });
}
</script>
</div></body></html>"""

DASHBOARD_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>评测 Dashboard</title><style>__CSS__</style>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
</head>
<body><div class="wrap">
<p><a class="back" href="/">← 返回首页</a></p>
<div class="tag">评 测 驱 动 迭 代</div>
<h1>评测 Dashboard</h1>
<div class="sub">现役口径：Prompt v1.7.1 · 评测器 v1.5 · GT v1.1/v1.0 ｜ 17 案例三层验证集，双人盲评校准</div>

<div class="panel"><div id="agg" class="chart" style="height:400px"></div></div>
<div class="panel"><div id="case56" class="chart" style="height:520px"></div></div>
<div class="panel"><div id="case17" class="chart" style="height:420px"></div></div>
<div class="hint" style="margin-top:0">口径说明：⑤ 优先级一致率为已知弱项（两轮 Prompt 实验未达标已挂账，详见 docs/experiments/24、25）；FINAL 为正式盲测集，用后已转固定回归集身份。</div>
</div>
<script>
var D = __DATA__;
var g1 = echarts.init(document.getElementById('agg'));
g1.setOption({
  title: { text: '六指标 × 四数据集（均值）', left: 'center', textStyle: { fontSize: 14 } },
  legend: { top: 28, textStyle: { fontSize: 11 } },
  grid: { top: 64, left: 44, right: 24, bottom: 70 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'category', data: D.keys, axisLabel: { rotate: 18, fontSize: 11, color: '#5f6b80' } },
  yAxis: { type: 'value', max: 1, axisLabel: { color: '#5f6b80' }, splitLine: { lineStyle: { color: '#e2e6f0' } } },
  series: D.datasets.map(function(d) {
    return { name: d.name, type: 'bar', barGap: 0, data: d.agg, itemStyle: { color: d.color },
      label: { show: true, position: 'top', fontSize: 9, color: '#5f6b80',
        formatter: function(p) { return p.value == null ? '—' : p.value; } } };
  })
});

var g2 = echarts.init(document.getElementById('case56'));
g2.setOption({
  title: { text: '逐案例 ⑤ 优先级一致率（越短越偏）', left: 'center', textStyle: { fontSize: 14 } },
  grid: { top: 40, left: 190, right: 60, bottom: 30 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'value', max: 1, axisLabel: { color: '#5f6b80' }, splitLine: { lineStyle: { color: '#e2e6f0' } } },
  yAxis: { type: 'category', inverse: true, data: D.case_names, axisLabel: { fontSize: 11, color: '#1f2430' } },
  series: [
    { name: '⑤ 优先级一致', type: 'bar', stack: 'a', data: D.case_5, itemStyle: { color: '#2f54eb' },
      label: { show: true, position: 'right', fontSize: 10, color: '#5f6b80' }, barWidth: 14 },
    { name: '缺口', type: 'bar', stack: 'a', data: D.case_5.map(function(v) { return v == null ? 0 : Math.max(0, 1 - v); }),
      itemStyle: { color: '#eef1f8' }, tooltip: { show: false }, barWidth: 14 }
  ]
});

var g3 = echarts.init(document.getElementById('case17'));
g3.setOption({
  title: { text: '逐案例 ① 提取F1 与 ⑥ 能力匹配', left: 'center', textStyle: { fontSize: 14 } },
  legend: { top: 28, textStyle: { fontSize: 11 } },
  grid: { top: 56, left: 190, right: 60, bottom: 30 },
  tooltip: { trigger: 'axis' },
  xAxis: { type: 'value', max: 1, axisLabel: { color: '#5f6b80' }, splitLine: { lineStyle: { color: '#e2e6f0' } } },
  yAxis: { type: 'category', inverse: true, data: D.case_names, axisLabel: { fontSize: 11, color: '#1f2430' } },
  series: [
    { name: '① 提取F1', type: 'bar', data: D.case_1, itemStyle: { color: '#2f54eb' }, barWidth: 8,
      label: { show: true, position: 'right', fontSize: 9, color: '#5f6b80' } },
    { name: '⑥ 能力匹配', type: 'bar', data: D.case_6, itemStyle: { color: '#16855c' }, barWidth: 8,
      label: { show: true, position: 'right', fontSize: 9, color: '#5f6b80' } }
  ]
});

window.addEventListener('resize', function() { g1.resize(); g2.resize(); g3.resize(); });
</script>
</body></html>"""

DS_COLORS = {"MAIN": "#2f54eb", "DEV": "#7a5cd6", "HOLD": "#16855c", "FINAL": "#b26a00"}


def load_snapshots():
    ev = PROJECT_ROOT / "output" / "evaluation"
    datasets, case_names, case_1, case_5, case_6 = [], [], [], [], []
    for ds, fname in METRIC_SNAPSHOTS.items():
        p = ev / fname
        if not p.exists():
            datasets.append({"name": ds, "agg": [None] * len(METRIC_KEYS), "color": DS_COLORS[ds]})
            continue
        m = json.loads(p.read_text(encoding="utf-8"))
        agg = m.get("aggregate", {})
        datasets.append({"name": ds, "agg": [agg.get(k) for k in METRIC_KEYS], "color": DS_COLORS[ds]})
        for cid, c in m.get("per_case", {}).items():
            short = re.sub(r"^(case|dev|hold|final)_", "", cid).split("_")[0]
            case_names.append(f"[{ds}] {short}")

            def rate(key):
                v = c.get(key)
                if isinstance(v, dict):
                    return v.get("f1", v.get("率"))
                return None
            case_1.append(rate("①提取F1"))
            case_5.append(rate("⑤优先级一致性"))
            case_6.append(rate("⑥能力匹配准确率"))
    return {"keys": METRIC_SHORT, "datasets": datasets, "case_names": case_names,
            "case_1": case_1, "case_5": case_5, "case_6": case_6}


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


def render(template: str, **kw: str) -> str:
    body = template
    for k, v in kw.items():
        body = body.replace(f"__{k.upper()}__", v)
    return body


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, code: int = 200, ctype: str = "text/html; charset=utf-8"):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            self._send(render(HOME_PAGE, css=PAGE_CSS, cases=list_cases()))
        elif path == "/dashboard":
            data = json.dumps(load_snapshots(), ensure_ascii=False)
            self._send(render(DASHBOARD_PAGE, css=PAGE_CSS, data=data))
        elif path.startswith("/api/case/"):
            name = path.split("/api/case/", 1)[1]
            p = PROJECT_ROOT / "output" / name / "analysis.json"
            if not p.exists():
                self._send("{}", 404, "application/json; charset=utf-8")
                return
            self._send(p.read_text(encoding="utf-8"), 200, "application/json; charset=utf-8")
        elif path.startswith("/report/"):
            name = path.split("/report/", 1)[1]
            report = PROJECT_ROOT / "output" / name / "report.md"
            ana = PROJECT_ROOT / "output" / name / "analysis.json"
            if not report.exists():
                self._send("<h1>报告不存在</h1><p><a href='/'>返回</a></p>", 404)
                return
            raw = json.dumps(report.read_text(encoding="utf-8"), ensure_ascii=False)
            a = ana.read_text(encoding="utf-8") if ana.exists() else "{}"
            self._send(render(REPORT_PAGE, title=html.escape(name), css=PAGE_CSS, raw=raw, ana=a))
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
            self._send(render(HOME_PAGE, css=PAGE_CSS, cases=list_cases())
                       .replace("</div>\n</div>\n<script>",
                                f'<div class="err" style="display:block">运行失败：{msg}</div></div>\n</div>\n<script>'))


def main():
    port = 8000
    print(f"[Web] AI 客户需求分析助手演示界面：http://127.0.0.1:{port}")
    print("[Web] 评测 Dashboard：http://127.0.0.1:8000/dashboard")
    print("[Web] Ctrl+C 退出")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
