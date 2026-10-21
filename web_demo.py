#!/usr/bin/env python3
"""Web 演示界面（零依赖，标准库实现，图表用 ECharts CDN）。

用法（项目根目录）：
  python web_demo.py            # http://127.0.0.1:8000

工作台流程：输入脏材料 → ① 实时清洗预览（免费、可反复调）→ ② 运行完整分析
→ ③ 分节点查看中间产物（清洗/提取/分析）→ 报告页。
另有 /dashboard 评测看板。
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

from pipeline import cleaner  # noqa: E402
from pipeline import workflow as wf  # noqa: E402
import storage  # noqa: E402
import retrieval  # noqa: E402


def _safe_pid(pid) -> str:
    """项目 ID 安全校验（防路径穿越）。"""
    if not pid or storage.UNSAFE_ID_RE.search(str(pid)):
        raise ValueError(f"非法项目 ID：{pid!r}")
    return str(pid)


def _project_version_refs(pid: str) -> list:
    """取项目全部文档的当前版本组合（多材料引用式问答的定位依据）。"""
    tree = storage.get_project_tree(pid)
    if not tree:
        return []
    return [{"document_id": d["document_id"], "version_id": d["current_version_id"]}
            for d in tree["documents"] if d.get("current_version_id")]

UPLOAD_DIR = PROJECT_ROOT / "test_data" / "web_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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
    --blue:#2b5cff; --blues:#2f54eb; --text:#1f2430; --muted:#5f6b80;
    --green:#16855c; --amber:#b26a00; --red:#c9404e; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--text); font-family:"PingFang SC","Microsoft YaHei",sans-serif;
    line-height:1.7; padding:40px 20px 80px; }
  .wrap { max-width:1000px; margin:0 auto; }
  .tag { display:inline-block; padding:2px 12px; border:1px solid var(--blue); color:var(--blues);
    border-radius:999px; font-size:13px; margin-bottom:14px; letter-spacing:2px; background:#fff; }
  h1 { font-size:26px; margin-bottom:6px; }
  h2 { font-size:16px; margin-bottom:12px; padding-left:10px; border-left:4px solid var(--blue); }
  h3 { font-size:14px; margin:14px 0 8px; color:var(--blues); }
  .sub { color:var(--muted); font-size:14px; margin-bottom:24px; }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:20px 22px; margin-bottom:18px; box-shadow:0 1px 4px rgba(31,36,48,.05); }
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
  button.ghost { background:#fff; color:var(--blues); border:1px solid var(--blue); }
  .stat { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:12px 0; }
  .stat div { background:var(--panel2); border-radius:8px; padding:10px; text-align:center; }
  .stat b { font-size:20px; color:var(--blues); display:block; }
  .stat span { font-size:12px; color:var(--muted); }
  table { border-collapse:collapse; width:100%; font-size:13px; margin:8px 0; }
  th,td { border:1px solid var(--line); padding:6px 9px; text-align:left; vertical-align:top; }
  th { background:var(--panel2); color:var(--muted); font-weight:500; font-size:12.5px; }
  .list a { display:flex; justify-content:space-between; padding:10px 12px; border-bottom:1px solid var(--line);
    color:var(--text); text-decoration:none; font-size:14px; border-radius:6px; }
  .list a:hover { background:var(--panel2); }
  .list .meta { color:var(--muted); font-size:12px; }
  .hint { font-size:12.5px; color:var(--muted); margin-top:10px; }
  .md table { border-collapse:collapse; width:100%; font-size:13.5px; margin:10px 0; }
  .md th,.md td { border:1px solid var(--line); padding:7px 10px; }
  .md th { background:var(--panel2); }
  .md h1{font-size:22px} .md h2{font-size:18px;border:none;padding:0;margin:20px 0 10px}
  .md blockquote { border-left:3px solid var(--line); padding:4px 14px; color:var(--muted); margin:8px 0; }
  .back { color:var(--blues); text-decoration:none; font-size:13px; }
  .chart { width:100%; height:360px; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .err { color:var(--red); background:rgba(201,64,78,.08); border:1px solid rgba(201,64,78,.25);
    border-radius:8px; padding:10px 14px; font-size:13.5px; margin-top:12px; display:none; }
  .warn { color:var(--amber); background:rgba(178,106,0,.08); border:1px solid rgba(178,106,0,.25);
    border-radius:8px; padding:8px 12px; font-size:13px; margin-top:10px; display:none; }
  .ok { color:var(--green); background:rgba(22,133,92,.08); border:1px solid rgba(22,133,92,.25);
    border-radius:8px; padding:8px 12px; font-size:13px; margin-top:10px; }
  .pill { display:inline-block; padding:1px 10px; border-radius:999px; font-size:12px; font-weight:600; }
  .p-high { background:rgba(47,84,235,.10); color:var(--blues); }
  .p-mid { background:rgba(178,106,0,.10); color:var(--amber); }
  .p-low { background:rgba(95,107,128,.10); color:var(--muted); }
  .p-ok { background:rgba(22,133,92,.10); color:var(--green); }
  .p-tbd { background:rgba(95,107,128,.10); color:var(--muted); }
  .p-bad { background:rgba(201,64,78,.10); color:var(--red); }
  .seglist { max-height:340px; overflow-y:auto; border:1px solid var(--line); border-radius:8px; }
  .seg { padding:8px 12px; border-bottom:1px solid var(--line); font-size:12.5px; }
  .seg:last-child { border-bottom:none; }
  .seg .did { color:var(--blues); font-weight:700; margin-right:6px; }
  .seg .pid { color:var(--muted); font-size:11px; margin-left:6px; }
  .seg .scrub { color:var(--amber); font-size:11px; }
  .flowbar { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
  .flowbar .step { flex:1; min-width:120px; background:var(--panel2); border-radius:8px;
    padding:8px 10px; text-align:center; font-size:12.5px; color:var(--muted); }
  .flowbar .step.done { background:rgba(22,133,92,.10); color:var(--green); border:1px solid rgba(22,133,92,.25); }
  .flowbar .step b { display:block; font-size:13px; }
  ul.plain { padding-left:18px; font-size:13px; }
  ul.plain li { margin:3px 0; }
  .topnav a { margin-right:16px; color:var(--blues); text-decoration:none; font-size:14px; font-weight:600; }
  @media (max-width:700px){ .grid2{grid-template-columns:1fr} .stat{grid-template-columns:1fr 1fr} }
  .qa-grid { display:grid; grid-template-columns:minmax(0,1fr) 320px; gap:14px; align-items:start; }
  .ctx-item { padding:7px 0; border-bottom:1px solid var(--line); font-size:12.5px; }
  .ctx-item b { color:var(--blues); margin-right:6px; }
  .ctx-sec { font-size:12px; color:var(--muted); margin:12px 0 4px; letter-spacing:1px; }
  .list-link { display:flex; justify-content:space-between; padding:9px 12px; border-bottom:1px solid var(--line);
    color:var(--text); text-decoration:none; font-size:14px; border-radius:6px; }
  .list-link:hover { background:var(--panel2); }
  @media (max-width:900px){ .qa-grid { grid-template-columns:1fr; } }
"""

