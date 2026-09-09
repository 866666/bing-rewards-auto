# -*- coding: utf-8 -*-
"""fix_task_action.py - 修正 RewardsDailyAuto 的 Action 为 pythonw 直启（一次性修复脚本）"""
import sys, datetime
import win32com.client

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TASK = "RewardsDailyAuto"
PYW = r"C:\Users\shang\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
SCRIPT = r"C:\Users\shang\WorkBuddy\bing-rewards-auto\rewards_daily.py"
WORK = r"C:\Users\shang\WorkBuddy\bing-rewards-auto"

sched = win32com.client.Dispatch("Schedule.Service")
sched.Connect()
folder = sched.GetFolder("\\")

td = sched.NewTask(0)
td.RegistrationInfo.Description = (
    "Microsoft Rewards daily automation (bing searches 30 + Daily Set + Edge 30min hold). "
    "Action=pythonw rewards_daily.py (no vbs layer). Runs daily 08:30; "
    "quota resets 08:00 Beijing. Battery OK + catch-up."
)
trig = td.Triggers.Create(2)  # TASK_TRIGGER_DAILY
trig.StartBoundary = datetime.date.today().strftime("%Y-%m-%d") + "T08:30:00"
trig.DaysInterval = 1
trig.Enabled = True

act = td.Actions.Create(0)
act.Path = PYW
arg = '"' + SCRIPT + '"'
act.Arguments = arg
act.WorkingDirectory = WORK

s = td.Settings
s.DisallowStartIfOnBatteries = False
s.StopIfGoingOnBatteries = False
s.StartWhenAvailable = True
s.WakeToRun = False
s.ExecutionTimeLimit = "PT4H"
s.MultipleInstances = 0

folder.RegisterTaskDefinition(TASK, td, 6, None, None, 3)
print("OK: RegisterTaskDefinition done")

# 回读验证
t = folder.GetTask(TASK)
a = t.Definition.Actions.Item(1)
print("Action.Path      :", a.Path)
print("Action.Arguments :", a.Arguments)
print("Action.WorkDir   :", a.WorkingDirectory)
ok = (a.Path == PYW) and (a.Arguments == arg) and (a.WorkingDirectory == WORK)
print("VERIFY           :", "PASS" if ok else "FAIL")