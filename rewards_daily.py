# -*- coding: utf-8 -*-
r"""Microsoft Rewards 每日积分自动化（直连版，已验证）
====================================================

核心能力（每日可拿）：
  [OK] PC Bing 搜索：12-20 次自然搜索 = 60 分（直连实测每搜索 +3 分）
  [WARN] Homepage Quiz：best-effort（CDP 注入 React 事件不稳定，可能不计分）
  [WARN] morePromotions：best-effort（UI 可见但 offer claim 机制封闭）

不通过（实测）：
  [NO] Edge 30 分钟 / Mobile App：需 Edge 持续活跃 / 手机，不在 PC 脚本范围

关键更新（2026-08-27 实测）：
  [OK] 不挂代理直连也能计分！bing.com 跳 cn.bing.com 后搜索依然 +3 分/次。
       （之前结论"必须挂代理"是错的：当时只测了 HK 节点被拒，直连未测）
  [OK] Daily Set 部分任务直连可完成（手动可点，脚本 UI 隐藏仍无法触发）

前置条件：
  1. Edge 已启动并带 --remote-debugging-port=9224
     （启动命令：msedge.exe --remote-debugging-port=9224 --user-data-dir=<本项目目录>\edge_debug_profile）
     脚本会自动启动（ensure_edge），无需手动
  2. Edge 已登录 Microsoft 账号（outlook/hotmail 任意）
  3. 不需要 Clash / 代理

使用：
  python rewards_daily.py                # 直连：搜索 + 报告（默认）
  python rewards_daily.py --count 20     # 搜索次数（默认 12，可 20 次拿满 60 分）
  python rewards_daily.py --with-clash   # 可选：切 Clash 到美国节点再搜
  python rewards_daily.py --no-quiz      # 跳过 Quiz 尝试
"""
import sys, io, json, time, random, urllib.request, urllib.parse, base64, argparse, subprocess, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 项目根目录（脚本所在目录，支持任意位置解压运行；edge_debug_profile 需自行登录）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE_DIR, "rewards_daily_log.txt")
CONFIG_PATH = os.path.join(BASE_DIR, ".rewards_config.json")
LOG = open(LOG_PATH, "w", encoding="utf-8")
def p(*a):
    s = " ".join(str(x) for x in a)
    LOG.write(s + "\n"); LOG.flush()
    try: print(s, flush=True)
    except: pass

# === 配置 ===
# 清理本机代理环境变量（Clash 2718 会劫持 127.0.0.1 的 CDP 访问返回 502，拉浏览器的
# 手动/计划任务两种环境下都必须直连 localhost）
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
os.environ["no_proxy"] = "127.0.0.1,localhost,::1"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)

CDP_HTTP = "http://127.0.0.1:9224"
CDP_PORT = 9224
US_NODE = "🇺🇸 美国 01 [V]"   # 切到这个节点
PIPE_NAME = r"\\.\pipe\verge-mihomo"
CLASH_SECRET = os.environ.get("BING_REWARDS_CLASH_SECRET", "set-your-secret")

# 搜索关键词池（多样化，避免 Bing 风控）
QUERIES_POOL = [
    "weather today", "latest news", "AI news 2026", "stock market today",
    "python tutorial", "recipe chicken", "best laptop 2026",
    "Microsoft Rewards tips", "bing search tips", "Excel functions",
    "PyTorch tutorial", "Node.js tutorial", "GitHub trending", "Linux commands",
    "machine learning basics", "deep learning", "neural network",
    "Rust language", "Go language", "WebAssembly", "docker compose",
    "kubernetes basics", "Nginx config", "Redis cache", "MongoDB",
    "FIFA World Cup 2026", "Olympic games", "Mars rover", "James Webb",
    "iPhone 18 release", "Samsung Galaxy", "Tesla Model Y", "BYD car",
    "healthy breakfast", "workout plan", "yoga benefits", "meditation",
    "coffee brewing", "wine pairing", "chocolate cake recipe",
    "travel Japan", "travel Europe", "backpacking tips", "hotel booking",
    "electric vehicle", "solar panel", "renewable energy", "climate change",
    "space exploration", "astronomy facts", "black hole", "exoplanet",
    "history of internet", "history of computing", "ancient Egypt",
    "Chinese history", "Tang dynasty", "Silk Road", "Great Wall",
]