HOME_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 客户需求分析助手 · 工作台</title><style>__CSS__</style></head>
<body><div class="wrap">
<div class="tag">产 品 工 作 台</div>
<h1>AI 客户需求分析助手</h1>
<div class="sub topnav"><a href="/">工作台</a><a href="/library">资料库</a><a href="/chat">AI 问答</a><a href="/dashboard">评测 Dashboard</a></div>

<div class="panel">
<h2>① 输入原始材料</h2>
<textarea id="mat" placeholder="把客户的会议转写 / 邮件 / 微信记录原样粘贴到这里，越脏越真实越好。格式：每段一行，段前标 P1、P2、P3……（不标也能清洗，但引用回链会缺失）"></textarea>
<div class="row">
  <span style="font-size:13px;color:var(--muted)">或上传文件：</span>
  <input type="file" id="file" accept=".md,.txt">
  <button class="ghost" id="btnClean" onclick="doClean()">① 清洗预览（免费·秒出）</button>
  <button id="btnRun" onclick="doRun()" disabled>② 运行完整分析（约 0.3 元）</button>
</div>
<div class="err" id="err"></div>
<div class="warn" id="warn"></div>
<div class="hint">清洗为纯代码节点，实时执行不消耗 API；确认材料质量后再运行完整分析（提取 + 分析 + 报告，约 1~2 分钟）。</div>
</div>

<div class="panel" id="cleanPanel" style="display:none">
<h2>② 清洗结果（节点 1 · 纯代码）</h2>
<div class="stat" id="cleanStats"></div>
<div id="cleanWarn"></div>
<h3>原始段落 → 清洗后编号（点击报告中的 D 编号即回链到这里）</h3>
<div class="seglist" id="segList"></div>
</div>

<div class="panel">
<h2>③ 已有分析报告（点开即看，报告页含图表）</h2>
<div class="list">__CASES__</div>
</div>
</div>
<script>
var cleaned = false;
function showErr(msg) { var e = document.getElementById('err'); e.textContent = msg; e.style.display = 'block'; }
function getMaterial() {
  var t = document.getElementById('mat').value.trim();
  if (!t) { showErr('请先粘贴材料，或上传文件'); return null; }
  return t;
}
function fetchFile(cb) {
  var f = document.getElementById('file').files[0];
  if (!f) { cb(); return; }
  var r = new FileReader();
  r.onload = function() { document.getElementById('mat').value = r.result; cb(); };
  r.readAsText(f, 'utf-8');
}
function doClean() {
  document.getElementById('err').style.display = 'none';
  fetchFile(function() {
    var t = getMaterial(); if (!t) return;
    fetch('/api/clean', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ material: t }) })
    .then(function(r){ return r.json(); })
    .then(function(d) {
      if (d.error) { showErr(d.error); return; }
      cleaned = true;
      document.getElementById('btnRun').disabled = false;
      var p = document.getElementById('cleanPanel'); p.style.display = 'block';
      var s = d.stats;
      document.getElementById('cleanStats').innerHTML =
        '<div><b>' + s['输入块数'] + '</b><span>输入块数</span></div>' +
        '<div><b>' + s['解析段落数'] + '</b><span>解析段落数</span></div>' +
        '<div><b>' + s['PII脱敏段落数'] + '</b><span>PII 脱敏段落</span></div>' +
        '<div><b>' + s['刷屏合并数'] + '</b><span>刷屏合并</span></div>';
      var w = document.getElementById('warn');
      if (d.warnings.length) { w.innerHTML = d.warnings.join('<br>'); w.style.display = 'block'; }
      else { w.style.display = 'none'; }
      document.getElementById('segList').innerHTML = d.segments.map(function(sg) {
        return '<div class="seg"><span class="did">' + sg.did + '</span>' +
          (sg.pii_scrubbed ? '<span class="scrub">[已脱敏] </span>' : '') +
          sg.text.replace(/</g,'&lt;') + '<span class="pid">← 原始 ' + sg.pid + '</span></div>';
      }).join('');
      p.scrollIntoView({ behavior:'smooth' });
    })
    .catch(function(e){ showErr('清洗失败：' + e); });
  });
}
function doRun() {
  if (!cleaned) return;
  var t = document.getElementById('mat').value.trim();
  var b = document.getElementById('btnRun');
  b.disabled = true; b.textContent = '分析中…（1~2 分钟，请勿关闭）';
  fetch('/run-text', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ material: t }) })
  .then(function(r){ return r.json().then(function(d){ return { status:r.status, d:d }; }); })
  .then(function(x) {
    if (x.status === 200 && x.d.case) { window.location.href = '/pipeline/' + encodeURIComponent(x.d.case); }
    else { showErr(x.d.error || '运行失败'); b.disabled = false; b.textContent = '② 运行完整分析（约 0.3 元）'; }
  })
  .catch(function(e){ showErr('请求失败：' + e); b.disabled = false; });
}
</script>
</body></html>"""

PIPELINE_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · 分析流水线</title><style>__CSS__</style>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
</head>
<body><div class="wrap">
<p><a class="back" href="/">← 返回工作台</a>　<a class="back" href="/report/__URLNAME__">完整报告（可阅读版）→</a>　<a class="back" href="/dashboard">评测 Dashboard</a></p>
<div class="tag">分 析 流 水 线</div>
<h1>__TITLE__</h1>
<div class="flowbar">
  <div class="step done"><b>✓ 节点1 清洗</b>纯代码 · PII脱敏 · 编号映射</div>
  <div class="step done"><b>✓ 节点2~3 提取</b>LLM + Schema 校验闸</div>
  <div class="step done"><b>✓ 节点4~7 分析</b>评分加权 + 能力匹配</div>
  <div class="step done"><b>✓ 节点8 报告</b>草稿态 · 待人工终审</div>
</div>
__BODY__
</div>
<script>
__CHART__
</script>
</body></html>"""

