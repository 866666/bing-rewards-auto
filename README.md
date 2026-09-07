# Microsoft Rewards 每日积分自动化

通过 CDP（Chrome DevTools Protocol）控制本机 Edge，每天自动完成 Microsoft Rewards 的日常任务，全流程无人值守。

## 功能

| 任务 | 每日收益 | 说明 |
|---|---|---|
| Bing PC 搜索 ×30 | ≈90 分 | 1 次搜索 = 3 分，当天 30 次配额封顶 |
| Daily Set ×3 | 30 分 | 从 dashboard 实点击任务卡，每天任务名/内容自动变化 |
| Edge 浏览器使用 30 分钟 | bonus | 主流程跑完后浏览器再保持打开凑满 30 分钟，随后自动关闭 |
| Homepage Quiz | best-effort | 尽力触发，不计分也不影响其他任务 |

运行完自动关闭浏览器与脚本，不残留进程。

## 工作原理

- 脚本启动本机 Edge（带 `--remote-debugging-port=9224`，使用项目内 `edge_debug_profile` 登录态），通过 CDP 模拟真实搜索与点击。
- **配额时区是关键**：Rewards 每日配额按 **UTC 日** 切分，即 **北京时间早上 08:00 重置**。因此计划任务默认设在 **08:30**，才能吃到当天的 30 次搜索额度。设在其他时间（如凌晨）只会搜到"上一天"配额的尾巴。
- Daily Set 必须**从 rewards 面板真实点击**任务卡（referer + JS 追踪触发计分），直接打开任务链接无效。
- 重复关键词不计分，脚本内置 59 词库随机抽取，单轮不重复。

## 安装

### 1. 环境

- Windows 10/11 + Edge 浏览器
- Python 3.10+（安装时勾选 **Add Python to PATH**）
- 安装依赖：

```bat
pip install requests websocket-client pywin32
```

（`pywin32` 仅用于计划任务注册；`requests/websocket-client` 用于 CDP 通信）

### 2. 准备 Edge 登录态

脚本使用项目下 `edge_debug_profile` 目录作为浏览器数据目录（已登录你的微软账号）。首次使用：

```bat
msedge.exe --remote-debugging-port=9224 --user-data-dir=.\edge_debug_profile
```

用弹出的 Edge 窗口登录你的 Microsoft 账号（outlook/hotmail 均可）。以后脚本会自动复用该目录，不用再手动开。

> 若想用默认 Edge 配置，可删除 `edge_debug_profile` 后让脚本自动重建（会要求重新登录一次）。

### 3. 首次运行配置

```bat
python rewards_daily.py --setup
```

按引导完成：

1. 输入每日触发时间（默认 `08:30`，建议保持，见"工作原理"）
2. 自动注册 Windows 计划任务 `RewardsDailyAuto`
3. 自动把 Python 路径写入用户环境变量 `BING_REWARDS_PYTHON`（供 vbs 启动器无窗口调用）

## 使用

**手动补跑一次**（带终端输出）：

```bat
python rewards_daily.py
python rewards_daily.py --count 30          # 指定搜索次数
python rewards_daily.py --no-quiz           # 跳过 quiz
python rewards_daily.py --no-dailyset       # 跳过每日活动 3 任务
python rewards_daily.py --no-edge-hold      # 跑完立即关浏览器（不等 30 分钟）
```

**计划任务**（`--setup` 注册后无需任何操作）：

- 每天 08:30 自动运行，日志写入项目目录 `rewards_daily_log.txt`
- 错过触发时间（睡眠/关机）会自动补跑；电池供电也允许运行
- 主流程结束后浏览器保持打开至满 30 分钟（Edge bonus），随后自动关闭

**手动检查状态**：

```bat
python tools/check_rewards_status.py    # 起浏览器读积分/活动/界面语言
python tools/check_task_running.py      # 看计划任务是否在跑
```

## 项目结构

```
bing-rewards-auto/
├── rewards_daily.py          # 主脚本（搜索 + Daily Set + Edge 保活 + 自动关闭）
├── rewards_daily.vbs         # 静默启动器（计划任务用，纯 ASCII）
├── rewards_daily.bat         # 手动运行入口
├── tools/
│   ├── reregister_task_com.py    # 计划任务注册/重注册（COM，推荐）
│   ├── register_daily_task.py    # 计划任务注册（schtasks 备用）
│   ├── complete_dailyset.py      # 当天 Daily Set 3×10 任务
│   ├── check_rewards_status.py   # 状态/语言诊断
│   ├── check_task_running.py     # 进程与日志检查
│   └── experiment_search.py      # 计分机制实验
└── edge_debug_profile/       # Edge 登录态（不提交，见 .gitignore）
```

## 常见问题

**自动化界面是英文，自己打开是中文？** 正常。脚本搜索时带 `cc=us&setmkt=en-US&setlang=en` 参数（为了美区计分），会把语言偏好写入 profile 的 cookies，rewards 面板随之显示英文。**不影响计分与账号**。

**第二天积分不对？** 检查计划任务触发时间：若在 08:00 前触发，搜索的是上一天配额。用 `python rewards_daily.py --setup` 调整为 08:30 之后。

**想改触发时间？**

```bat
python tools/reregister_task_com.py --time 09:00
```

## 免责声明

本项目仅供个人学习与自动化研究。使用前请阅读 [Microsoft Rewards 服务条款](https://support.microsoft.com/rewards/get-started-with-microsoft-rewards)，自动化行为可能导致账号受限，风险自负。请勿用于多账号作弊等违规用途。

## License

[MIT](LICENSE)