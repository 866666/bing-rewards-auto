# -*- coding: utf-8 -*-
"""触发 RewardsDailyAuto 立即运行一次（补跑今日）+ 读取最新 XML 验证"""
import sys, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32com.client

sched = win32com.client.Dispatch("Schedule.Service")
sched.Connect()
t = sched.GetFolder("\\").GetTask("RewardsDailyAuto")

print("=== 新任务 XML（关键段）===")
xml = t.XML
for kw in ["StartBoundary", "DisallowStartIfOnBatteries", "StopIfGoingOnBatteries",
           "StartWhenAvailable", "ExecutionTimeLimit", "wscript.exe", "rewards_daily.vbs"]:
    for line in xml.splitlines():
        if kw in line:
            print("  " + line.strip())
            break

print("\n触发立即运行...")
t.Run("")  # 立即启动一次
time.sleep(3)
# 触发后再读状态: State 3=queued 4=ready 268435456(0x10000000)=running
t2 = sched.GetFolder("\\").GetTask("RewardsDailyAuto")
print(f"触发后任务状态: {t2.State} (0x10000000={0x10000000} 表示 running)")
last = t2.LastTaskTime if hasattr(t2, "LastTaskTime") else "n/a"
print(f"LastTaskTime: {last}")