REPORT_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · 需求分析报告</title><style>__CSS__</style>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
</head>
<body><div class="wrap">
<p><a class="back" href="/">← 返回工作台</a>　<a class="back" href="/pipeline/__URLNAME__">流水线视图 →</a>　<a class="back" href="/dashboard">评测 Dashboard</a></p>
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
<p><a class="back" href="/">← 返回工作台</a></p>
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

LIBRARY_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>资料库 · AI 客户需求分析助手</title><style>__CSS__</style></head>
<body><div class="wrap">
<p><a class="back" href="/">← 返回工作台</a>　<a class="back" href="/dashboard">评测 Dashboard</a></p>
<div class="tag">资 料 库</div>
<h1>项目资料库</h1>
<div class="sub">项目 → 文档 → 版本 → 分析运行，全部历史可追溯、不可覆盖</div>
<div class="panel">
<div id="tree"><em>加载中…</em></div>
</div>
<div class="panel" id="detail" style="display:none">
<h2 id="detailTitle"></h2>
<div id="detailBody"></div>
</div>
</div>
<script>
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
fetch('/api/projects').then(r=>r.json()).then(function(projects){
  if (!projects.length) { document.getElementById('tree').innerHTML = '<em>资料库为空</em>'; return; }
  document.getElementById('tree').innerHTML = projects.map(function(p){
    return '<a class="list-link" href="/library/'+encodeURIComponent(p[0])+'" style="display:flex;justify-content:space-between;padding:10px 12px;border-bottom:1px solid var(--line);color:var(--text);text-decoration:none;font-size:14px;border-radius:6px" onmouseover="this.style.background=\\'var(--panel2)\\'" onmouseout="this.style.background=\\'\\'">'
      + '<span>'+esc(p[1].name)+'</span><span style="color:var(--muted);font-size:12px">'+esc(p[0])+'</span></a>';
  }).join('');
});
</script>
</body></html>"""

LIBRARY_PROJECT_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ · 资料库</title><style>__CSS__</style></head>
<body><div class="wrap">
<p><a class="back" href="/library">← 返回资料库</a>　<a class="back" href="/">工作台</a>　<a class="back" href="/dashboard">评测 Dashboard</a></p>
<div class="tag">项 目 资 料</div>
<h1>__TITLE__</h1>
<div class="sub">创建于 __CREATED__</div>
<div class="panel"><h2>文档与版本</h2><div id="docs"><em>加载中…</em></div></div>
<div class="panel"><h2>分析运行记录</h2><div id="runs"><em>加载中…</em></div></div>
<div class="panel" id="viewer" style="display:none">
<h2 id="viewerTitle"></h2>
<div id="viewerBody"></div>
</div>
</div>
<script>
var PID = __PID__;
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
fetch('/api/project/'+encodeURIComponent(PID)).then(r=>r.json()).then(function(t){
  document.getElementById('docs').innerHTML = t.documents.length ? t.documents.map(function(d){
    var vs = d.versions.map(function(v){
      return '<div style="padding:8px 12px;border-bottom:1px solid var(--line);font-size:13px">'
        + '<b style="color:var(--blues)">'+esc(v.version_id)+'</b>'
        + '　清洗器 '+esc(v.cleaner_version)
        + '　'+esc(v.created_at)
        + '　<button class="ghost" style="padding:3px 12px;font-size:12px" onclick="viewVersion(\\''+esc(d.document_id)+'\\',\\''+esc(v.version_id)+'\\')">查看原文 / 清洗对照</button></div>';
    }).join('');
    return '<div style="margin-bottom:14px"><b>'+esc(d.file_name)+'</b>'
      + '<span style="color:var(--muted);font-size:12px">　当前 '+esc(d.current_version_id)+'　'+esc(d.document_id)+'</span>'
      + '<div style="border:1px solid var(--line);border-radius:8px;margin-top:6px">'+vs+'</div></div>';
  }).join('') : '<em>无文档</em>';
  document.getElementById('runs').innerHTML = t.runs.length ? t.runs.map(function(r){
    var badge = r.status==='done' ? '<span class="pill p-ok">done</span>' : '<span class="pill p-mid">'+esc(r.status)+'</span>';
    var links = '　<a class="back" href="/pipeline/'+encodeURIComponent(r.project_id)+'">流水线</a>'
      + (r.report_exists ? '　<a class="back" href="/report/'+encodeURIComponent(r.project_id)+'">报告</a>' : '');
    return '<div style="padding:8px 0;border-bottom:1px solid var(--line);font-size:13px">'
      + '<b style="color:var(--blues)">'+esc(r.run_id)+'</b> '+badge
      + '　Prompt '+esc(r.prompt_version||'unknown')
      + '　'+esc(r.created_at) + links + '</div>';
  }).join('') : '<em>无运行记录</em>';
});
function viewVersion(docId, vid){
  fetch('/api/version/'+encodeURIComponent(PID)+'/'+encodeURIComponent(docId)+'/'+encodeURIComponent(vid))
  .then(r=>r.json()).then(function(d){
    var v = document.getElementById('viewer');
    v.style.display = 'block';
    document.getElementById('viewerTitle').textContent = docId + ' · ' + vid + ' 原文 / 清洗对照';
    document.getElementById('viewerBody').innerHTML = '<table><tr><th style="width:60px">原始段</th><th style="width:60px">清洗段</th><th>脱敏后文本（进 LLM 与展示）</th></tr>'
      + d.segments.map(function(s){
        var merged = (s.merged_from||[]).map(function(m){ return '<div style="color:var(--muted);font-size:11px">← 合并自 '+esc(m.pid)+'</div>'; }).join('');
        return '<tr><td>'+esc(s.pid)+merged+'</td><td><b style="color:var(--blues)">'+esc(s.did)+'</b></td><td>'+esc(s.clean_text)+'</td></tr>';
      }).join('') + '</table>';
    v.scrollIntoView({behavior:'smooth'});
  });
}
</script>
</body></html>"""

