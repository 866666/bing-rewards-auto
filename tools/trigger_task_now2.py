# -*- coding: utf-8 -*-
"""重新触发 RewardsDailyAuto 并读运行状态"""
import sys, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import win32com.client

sched = win32com.client.Dispatch("Schedule.Service")
sched.Connect()
folder = sched.GetFolder("\\")
t = folder.GetTask("RewardsDailyAuto")

print("触发前 State:", t.State)
t.Run("")
time.sleep(5)
t2 = folder.GetTask("RewardsDailyAuto")
print("触发后 State:", t2.State, "(3=READY 4=RUNNING)")
print("LastRunTime:", t2.LastRunTime)
print("LastTaskResult:", hex(t2.LastTaskResult & 0xFFFFFFFF))
print("NextRunTime:", t2.NextRunTime)