# === Edge 自动启动 ===
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
EDGE_ARG_LIST = [
    f"--remote-debugging-port={CDP_PORT}",
    "--remote-allow-origins=*",
    f"--user-data-dir={os.path.join(BASE_DIR, 'edge_debug_profile')}",
    "--no-first-run", "--no-default-browser-check",
    "--window-size=1200,800", "about:blank",
]

def cdp_alive():
    try:
        urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=3)
        return True
    except Exception:
        return False

def ensure_edge(max_wait=45):
    """CDP 端口不通时自动启动带调试端口的 Edge（开机/重启后计划任务自愈）"""
    if cdp_alive():
        return True
    exe = next((e for e in EDGE_CANDIDATES if os.path.exists(e)), None)
    if not exe:
        p("  ❌ 找不到 msedge.exe")
        return False
    try:
        subprocess.Popen([exe] + EDGE_ARG_LIST, shell=False,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True)
    except Exception as e:
        p(f"  ❌ Edge 启动失败: {e}")
        return False
    for _ in range(max_wait):
        time.sleep(1)
        if cdp_alive():
            time.sleep(3)  # 等浏览器完全就绪
            return True
    return False

# === Clash 控制 ===
def clash_select(node=US_NODE, selectors=("XFLTD", "GLOBAL"), secret=CLASH_SECRET):
    """通过 named pipe 切换 Clash 节点"""
    try:
        import win32file, pywintypes
    except ImportError:
        p("  ⚠ pywin32 不可用，跳过 Clash 切换")
        return False

    def req(method, path, body=""):
        body = json.dumps(body) if isinstance(body, dict) else (body or "")
        raw = (f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
               f"Authorization: Bearer {secret}\r\nContent-Length: {len(body)}\r\n"
               f"Content-Type: application/json\r\nConnection: close\r\n\r\n{body}").encode("utf-8")
        h = win32file.CreateFile(PIPE_NAME, win32file.GENERIC_READ|win32file.GENERIC_WRITE, 0, None,
                                  win32file.OPEN_EXISTING, 0, None)
        try:
            win32file.WriteFile(h, raw)
            buf = b""; empty = 0
            for _ in range(200):
                try:
                    _, data = win32file.ReadFile(h, 65536)
                    if data: buf += data; empty = 0
                    else: empty += 1
                except pywintypes.error as e:
                    if e.winerror == 232:
                        empty += 1
                        if empty > 5: break
                        time.sleep(0.1)
                    else: break
                if empty > 10: break
            return buf.decode("utf-8", errors="replace")
        finally:
            win32file.CloseHandle(h)

    p(f"  → 切换 {selectors} 到 {node}")
    ok = True
    for sel in selectors:
        try:
            r = req("PUT", f"/proxies/{urllib.parse.quote(sel)}", body={"name": node})
            if "204" in r or "200" in r:
                p(f"    ✅ {sel} 切换成功")
            else:
                p(f"    ⚠ {sel} 响应: {r[:80]}")
                ok = False
        except Exception as e:
            p(f"    ❌ {sel} 失败: {e}")
            ok = False
    return ok

def clash_ip_check():
    """验证 IP 已切到美国"""
    try:
        req = urllib.request.Request("https://api.ipify.org?format=json",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ip = json.loads(r.read().decode()).get("ip", "")
            p(f"  → 当前 IP: {ip}")
            # 美国 IP 段开头
            return ip.startswith(("3.", "4.", "5.", "6.", "7.", "8.", "12.", "13.", "14.", "15.",
                                  "16.", "17.", "18.", "20.", "23.", "24.", "32.", "35.", "38.",
                                  "40.", "44.", "45.", "47.", "50.", "52.", "54.", "63.", "64.",
                                  "65.", "66.", "67.", "68.", "69.", "70.", "71.", "72.", "73.",
                                  "74.", "75.", "76.", "96.", "97.", "98.", "99.", "100.", "104.",
                                  "107.", "108.", "162.", "165.", "166.", "167.", "168.", "170.",
                                  "172.", "173.", "174.", "184.", "192.", "198.", "199.", "204.",
                                  "205.", "206.", "207.", "208.", "209.", "216."))
    except Exception as e:
        p(f"  ⚠ IP 验证失败: {e}")
        return False

# === Edge CDP ===
def cdp_new_page(url="about:blank"):
    """Edge 活着但没有标签页时，用 CDP 新建一个页面（Edge/Chrome 新版用 PUT，老版本用 GET）"""
    for method in ("PUT", "GET"):
        try:
            req = urllib.request.Request(f"{CDP_HTTP}/json/new?{urllib.parse.quote(url, safe='')}", method=method)
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception:
            continue
    return False

def get_ws():
    pages = json.loads(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=10).read().decode())
    pages = [x for x in pages if x.get("type") == "page"]
    if not pages:
        # 自愈：Edge 进程在但无标签页（崩溃恢复/重启后常见），新建一个再重试
        p("  ⚠ Edge 无活动页面，尝试自动新建标签页...")
        if cdp_new_page():
            time.sleep(3)
            pages = json.loads(urllib.request.urlopen(f"{CDP_HTTP}/json/list", timeout=10).read().decode())
            pages = [x for x in pages if x.get("type") == "page"]
        if not pages:
            raise RuntimeError("Edge 无活动页面（自动新建失败）")
    import websocket
    return websocket.create_connection(pages[0]["webSocketDebuggerUrl"], timeout=60, suppress_origin=True)