CHAT_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 问答 · 客户材料分析工作台</title><style>__CSS__</style></head>
<body><div class="wrap">
<p><a class="back" href="/">← 返回工作台</a>　<a class="back" href="/library">资料库</a>　<a class="back" href="/dashboard">评测 Dashboard</a></p>
<div class="tag">引 用 式 问 答 · 多 轮 对 话</div>
<h1>对项目材料提问</h1>
<div class="sub">支持多轮追问（代词与上下文自动衔接），答案只依据材料段落，每条结论带 D 编号引用回链；依据不足时明确提示，不编造。</div>

<div class="panel">
<div class="row" style="margin-top:0">
  <span style="font-size:13px;color:var(--muted)">项目：</span>
  <select id="proj" onchange="resetConv(); loadContext();" style="border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px;background:#fff"></select>
  <span style="font-size:13px;color:var(--muted)">模式：</span>
  <select id="mode" style="border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px;background:#fff">
    <option value="mock">离线演示（mock，不调用 API）</option>
    <option value="real" selected>真实问答（调用 DeepSeek）</option>
  </select>
  <span style="font-size:13px;color:var(--muted)">历史会话：</span>
  <select id="convSel" onchange="reopen(this.value)" style="border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px;background:#fff;max-width:260px">
    <option value="">新会话</option>
  </select>
  <button class="ghost" style="padding:6px 14px;font-size:12px" onclick="resetConv()">开新会话</button>
</div>
</div>

<div class="qa-grid">
<div class="panel">
<div id="chatlog" style="min-height:120px;max-height:480px;overflow-y:auto"></div>
<div id="empty" style="color:var(--muted);font-size:13px;padding:20px;text-align:center">输入第一个问题开始对话。追问时可以直接说「那预算呢？」，上下文自动衔接。</div>
<div class="row" style="border-top:1px solid var(--line);padding-top:12px">
  <textarea id="q" style="flex:1;min-height:60px" placeholder="输入问题，Enter 发送（Shift+Enter 换行）"></textarea>
  <button id="btn" onclick="ask()">发送</button>
</div>
<div class="err" id="err"></div>
</div>

