# -*- coding: utf-8 -*-
"""注册 Windows 计划任务：每天触发静默运行 rewards_daily.py（schtasks 版，备用）
用法：以管理员权限运行一次 python register_daily_task.py [--time HH:MM]
推荐改用 tools/reregister_task_com.py（COM 版：电池供电 + 错过后补跑）
"""
import subprocess, sys, os, argparse

ap = argparse.ArgumentParser()
ap.add_argument("--time", default="08:30", help="每日触发时间 HH:MM（默认 08:30，配额按 UTC 日=北京 08:00 重置）")
args = ap.parse_args()

task_name = "RewardsDailyAuto"
vbs_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rewards_daily.vbs")

# 每天触发（普通用户权限即可运行 wscript）
cmd = [
    "schtasks", "/Create",
    "/TN", task_name,
    "/TR", f'wscript.exe "{vbs_path}"',
    "/SC", "DAILY",
    "/ST", args.time,
    "/F",                       # 强制覆盖
]

print("注册 Windows 计划任务（schtasks 版）...")
print(f"  名称: {task_name}")
print(f"  触发: 每天 {args.time}")
print(f"  操作: wscript.exe \"{vbs_path}\"")
print()

r = subprocess.run(cmd, capture_output=True, text=True)
print("STDOUT:", r.stdout)
print("STDERR:", r.stderr)
print("Return code:", r.returncode)

if r.returncode == 0:
    print(f"\n✅ 任务已注册。可用以下命令管理:")
    print(f"  schtasks /Run /TN {task_name}     # 立即运行")
    print(f"  schtasks /Delete /TN {task_name} /F   # 删除")
    print(f"  schtasks /Query /TN {task_name}   # 查询")
else:
    print("\n❌ 注册失败。请用管理员权限运行本脚本。")
    sys.exit(1)
