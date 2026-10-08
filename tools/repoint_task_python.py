# -*- coding: utf-8 -*-
"""repoint_task_python.py —— 把 RewardsDailyAuto 的 Action 指向项目自带解释器（默认 .venv\\Scripts\\pythonw.exe）

为什么需要它（2026-10-08 事故）：
  任务原先直启 `%USERPROFILE%\\.workbuddy\\binaries\\python\\versions\\3.13.12\\pythonw.exe`。
  WorkBuddy 更新时把 3.13.12 改成指向它**自带 vendor python** 的符号链接，
  并清空了该环境里的第三方包（requests / websocket-client 都没了）
  → 每天 08:30 任务都失败，日志只留一句 "Edge 连接失败: No module named 'websocket'"。
  修复思路：让任务使用**项目内 .venv**（由系统 Python 创建，不随 WorkBuddy 更新变动）。

特性：
  * 只改 Action，保留原触发器（每天 08:30）与设置（电池可跑 / 错过补跑 / 4h 上限）
  * 幂等：可反复执行；执行后回读校验
  * 不依赖 schtasks.exe（本机被安全策略拉黑），走 COM API

用法:
  python tools/repoint_task_python.py                # 指向 <项目>\\.venv\\Scripts\\pythonw.exe
  python tools/repoint_task_python.py --show         # 只看当前 Action，不改
  python tools/repoint_task_python.py --python <exe> # 指定其它解释器
"""
import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import win32com.client

BASE_DIR = Path(__file__).resolve().parent.parent
TASK_NAME = "RewardsDailyAuto"
SCRIPT = str(BASE_DIR / "rewards_daily.py")
DEFAULT_PY = str(BASE_DIR / ".venv" / "Scripts" / "pythonw.exe")

ap = argparse.ArgumentParser()
ap.add_argument("--task", default=TASK_NAME)
ap.add_argument("--python", default=DEFAULT_PY, help=f"解释器路径（默认 {DEFAULT_PY}）")
ap.add_argument("--show", action="store_true", help="只显示当前 Action")
args = ap.parse_args()

sched = win32com.client.Dispatch("Schedule.Service")
sched.Connect()
folder = sched.GetFolder("\\")
task = folder.GetTask(args.task)


def dump(td, tag):
    print(f"--- {tag} ---")
    for i in range(1, td.Actions.Count + 1):
        a = td.Actions.Item(i)
        print(f"  Path      : {a.Path}")
        print(f"  Arguments : {a.Arguments}")
        print(f"  WorkDir   : {a.WorkingDirectory}")


if args.show:
    dump(task.Definition, "当前 Action")
    sys.exit(0)

td = task.Definition
dump(td, "修改前")

td.Actions.Clear()
act = td.Actions.Create(0)  # 0 = TASK_ACTION_EXEC
act.Path = args.python
act.Arguments = '"' + SCRIPT + '"'
act.WorkingDirectory = str(BASE_DIR)

# 6 = TASK_CREATE_OR_UPDATE, 3 = TASK_LOGON_INTERACTIVE_TOKEN（与既有任务一致）
folder.RegisterTaskDefinition(args.task, td, 6, None, None, 3)

check = folder.GetTask(args.task).Definition
dump(check, "修改后")

a = check.Actions.Item(1)
ok = (a.Path == args.python) and (a.Arguments == '"' + SCRIPT + '"')
print("\nVERIFY:", "PASS ✅" if ok else "FAIL ❌")
print("解释器是否存在:", Path(args.python).exists())
if not Path(args.python).exists():
    print("  ⚠ 解释器文件不存在！先创建 venv：")
    print(f'     "C:\\Program Files\\Python314\\python.exe" -m venv "{BASE_DIR}\\.venv"')
    print(f'     "{BASE_DIR}\\.venv\\Scripts\\python.exe" -m pip install requests websocket-client pywin32 psutil')
sys.exit(0 if ok else 1)