<div class="panel" id="ctxPanel">
<h2>项目上下文</h2>
<div id="ctxBody" style="font-size:13px"><em>加载中…</em></div>
</div>
</div>
<script>
var currentConv = null;
var busy = false;
function esc(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
function loadContext(){
  var pid = document.getElementById('proj').value;
  fetch('/api/project-context/'+encodeURIComponent(pid)).then(r=>r.json()).then(function(c){
    var b = document.getElementById('ctxBody');
    if (c.error) { b.innerHTML = '<em>'+esc(c.error)+'</em>'; return; }
    b.innerHTML =
      '<div class="ctx-sec">文 档</div>'
      + (c.documents||[]).map(function(d){
          return '<div class="ctx-item"><b>'+esc(d.current_version_id)+'</b>'+esc(d.file_name)+'</div>'; }).join('')
      + '<div class="ctx-sec">显 性 需 求（AI 提取）</div>'
      + (c.explicit_needs||[]).slice(0,6).map(function(e){
          return '<div class="ctx-item"><b>'+esc(e.id)+'</b>'+esc(e.content).slice(0,60)+'</div>'; }).join('')
      + '<div class="ctx-sec">最 近 运 行</div>'
      + (c.recent_runs||[]).map(function(r){
          return '<div class="ctx-item"><b>'+esc(r.run_id.slice(0,18))+'</b>'+esc(r.status)+'</div>'; }).join('')
      + '<div class="ctx-sec">快 捷 入 口</div>'
      + '<div class="ctx-item"><a class="back" href="/library/'+encodeURIComponent(pid)+'">→ 资料库（版本历史）</a></div>'
      + '<div class="ctx-item"><a class="back" href="/report/'+encodeURIComponent(pid)+'">→ 分析报告</a></div>'
      + '<div class="ctx-item"><a class="back" href="/pipeline/'+encodeURIComponent(pid)+'">→ 分析流水线</a></div>';
  });
}
function loadProjects(){
  fetch('/api/projects').then(r=>r.json()).then(function(ps){
    document.getElementById('proj').innerHTML = ps.map(function(p){
      return '<option value="'+esc(p[0])+'">'+esc(p[1].name)+'</option>'; }).join('');
    loadConvList();
  });
}
function loadConvList(){
  var pid = document.getElementById('proj').value;
  fetch('/api/conversations/'+encodeURIComponent(pid)).then(r=>r.json()).then(function(cs){
    var sel = document.getElementById('convSel');
    sel.innerHTML = '<option value="">新会话</option>' + cs.map(function(c){
      return '<option value="'+esc(c.conversation_id)+'"'+(c.conversation_id===currentConv?' selected':'')+'>'
        + esc(c.conversation_id)+'</option>'; }).join('');
  });
}
function resetConv(){ currentConv = null; document.getElementById('chatlog').innerHTML=''; document.getElementById('empty').style.display='block'; loadConvList(); }
function reopen(cid){
  currentConv = cid;
  fetch('/api/conversation/'+encodeURIComponent(document.getElementById('proj').value)+'/'+encodeURIComponent(cid))
  .then(r=>r.json()).then(function(d){
    var log = document.getElementById('chatlog');
    log.innerHTML = '';
    document.getElementById('empty').style.display = 'none';
    (d.messages||[]).forEach(function(m){
      if (m.role === 'user') appendBubble('user', esc(m.content), '');
      else appendBubble('assistant', esc(m.answer), m);
    });
    log.scrollTop = log.scrollHeight;
  });
}
function badge(sufficient){
  return sufficient ? '<span class="pill p-ok">有依据</span>' : '<span class="pill p-bad">证据不足</span>';
}
function citesHtml(citations){
  return (citations||[]).map(function(c){
    return (c.source_excerpts||[]).map(function(e){
      return '<div style="border:1px solid var(--line);border-radius:8px;padding:6px 10px;margin:5px 0;font-size:12.5px">'
        + '<span class="did" style="color:var(--blues);font-weight:700;margin-right:6px">'+esc(e.source_paragraph_id)+'</span>'
        + esc(e.text) + '</div>';
    }).join('');
  }).join('') || '<div style="color:var(--muted);font-size:12.5px">无引用（证据不足）</div>';
}
function appendBubble(role, text, meta){
  var log = document.getElementById('chatlog');
  document.getElementById('empty').style.display = 'none';
  var div = document.createElement('div');
  if (role === 'user') {
    div.style.cssText = 'text-align:right;margin:10px 0';
    div.innerHTML = '<span style="display:inline-block;background:var(--blue);color:#fff;border-radius:12px;padding:7px 14px;font-size:13.5px;max-width:75%;text-align:left">'+text+'</span>';
  } else {
    var extra = meta ? '<div style="margin-top:6px">'+badge(meta.sufficient)
      + (meta.invalid_dids && meta.invalid_dids.length ? ' <span style="font-size:12px;color:var(--red)">⚠ 已剔除无效引用：'+esc(meta.invalid_dids.join('、'))+'</span>' : '') + '</div>'
      + '<div style="margin-top:8px"><b style="font-size:12.5px;color:var(--muted)">引用来源</b>' + citesHtml(meta.citations) + '</div>'
      + '<div style="font-size:11.5px;color:var(--muted);margin-top:4px">检索命中 ' + (meta.hits||[]).length + ' 段</div>' : '';
    div.style.cssText = 'margin:10px 0';
    div.innerHTML = '<span style="display:inline-block;background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:9px 14px;font-size:13.5px;max-width:85%;text-align:left;white-space:pre-wrap">'+text+'</span>' + extra;
  }
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}
function ask(){
  if (busy) return;
  var q = document.getElementById('q').value.trim();
  if (!q) { var e=document.getElementById('err'); e.style.display='block'; e.textContent='请输入问题'; return; }
  document.getElementById('err').style.display='none';
  busy = true;
  var b = document.getElementById('btn'); b.disabled = true; b.textContent = '思考中…';
  appendBubble('user', esc(q), null);
  document.getElementById('q').value = '';
  var log = document.getElementById('chatlog');
  var wait = document.createElement('div');
  wait.id = 'waiting'; wait.style.cssText = 'color:var(--muted);font-size:13px;margin:8px 0';
  wait.textContent = '检索材料段落并生成引用式回答…';
  log.appendChild(wait); log.scrollTop = log.scrollHeight;
  fetch('/api/ask', { method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ project_id: document.getElementById('proj').value, question: q,
      mock: document.getElementById('mode').value === 'mock', conversation_id: currentConv }) })
  .then(r=>r.json()).then(function(d){
    document.getElementById('waiting').remove();
    busy = false; b.disabled = false; b.textContent = '发送';
    if (d.error) { appendBubble('assistant', '⚠ ' + esc(d.error), null); return; }
    currentConv = d.conversation_id;
    appendBubble('assistant', esc(d.answer), d);
    loadConvList();
  }).catch(function(e){
    document.getElementById('waiting').remove();
    busy = false; b.disabled = false; b.textContent = '发送';
    appendBubble('assistant', '⚠ 请求失败：' + esc(String(e)), null);
  });
}
document.getElementById('q').addEventListener('keydown', function(ev){
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); ask(); }
});
loadProjects(); loadContext();
</script>
</body></html>"""
DS_COLORS = {"MAIN": "#2f54eb", "DEV": "#7a5cd6", "HOLD": "#16855c", "FINAL": "#b26a00"}

TIER_PILL = {"高": "p-high", "中": "p-mid", "低": "p-low"}
VERDICT_PILL = {"匹配": "p-ok", "部分匹配": "p-mid", "待确认": "p-tbd", "不支持": "p-bad"}


def run_clean(material: str) -> dict:
    """节点1 清洗：纯代码、零成本。返回统计 + 段落对照 + 兜底警告。"""
    data = cleaner.clean(material)
    segs = [{"pid": s.pid, "did": s.did, "text": s.text, "pii_scrubbed": s.pii_scrubbed}
            for s in data.segments]
    cleaned_len = len(data.cleaned_text())
    warnings = []
    if cleaned_len < wf.MIN_MATERIAL_CHARS:
        warnings.append(f"⚠ 清洗后仅 {cleaned_len} 字符（少于 {wf.MIN_MATERIAL_CHARS}），材料不足，正式分析会被拒收——请补充更多内容")
    elif cleaned_len > wf.MAX_MATERIAL_CHARS:
        warnings.append(f"⚠ 清洗后 {cleaned_len} 字符（超过 {wf.MAX_MATERIAL_CHARS}），请先人工分段或摘要后再分析")
    else:
        warnings.append(f"✓ 清洗后 {cleaned_len} 字符，可进入正式分析")
    return {"stats": data.stats, "segments": segs, "cleaned_len": cleaned_len, "warnings": warnings}


def save_material(material: str) -> str:
    safe = "web_" + time.strftime("%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    case_path = UPLOAD_DIR / f"{safe}.md"
    case_path.write_text(material, encoding="utf-8")
    return case_path.stem


def load_case(name: str):
    out = PROJECT_ROOT / "output" / name
    ana = out / "analysis.json"
    mp = out / "mapping.json"
    if not ana.exists():
        return None
    a = json.loads(ana.read_text(encoding="utf-8"))
    m = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    return a, m


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
        rows.append(f'<a href="/pipeline/{html.escape(d.name)}"><span>{html.escape(d.name)}'
                    f'</span><span class="meta">{mtime}</span></a>')
    return "\n".join(rows) or '<span class="meta">暂无报告，先在工作台跑一份</span>'


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def build_pipeline_body(a: dict, m: dict) -> tuple[str, str]:
    """节点产物卡片 + ECharts 配置。"""
    stats = m.get("stats", {})
    mapping = m.get("mapping", [])
    node1, node2, priority = a.get("node1", {}), a.get("node2", {}), a.get("priority", [])
    caps = node2.get("capability_match", [])

    # 节点1 卡片
    seg_rows = "".join(
        f"<tr><td>{esc(x['pid'])}</td><td><b style='color:#2f54eb'>{esc(x['did'])}</b></td></tr>"
        for x in mapping)
    body = f"""
