# -*- coding: utf-8 -*-
"""重新注册 RewardsDailyAuto 计划任务（走 COM API，不依赖 schtasks.exe）

用法:
  python tools/reregister_task_com.py [--time HH:MM]   # 默认 08:30，也可 --task <名称>

特性（对比旧任务 XML 的三个坑）：
1. AllowStartIfOnBatteries=True  / StopIfGoingOnBatteries=False  → 笔记本电池供电也运行
2. StartWhenAvailable=True      → 错过后（睡眠/关机）电脑一可用立即补跑
3. ExecutionTimeLimit=4h        → 防止长跑异常挂死
"""
import sys, argparse, datetime
from pathlib import Path
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import win32com.client

BASE_DIR = Path(__file__).resolve().parent.parent
TASK_NAME = "RewardsDailyAuto"
VBS = str(BASE_DIR / "rewards_daily.vbs")

ap = argparse.ArgumentParser()
ap.add_argument("--time", default="08:30", help="每日触发时间 HH:MM（默认 08:30：配额按 UTC 日=北京时间 08:00 重置）")
ap.add_argument("--task", default=TASK_NAME, help="计划任务名（默认 RewardsDailyAuto）")
args = ap.parse_args()

hh, mm = args.time.split(":")
start_boundary = datetime.date.today().strftime("%Y-%m-%d") + f"T{hh}:{mm}:00"

sched = win32com.client.Dispatch("Schedule.Service")
sched.Connect()
folder = sched.GetFolder("\\")

td = sched.NewTask(0)
td.RegistrationInfo.Description = ("Microsoft Rewards daily automation (bing searches + Daily Set + Edge 30min hold). "
                                   f"Runs daily {args.time}: quota resets at 08:00 Beijing (UTC day), "
                                   "run after it to eat fresh daily quota. Fixed: run on battery + catch-up.")

# 触发器：每天 args.time
trig = td.Triggers.Create(2)  # 2 = TASK_TRIGGER_DAILY
trig.StartBoundary = start_boundary
trig.DaysInterval = 1
trig.Enabled = True

# 动作：wscript 跑 vbs
action = td.Actions.Create(0)  # 0 = TASK_ACTION_EXEC
action.Path = "wscript.exe"
action.Arguments = f'"{VBS}"'
action.WorkingDirectory = str(Path(VBS).parent)

# 设置
s = td.Settings
s.DisallowStartIfOnBatteries = False   # 电池供电也允许启动
s.StopIfGoingOnBatteries = False
s.StartWhenAvailable = True            # 错过后补跑（关键修复）
s.WakeToRun = False
s.ExecutionTimeLimit = "PT4H"
s.MultipleInstances = 0                # IgnoreNew

# 注册：6 = TASK_CREATE_OR_UPDATE, 3 = TASK_LOGON_INTERACTIVE_TOKEN
folder.RegisterTaskDefinition(args.task, td, 6, None, None, 3)
print(f"OK: 任务 {args.task} 已注册（每天 {args.time}）")

# 回读验证
t = folder.GetTask(args.task)
state = t.State
xml = t.XML
checks = {
    "AllowStartIfOnBatteries=true":  "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml,
    "StopIfGoingOnBatteries=false":  "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml,
    "StartWhenAvailable=true":       "<StartWhenAvailable>true</StartWhenAvailable>" in xml,
    f"Trigger {args.time} daily":     f"<StartBoundary>{start_boundary}</StartBoundary>" in xml and "<DaysInterval>1</DaysInterval>" in xml,
    "Action wscript+vbs":            "wscript.exe" in xml and VBS in xml,
}
print(f"任务状态 code: {state} (4=ready/3=queued/268435456=running?)")
for k, v in checks.items():
    print(("  ✅ " if v else "  ❌ ") + k)
if all(checks.values()):
    print("\nALL CHECKS PASSED")
else:
    print("\nSOME CHECKS FAILED — 详见上方")