def cdp_send(ws, method, params=None, timeout=60):
    cdp_send.id = getattr(cdp_send, "id", 0) + 1
    ws.send(json.dumps({"id": cdp_send.id, "method": method, "params": params or {}}))
    dl = time.time() + timeout
    while time.time() < dl:
        ws.settimeout(max(1, dl - time.time()))
        try: r = json.loads(ws.recv())
        except: continue
        if r.get("id") == cdp_send.id: return r
    raise TimeoutError(method)

def cdp_js(ws, expr, timeout=30, await_promise=False):
    r = cdp_send(ws, "Runtime.evaluate",
                 {"expression": expr, "returnByValue": True, "awaitPromise": await_promise},
                 timeout=timeout)
    return r.get("result", {}).get("result", {}).get("value")

def cdp_nav(ws, url, wait=8):
    cdp_send(ws, "Page.enable")
    cdp_send(ws, "Page.navigate", {"url": url})
    time.sleep(wait)

def cdp_shot(ws, path):
    r = cdp_send(ws, "Page.captureScreenshot", {"format": "png"})
    with open(path, "wb") as f: f.write(base64.b64decode(r["result"]["data"]))

# === Rewards 任务 ===
API_BLOCKED = False  # 微软对中国区直连封锁积分 API（8/28 后），置位后跳过积分报告不中断搜索

def get_points_and_state(ws):
    """读取积分状态。直连下 getuserinfo API 返回 404（微软区域封锁）时容错返回 None，
    主流程继续执行搜索，不中断。"""
    global API_BLOCKED
    try:
        cdp_nav(ws, "https://rewards.bing.com/dashboard", 12)
        today = time.strftime("%m/%d/%Y")
        v = cdp_js(ws, """(async () => {
          try {
            const xhr = new XMLHttpRequest();
            xhr.open('GET', 'https://rewards.bing.com/api/getuserinfo?type=1', false);
            xhr.send();
            if (xhr.status !== 200) return 'API_ERR:' + xhr.status;
            const j = JSON.parse(xhr.responseText);
            const us = j.dashboard.userStatus;
            const ctrs = us.counters || {};
            return JSON.stringify({
              points: us.availablePoints,
              level: us.levelInfo?.activeLevelName,
              pcSearch: (ctrs.pcSearch?.[0]?.attributes?.progress) + '/' + (ctrs.pcSearch?.[0]?.attributes?.max),
              activity: ctrs.activityAndQuiz?.[0]?.pointProgress + '/' + ctrs.activityAndQuiz?.[0]?.pointProgressMax,
              dailyPoint: ctrs.dailyPoint?.[0]?.pointProgress + '/' + ctrs.dailyPoint?.[0]?.pointProgressMax,
              dailySetComplete: (j.dashboard.dailySetPromotions?.['""" + today + """'] || [])
                .reduce((s, t) => s + (t.complete ? 1 : 0), 0) + '/3',
            });
          } catch(e) { return 'JS_EXC:' + e.message; }
        })()""", timeout=30, await_promise=True)
        if not v:
            raise RuntimeError("getuserinfo 返回空")
        if v.startswith("API_ERR:") or v.startswith("JS_EXC:"):
            API_BLOCKED = True
            p(f"  ⚠ 积分 API 不可用（{v}）：微软对中国区直连封锁 rewards API，改为只执行搜索")
            return None
        API_BLOCKED = False
        return v
    except Exception as e:
        API_BLOCKED = True
        p(f"  ⚠ 读取积分失败: {e}（继续执行搜索）")
        return None