<div class="panel">
<h2>节点 1 · 清洗（纯代码）</h2>
<div class="stat">
  <div><b>{stats.get('输入块数', '—')}</b><span>输入块数</span></div>
  <div><b>{stats.get('解析段落数', '—')}</b><span>解析段落数</span></div>
  <div><b>{stats.get('PII脱敏段落数', '—')}</b><span>PII 脱敏段落</span></div>
  <div><b>{stats.get('刷屏合并数', '—')}</b><span>刷屏合并</span></div>
</div>
<details><summary style="cursor:pointer;font-size:13px;color:#5f6b80">P ↔ D 编号映射表（{len(mapping)} 段）</summary>
<table><tr><th>原始编号</th><th>清洗编号</th></tr>{seg_rows}</table></details>
</div>
"""

    # 节点2 提取卡片
    n1 = ""
    for k, label in [("customer_background", "客户背景"), ("business_goals", "业务目标")]:
        items = node1.get(k) or []
        if items:
            n1 += f"<h3>{label}</h3><ul class='plain'>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"
    en = node1.get("explicit_needs") or []
    if en:
        rows = "".join(f"<tr><td><b>{esc(e.get('id'))}</b></td><td>{esc(e.get('content'))}</td>"
                       f"<td>{esc('、'.join(e.get('dids', []) or []))}</td></tr>" for e in en)
        n1 += f"<h3>显性需求（{len(en)} 条，带原文回链）</h3><table><tr><th>#</th><th>需求</th><th>出处</th></tr>{rows}</table>"
    inf = node1.get("inferred_needs") or []
    if inf:
        rows = "".join(f"<tr><td>{esc(i.get('id'))}</td><td>{esc(i.get('content'))}</td>"
                       f"<td><span class='pill p-{{}}'>".replace("p-{}", "p-tbd") + f"{esc(i.get('confidence'))}</span></td></tr>" for i in inf)
        n1 += f"<h3>潜在需求（推断）</h3><table><tr><th>#</th><th>推断</th><th>置信</th></tr>{rows}</table>"
    gaps = node1.get("info_gaps") or node1.get("gaps") or []
    if gaps:
        rows = ""
        for g in gaps:
            lvl = g.get("level") or g.get("级别") or ""
            cls = "p-bad" if "决定" in str(lvl) else ("p-mid" if "关键" in str(lvl) else "p-tbd")
            rows += (f"<tr><td>{esc(g.get('gap') or g.get('缺口'))}</td>"
                     f"<td><span class='pill {cls}'>{esc(lvl)}</span></td>"
                     f"<td>{esc(g.get('impact') or g.get('影响'))}</td>"
                     f"<td>{esc(g.get('question') or g.get('追问'))}</td></tr>")
        n1 += f"<h3>待确认信息（缺口 → 追问）</h3><table><tr><th>缺口</th><th>级别</th><th>影响</th><th>追问</th></tr>{rows}</table>"
    body += f"""
