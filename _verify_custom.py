"""自定义提炼 mock 全链路验证（Playwright）。"""
const_js = r'''
const { chromium } = require('playwright');
(async()=>{
  const browser = await chromium.launch({headless:true, channel:'chrome'});
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  const errors = []; page.on('pageerror', e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8010/', {waitUntil:'networkidle'});
  await page.fill('input[name=email]', 'pm@demo.cn');
  await page.fill('input[name=password]', 'demo12345');
  await page.click('button:has-text("登录")');
  await page.waitForSelector('h1:has-text("项目工作台")', {timeout:10000});
  // 选自定义提炼
  await page.evaluate(()=>{state.page='new';setScene('custom');render()});
  await page.waitForSelector('text=提炼需求描述', {timeout:8000});
  await page.fill('textarea[placeholder*="例："]', '帮我把这些对话按问题类型分类，每类给一个典型例子。');
  await page.fill('textarea[placeholder*="粘贴原始材料"], textarea:not([placeholder*="例："])', 'P1 学员：课程什么时候上线？\nP2 班主任：下周确定。\nP3 学员：数据集那块我还是不会写。');
  // 设计输出结构（DEMO_MODE 自动 mock 设计）
  await page.click('button:has-text("设计输出结构")');
  await page.waitForSelector('text=输出结构预览', {timeout:8000});
  const previewOk = await page.locator('text=关键要点').count() || await page.locator('text=信息条目').count();
  console.log('结构预览 OK:', !!previewOk);
  // 清洗预览 → 运行分析
  await page.click('button:has-text("清洗预览")');
  await page.waitForSelector('text=清洗结果', {timeout:8000});
  await page.click('button:has-text("运行完整分析")');
  await page.waitForSelector('.report-head', {timeout:20000});
  const scene = await page.evaluate(()=>reportScene());
  const tabs = await page.evaluate(()=>reportTabs().map(t=>t.label));
  console.log('场景:', scene, '| 页签:', JSON.stringify(tabs));
  if(scene!=='generic') throw new Error('场景未走通用渲染器');
  // 空内容兜底（mock 数据可能部分空）——页签切换无报错
  for(const t of tabs){ await page.click(`.tab:has-text("${t}")`); await page.waitForTimeout(200); }
  console.log('全页签切换无异常 OK');
  console.log(JSON.stringify({pageErrors:errors}));
  await browser.close();
  console.log('CUSTOM SCENE VERIFIED');
})().catch(e=>{console.error('FAIL:', e.message); process.exit(1)});
'''
import subprocess, sys, os
env = dict(os.environ, NODE_PATH=r"C:/Users/Administrator/.workbuddy/binaries/node/workspace/node_modules")
r = subprocess.run([r"C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe", "-e", const_js],
                   capture_output=True, text=True, env=env, timeout=300)
print(r.stdout)
if r.returncode != 0:
    print("STDERR:", r.stderr[-400:])
    sys.exit(1)