def natural_search(ws, query, wait=10):
    """bing.com 主页输入+回车（已验证计分模式）"""
    cdp_nav(ws, "https://www.bing.com/?cc=us&setmkt=en-US&setlang=en", 6)
    cdp_js(ws, f"""(() => {{
      const input = document.querySelector('#sb_form_q, input[name=q], textarea[name=q]');
      if (!input) return 'no input';
      input.focus();
      input.value = {json.dumps(query)};
      input.dispatchEvent(new Event('input', {{bubbles: true}}));
      input.dispatchEvent(new Event('change', {{bubbles: true}}));
      setTimeout(() => {{
        const form = document.querySelector('#sb_form, form[action*="search"]');
        if (form) form.submit();
      }}, 300);
      return 'submitted';
    }})()""", timeout=10)
    time.sleep(wait)

def try_quiz(ws):
    """尝试点 Homepage Quiz（best-effort，可能不计分）"""
    try:
        cdp_nav(ws, "https://www.bing.com/?cc=us&setmkt=en-US&setlang=en", 10)
        for _ in range(3):  # 最多 3 题
            has_quiz = cdp_js(ws, """(() => {
              const a = Array.from(document.querySelectorAll('a'))
                .find(x => x.href && x.href.includes('HPQuiz_') && (x.innerText||'').trim().match(/^[ABC]/));
              if (!a) return null;
              const r = a.getBoundingClientRect();
              return JSON.stringify({x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2), text: a.innerText.trim()});
            })()""", timeout=15)
            if not has_quiz: return
            pos = json.loads(has_quiz)
            cdp_send(ws, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": pos["x"], "y": pos["y"]})
            time.sleep(0.3)
            cdp_send(ws, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            time.sleep(0.1)
            cdp_send(ws, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": pos["x"], "y": pos["y"], "button": "left", "clickCount": 1})
            time.sleep(15)  # 等下一题或完成
    except Exception as e:
        p(f"  ⚠ Quiz 失败: {e}")

# === 主流程 ===
def setup_wizard():
    """首次运行向导：配置计划任务触发时间（默认 08:30）并注册"""
    def ask(prompt, default):
        try:
            v = input(prompt).strip()
            return v if v else default
        except (EOFError, KeyboardInterrupt):
            return default

    print("=" * 60)
    print("Microsoft Rewards 每日积分自动化 - 首次运行配置")
    print("=" * 60)
    print("说明：Rewards 每日配额按 UTC 日切分 = 北京时间 08:00 重置。")
    print("      计划任务建议设在 08:30 之后，才能吃到当天的 30 次搜索配额。")
    print()
    raw = ask("每日触发时间 HH:MM（回车用默认 08:30）: ", "08:30")
    t = raw if raw else "08:30"
    try:
        hh, mm = t.split(":")
        assert 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except Exception:
        print(f"  时间格式无效（{raw!r}），使用默认 08:30")
        t = "08:30"

    cfg = {"trigger_time": t, "task_name": "RewardsDailyAuto",
           "configured_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    reg_script = os.path.join(BASE_DIR, "tools", "reregister_task_com.py")

    # 找一个能 import win32com 的解释器（本解释器 / 常见 venv / 系统 py）
    interpreters = [sys.executable]
    venv_py = os.path.join(os.path.dirname(sys.executable), "envs", "default", "Scripts", "python.exe")
    if os.path.exists(venv_py) and venv_py != sys.executable:
        interpreters.append(venv_py)
    usable = []
    for py in interpreters:
        try:
            subprocess.run([py, "-c", "import win32com.client"], capture_output=True, timeout=20, check=True)
            usable.append(py)
        except Exception:
            continue

    ok = False
    for py in usable:
        try:
            r = subprocess.run([py, reg_script, "--time", t, "--task", cfg["task_name"]],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            out = (r.stdout or "") + (r.stderr or "")
            print(out[-800:])
            if "ALL CHECKS PASSED" in out:
                ok = True
                break
        except Exception as e:
            print(f"  尝试 {py} 注册失败: {e}")

    if not ok:
        # 兜底：给出 schtasks 手动命令
        vbs = os.path.join(BASE_DIR, "rewards_daily.vbs")
        hh, mm = t.split(":")
        cmd = (f'schtasks /Create /TN "{cfg["task_name"]}" /TR "wscript.exe \\"{vbs}\\"" '
               f"/SC DAILY /ST {hh}:{mm} /F /RL LIMITED")
        print("COM 注册不可用（需 pip install pywin32）。请手动执行以下命令注册计划任务：")
        print("  " + cmd)
        ask("已手动注册后按回车继续（或直接回车保存配置退出）: ", "")

    # 持久化解释器路径到用户环境变量（vbs 启动器按此解析；计划任务环境无交互终端）
    pyw = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "pythonw.exe")
    if os.path.exists(pyw):
        try:
            r = subprocess.run(["setx", "BING_REWARDS_PYTHON", pyw], capture_output=True, timeout=15)
            if r.returncode == 0:
                print(f"  ✅ 已写入用户环境变量 BING_REWARDS_PYTHON = {pyw}")
            else:
                print(f"  ⚠ setx 未成功（rc={r.returncode}），可手动: setx BING_REWARDS_PYTHON \"{pyw}\"")
        except Exception as e:
            print(f"  ⚠ setx 失败: {e}（可手动: setx BING_REWARDS_PYTHON \"{pyw}\"）")
    else:
        print("  ℹ 未找到 pythonw.exe，请确保 Python（含 Add to PATH）已安装")

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 配置已保存: {CONFIG_PATH}")
    except Exception as e:
        print(f"  ⚠ 保存配置失败: {e}")
    print("完成。之后可直接运行 python rewards_daily.py 手动补跑，或等计划任务自动执行。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true", help="首次运行向导：设置计划任务触发时间（默认 08:30）")
    ap.add_argument("--with-clash", action="store_true", help="切 Clash 到美国节点（默认不需要，直连即可计分）")
    ap.add_argument("--count", type=int, default=30, help="搜索次数（默认 30 = 当日 PC 搜索配额封顶≈90 分；配额按 UTC 日=北京 08:00 重置，任务 08:30 跑可吃满当天）")
    ap.add_argument("--no-quiz", action="store_true", help="跳过 Quiz 尝试")
    ap.add_argument("--no-dailyset", action="store_true", help="跳过 Daily Set 3×10 任务")
    ap.add_argument("--no-edge-hold", action="store_true", help="跳过 30 分钟 Edge 保活（跑完即关）")
    args = ap.parse_args()

    if args.setup:
        return setup_wizard()

    # 首次运行提示（未配置计划任务时；计划任务场景已由 --setup 落盘配置，不会重复打扰）
    if not os.path.exists(CONFIG_PATH):
        p("ℹ 首次运行：尚未配置每日计划任务。可运行  python rewards_daily.py --setup  设置触发时间（默认 08:30）")

    p("=" * 60)
    p("Microsoft Rewards 每日积分自动化")
    p("=" * 60)
    p(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    p(f"搜索次数: {args.count}  |  Quiz: {'off' if args.no_quiz else 'on'}")
    p(f"Clash 切换: {'on' if args.with_clash else 'off（直连，实测可计分）'}")
    p("")

    # 1) 切 Clash（可选）
    if args.with_clash:
        p("[1/4] 切换 Clash 到美国节点...")
        if clash_select():
            time.sleep(2)
            if not clash_ip_check():
                p("  ⚠ IP 验证未通过（可能时延或代理仍切），继续运行")
        else:
            p("  ⚠ Clash 切换失败，请手动确认后重试（不带 --with-clash 直连）")
    else:
        p("[1/4] 直连模式（实测不挂代理也能计分）")

    # 2) 连 Edge（不在运行则自动启动）
    p("\n[2/4] 连接 Edge CDP...")
    if not ensure_edge():
        p("  ❌ Edge 无法启动或 CDP 端口 9224 不通")
        return
    EDGE_STARTED_AT = time.time()  # Edge 打开时间起点（用于 30 分钟 Edge bonus 保活）
    try:
        ws = get_ws()
        cdp_send(ws, "Page.enable"); cdp_send(ws, "Network.enable"); cdp_send(ws, "Runtime.enable")
        p("  ✅ Edge CDP 已连接")
    except Exception as e:
        p(f"  ❌ Edge 连接失败: {e}")
        return

    # 3) 初始状态
    p("\n[3/4] 任务执行...")
    s0 = get_points_and_state(ws)
    p(f"  初始: {s0 if s0 else '(积分 API 不可用，直连被区域封锁)'}")

    # 4) PC 搜索
    queries = random.sample(QUERIES_POOL, min(args.count, len(QUERIES_POOL)))
    p(f"\n  → PC 搜索 {len(queries)} 次（自然搜索模式）")
    for i, q in enumerate(queries, 1):
        natural_search(ws, q, wait=random.uniform(5, 8))
        p(f"    [{i:2d}/{len(queries)}] {q}")
        if i % 5 == 0:
            cur = get_points_and_state(ws)
            p(f"        状态: {cur}")
        if i < len(queries):
            time.sleep(random.uniform(12, 25))

    # 5) Quiz (best-effort)
    if not args.no_quiz:
        p("\n  → 尝试 Homepage Quiz（best-effort）")
        try_quiz(ws)

    # 5.5) Daily Set（3 个 +10 urlreward 每日活动，需从 dashboard 实点击触发计分）
    if not args.no_dailyset:
        p("\n  → 尝试 Daily Set（3×+10 每日活动）")
        try:
            ds_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "complete_dailyset.py")
            py = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "python.exe")
            r = subprocess.run([py, ds_script], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=420)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            p("   " + out.replace("\n", "\n   ")[-1200:])
        except Exception as e:
            p(f"  ⚠ Daily Set 执行失败: {e}")

    # 6) 等 30s 让计分到账
    p("\n  → 等 30s 让计分到账...")
    time.sleep(30)

    # 7) 最终状态
    s1 = get_points_and_state(ws)
    p(f"\n[4/4] 最终: {s1 if s1 else '(积分 API 不可用)'}")
    try:
        if s0 and s1:
            d0, d1 = json.loads(s0), json.loads(s1)
            delta = d1.get("points", 0) - d0.get("points", 0)
            p(f"\n  📊 积分变化: {d0.get('points')} → {d1.get('points')} (+{delta} 分)")
        else:
            p("\n  ⚠ 积分报告跳过：微软 8/28 后对中国区直连封锁 rewards API（getuserinfo 404）")
            p("    搜索已正常执行（8/27 实测直连搜索仍计分）。")
            p("    如需核对积分增量，带 --with-clash 挂美国节点再跑一次。")
    except Exception as e:
        p(f"  ⚠ 积分解析失败: {e}")

    cdp_shot(ws, os.path.join(BASE_DIR, "rewards_daily_done.png"))
    p(f"\n  📸 截图: rewards_daily_done.png")

    # 8) Edge 保持打开 ≥30 分钟（Edge 浏览器使用 bonus 任务），期间每 5 分钟页面轻交互防挂起
    if not args.no_edge_hold:
        hold_min = 30
        elapsed = time.time() - EDGE_STARTED_AT
        if elapsed < hold_min * 60:
            wait_s = int(hold_min * 60 - elapsed)
            p(f"\n  → Edge 已打开 {elapsed/60:.1f} 分钟，再保持 {wait_s/60:.1f} 分钟（凑满 {hold_min} 分钟 Edge bonus）")
            full, rem = divmod(wait_s, 300)
            for i in range(full):
                try:
                    cdp_js(ws, "window.scrollBy(0, 150); 1", timeout=10)
                except Exception:
                    pass
                p(f"      已保持 { (i+1)*5 } 分钟 ...")
                time.sleep(300)
            if rem:
                time.sleep(rem)
        p(f"\n  → Edge 打开时长已达 {hold_min} 分钟，关闭浏览器")

    # 9) 关闭浏览器（Browser.close 只关 9224 调试实例，不影响日常 Edge）
    try:
        cdp_send(ws, "Browser.close", timeout=5)
    except Exception:
        pass  # 浏览器关闭后 ws 必然断开，异常属正常
    time.sleep(3)
    if not cdp_alive():
        p("  ✅ Edge 已关闭（9224 已释放）")
    else:
        p("  ⚠ Browser.close 未生效，Edge 可能仍在；不影响下一次任务（脚本会自动复用/自愈）")
    try:
        ws.close()
    except Exception:
        pass
    p(f"\n  📋 日志: {LOG_PATH}")
    p("=" * 60)
    p("完成")
    p("=" * 60)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        p("❌ 未捕获异常：")
        p(traceback.format_exc())
    finally:
        LOG.close()