<div class="panel">
<h2>节点 2~3 · 信息提取（LLM + 校验闸）</h2>
{n1 or '<em>无 node1 数据</em>'}
</div>
"""

    # 节点4~7 分析卡片
    pr = ""
    if priority:
        rows = "".join(
            f"<tr><td>{esc(r['id'])}</td><td>{esc(r.get('content'))[:44]}</td>"
            f"<td>{r.get('impact')}</td><td>{r.get('urgency')}</td><td>{r.get('coverage')}</td><td>{r.get('signal')}</td>"
            f"<td><b>{r.get('weighted')}</b></td><td><span class='pill {TIER_PILL.get(r.get('tier'), 'p-low')}'>{esc(r.get('tier'))}</span></td></tr>"
            for r in priority)
        pr = (f"<table><tr><th>#</th><th>需求</th><th>影响</th><th>紧迫</th><th>覆盖</th><th>信号</th><th>加权</th><th>档位</th></tr>"
              f"{rows}</table>")
    cap_rows = "".join(
        f"<tr><td>{esc(c.get('target'))}</td>"
        f"<td><span class='pill {VERDICT_PILL.get(c.get('verdict'), 'p-tbd')}'>{esc(c.get('verdict'))}</span></td>"
        f"<td>{esc(c.get('reason'))}</td></tr>" for c in caps)
    body += f"""
<div class="panel">
<h2>节点 4~7 · 需求分析（四维加权 + 能力匹配）</h2>
{pr}
<h3>能力匹配裁定</h3>
<table><tr><th>对象</th><th>结论</th><th>依据</th></tr>{cap_rows}</table>
<div id="radar" class="chart"></div>
</div>
"""

    # 图表：雷达 + 矩阵（复用报告页逻辑，精简为一张雷达）
    chart = """
if (window.echarts) {
  var ana = __ANA__;
  var C = { high:'#2f54eb', mid:'#b26a00', low:'#8a93a6' };
  var pri = (ana.priority||[]).slice().sort(function(a,b){ return b.weighted - a.weighted; }).slice(0, 6);
  var radar = echarts.init(document.getElementById('radar'));
  radar.setOption({
    title: { text: '四维评分雷达（Top6）', left: 'center', textStyle: { fontSize: 13, color: '#5f6b80' } },
    legend: { bottom: 0, type: 'scroll', textStyle: { fontSize: 11 } },
    radar: { indicator: [{ name: '影响', max: 5 }, { name: '紧迫', max: 5 }, { name: '覆盖', max: 5 }, { name: '信号', max: 5 }], radius: '55%' },
    series: [{ type: 'radar', data: pri.map(function(r) {
      return { name: r.id + ' ' + r.tier, value: [r.impact, r.urgency, r.coverage, r.signal],
        itemStyle: { color: C[r.tier] || C.low }, areaStyle: { opacity: .08 } }; }) }]
  });
  window.addEventListener('resize', function() { radar.resize(); });
}
""".replace("__ANA__", json.dumps({"priority": priority}, ensure_ascii=False))
    return body, chart


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

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False), code, "application/json; charset=utf-8")

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            self._send(render(HOME_PAGE, css=PAGE_CSS, cases=list_cases()))
        elif path == "/dashboard":
            self._send(render(DASHBOARD_PAGE, css=PAGE_CSS,
                              data=json.dumps(load_snapshots(), ensure_ascii=False)))
        elif path == "/library":
            self._send(render(LIBRARY_PAGE, css=PAGE_CSS))
        elif path.startswith("/library/"):
            pid = path.split("/library/", 1)[1]
            tree = storage.get_project_tree(pid)
            if tree is None:
                self._send("<h1>项目不存在</h1><p><a href='/library'>返回资料库</a></p>", 404)
                return
            self._send(render(LIBRARY_PROJECT_PAGE, css=PAGE_CSS, title=html.escape(pid),
                              created=html.escape(tree["project"].get("created_at", "")),
                              pid=json.dumps(pid, ensure_ascii=False)))
        elif path == "/api/projects":
            self._json(storage.list_projects())
        elif path.startswith("/api/project/"):
            pid = path.split("/api/project/", 1)[1]
            tree = storage.get_project_tree(pid)
            if tree is None:
                self._json({"error": "项目不存在"}, 404)
                return
            self._json(tree)
        elif path == "/chat":
            self._send(render(CHAT_PAGE, css=PAGE_CSS))
        elif path.startswith("/api/conversations/"):
            pid = path.split("/api/conversations/", 1)[1]
            self._json(retrieval.list_conversations(pid))
        elif path.startswith("/api/conversation/"):
            _, rest = path.split("/api/conversation/", 1)
            pid, cid = rest.split("/", 1)
            msg_file = storage._resolve_under_base(
                "projects", pid, "conversations", cid) / "messages.json"
            if not msg_file.exists():
                self._json({"error": "会话不存在"}, 404)
                return
            self._json(json.loads(msg_file.read_text(encoding="utf-8")))
        elif path.startswith("/api/project-context/"):
            pid = path.split("/api/project-context/", 1)[1]
            tree = storage.get_project_tree(pid)
            if tree is None:
                self._json({"error": "项目不存在"}, 404)
                return
            needs, runs = [], []
            # tree["runs"] 已按最近在前排序；取最近一条含 analysis.json 的运行
            for r in tree["runs"]:
                ana = storage._resolve_under_base(pid, "analysis_runs", r["run_id"], "analysis.json")
                if ana.exists():
                    a = json.loads(ana.read_text(encoding="utf-8"))
                    needs = (a.get("node1", {}) or {}).get("explicit_needs", [])
                    break
            for r in tree["runs"][:3]:
                runs.append({"run_id": r["run_id"], "status": r.get("status")})
            self._json({"documents": tree["documents"], "explicit_needs": needs,
                        "recent_runs": runs})
        elif path.startswith("/api/version/"):
            _, rest = path.split("/api/version/", 1)
            pid, doc_id, vid = rest.split("/", 2)
            vdir = storage._documents_dir(pid) / doc_id / "versions" / vid
            if not (vdir / "clean.json").exists():
                self._json({"error": "版本不存在"}, 404)
                return
            self._json(json.loads((vdir / "clean.json").read_text(encoding="utf-8")))
        elif path.startswith("/api/case/"):
            name = path.split("/api/case/", 1)[1]
            got = load_case(name)
            if got is None:
                self._json({"error": "案例不存在"}, 404)
                return
            a, m = got
            self._json({"analysis": a, "mapping": m})
        elif path.startswith("/pipeline/"):
            name = path.split("/pipeline/", 1)[1]
            got = load_case(name)
            if got is None:
                self._send("<h1>案例不存在</h1><p><a href='/'>返回</a></p>", 404)
                return
            a, m = got
            body, chart = build_pipeline_body(a, m)
            self._send(render(PIPELINE_PAGE, title=html.escape(name), css=PAGE_CSS,
                              urlname=name, body=body, chart=chart))
        elif path.startswith("/report/"):
            name = path.split("/report/", 1)[1]
            report = PROJECT_ROOT / "output" / name / "report.md"
            ana = PROJECT_ROOT / "output" / name / "analysis.json"
            if not report.exists():
                self._send("<h1>报告不存在</h1><p><a href='/'>返回</a></p>", 404)
                return
            raw = json.dumps(report.read_text(encoding="utf-8"), ensure_ascii=False)
            a = ana.read_text(encoding="utf-8") if ana.exists() else "{}"
            self._send(render(REPORT_PAGE, title=html.escape(name), css=PAGE_CSS,
                              urlname=name, raw=raw, ana=a))
        else:
            self._send("<h1>404</h1>", 404)

    def do_POST(self):
        path = unquote(urlparse(self.path).path)
        if path == "/api/ask":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                question = (payload.get("question") or "").strip()
                if not question:
                    self._json({"error": "问题为空"}, 400)
                    return
                pid = _safe_pid(payload.get("project_id"))
                version_refs = _project_version_refs(pid)
                if not version_refs:
                    self._json({"error": "项目无可用文档版本"}, 400)
                    return
                r = retrieval.answer_question(
                    pid, version_refs, question,
                    mock=bool(payload.get("mock")),
                    conversation_id=payload.get("conversation_id"))
                fe = storage.citations_for_frontend(
                    {"citations": r["citations"]}, pid)
                self._json({"answer": r["answer"], "sufficient": r["sufficient"],
                            "cited_dids": r["cited_dids"], "invalid_dids": r["invalid_dids"],
                            "hits": r["hits"], "frontend_citations": fe["citations"],
                            "conversation_id": r["conversation_id"]})
            except Exception as e:  # noqa: BLE001
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        if path == "/api/clean":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                material = (payload.get("material") or "").strip()
                if not material:
                    self._json({"error": "材料为空"}, 400)
                    return
                self._json(run_clean(material))
            except Exception as e:  # noqa: BLE001
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        if path == "/run-text":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                material = (payload.get("material") or "").strip()
                if not material:
                    self._json({"error": "材料为空"}, 400)
                    return
                stem = save_material(material)
                wf.run_case(str(UPLOAD_DIR / f"{stem}.md"), mock=False)
                self._json({"case": stem})
            except Exception as e:  # noqa: BLE001
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            return
        if path == "/run":
            # 兼容旧表单（multipart）：直接跑完整分析
            ctype = self.headers.get("Content-Type", "")
            boundary = re.search(r"boundary=([^;]+)", ctype)
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            material = ""
            if boundary:
                for part in body.split(("--" + boundary.group(1)).encode()):
                    m = re.search(rb'name="material"', part)
                    if not m:
                        continue
                    _, _, val = part.partition(b"\r\n\r\n")
                    material = val.rsplit(b"\r\n", 1)[0].decode("utf-8", "replace").strip()
                    break
            if not material:
                self._send("<h1>材料为空</h1>", 400)
                return
            stem = save_material(material)
            try:
                wf.run_case(str(UPLOAD_DIR / f"{stem}.md"), mock=False)
                self.send_response(303)
                self.send_header("Location", f"/pipeline/{stem}")
                self.end_headers()
            except Exception as e:  # noqa: BLE001
                self._send(f"<h1>运行失败</h1><p>{html.escape(str(e))}</p><p><a href='/'>返回</a></p>", 500)
            return
        self._send("<h1>404</h1>", 404)


def main():
    port = 8000
    print(f"[Web] 工作台：http://127.0.0.1:{port}")
    print(f"[Web] 评测 Dashboard：http://127.0.0.1:{port}/dashboard")
    print("[Web] Ctrl+C 退出")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
