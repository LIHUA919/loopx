# Codex 多 App 隔离与运维最佳实践（zh-CN）

## 定位

面向在单台 macOS 上同时运行多个 Codex / ChatGPT App（例如官方 GPT 版 + DeepSeek 版）的操作者 runbook。
与 [Codex App 多 Provider 切换](codex-app-provider-switching.md) 互补：那边讲「同一个 App 内切换 provider」，这里讲「多个 App 并存、隔离与排障」。

本文来自 2026-08-22 的真实线上故障复盘，包含结论、证据和可复现的检查命令。

## 目标与硬约束

- 两个 App 并存：GPT 官方模型（`gpt-5.6-sol`）与 DeepSeek（DeepSeek / 方舟 / OpenCode Go），各自独立的 `CODEX_HOME` 与前端数据目录，session / config / auth 互不污染。
- 官方 App 的单实例锁按 `--user-data-dir` 区分：**不同 `--user-data-dir` 可以同时多开**（已实测 `/Applications/ChatGPT.app` 同时跑两个实例、两个 app-server）。
- 隔离的 `CODEX_HOME` 是硬边界；跨 home 只读查阅用 peer bridge，不共享、不复制 SQLite。
- 开发机（SSH 远程项目）只挂在 GPT home；DS home 不挂远程开发机。

## 推荐方案：官方 App 双实例（不要做签名克隆）

启动两个实例的统一形状（由 launcher 脚本固化）：

```sh
/usr/bin/env \
  -u OPENAI_API_KEY -u CODEX_API_KEY -u CODEX_ACCESS_TOKEN \
  /usr/bin/open -n -a "/Applications/ChatGPT.app" \
  --env "CODEX_HOME=$HOME/.codex" \
  --env "CODEX_ELECTRON_USER_DATA_PATH=$HOME/Library/Application Support/Codex" \
  --env "CODEX_PROFILE_ROLE=DS" \
  --env 'DISABLE_AUTO_UPDATE=true' \
  --args "--user-data-dir=$HOME/Library/Application Support/Codex"
```

GPT 实例除 `CODEX_HOME=$HOME/.codex-gpt` 与 `--user-data-dir=.../Codex GPT` 外完全相同。

目录约定：

| 实例 | CODEX_HOME | 前端数据（--user-data-dir） | 模型 |
| --- | --- | --- | --- |
| Codex GPT | `~/.codex-gpt` | `~/Library/Application Support/Codex GPT` | gpt-5.6-sol（含开发机） |
| Codex DS V4 Flash | `~/.codex` | `~/Library/Application Support/Codex` | DeepSeek / 方舟 / OpenCode Go |

## 踩坑与教训

### 1. 不要给官方 App 做 ad-hoc 签名克隆（bundle id + 单实例锁 patch）

隔离克隆（`codesign --force --sign -`，Team ID 未设置，bundle id 改为 `com.openai.codex.gpt-isolated`）会导致 Apple Events 授权失败：

- Computer Use / SkyCUA 服务（`com.openai.sky.CUAService`）只接受 OpenAI 官方签名（Team ID `2DC432GLL2`）。
- 克隆发 Apple Events 报 `Sender process is not authenticated`（错误 `-10000`），App 每 ~0.4 秒重试一次、每次泄漏一个 `SkyComputerUseService` 子进程，几分钟内打爆进程表。
- 症状：zombie 上千、load average 400+、CLI / 测试全部 `fork: Resource temporarily unavailable`。

结论：**该授权无法通过系统设置或 TCC 授权解决**（不是权限 checkbox，是发送者签名身份校验）；需要 Computer Use 时请用官方签名实例。

### 2. 不要用 `launchctl submit` 做「延迟重启」

`launchctl submit -l <label> -- ...` 会注册 **keepalive 常驻任务**：进程退出后 launchd 反复拉起（实测几十到上百次），而不是一次性的延迟执行。

正确做法：

- 重启用一次性 `open -n -a`；不要 `launchctl submit`。
- 若已误用，逐个清理：
  ```sh
  launchctl list | rg 'codex-gpt-runtime'   # 找出残留标签
  launchctl bootout gui/$(id -u)/<label>    # 逐个移除
  ```

### 3. 实例级操作必须按 `--user-data-dir` 匹配 PID

- 不能用 bundle id（`com.openai.codex`）全局 `quit` / `osascript quit`：会同时退掉两个实例。
- 判断「某 App 是否在跑」不能按可执行路径（两个实例同路径），要按 `--user-data-dir` 匹配进程命令行。

### 4. 进程表被耗尽时，agent 会主动重启宿主 App

LoopX agent 在检测到 zombie 积压（父进程不响应回收）时会强制重启宿主 App 来统一回收。此时要**先修泄漏源**（本例是 Computer Use worker），而不是对抗 agent 的重启；否则会形成「泄漏 → 重启 → 再泄漏」死循环。

### 5. 配置会被 App 回写

App 启动时会重写 `config.toml` 并回填它管理的键（例如 Computer Use 的 `notify` hook、`SKY_CUA_SERVICE_PATH`）。因此：

- 想禁用一个 App 级能力，优先从 App 管理方（能力目录 / 插件开关 / 服务路径）下手，而不是只删 config 行。
- 修改 config 前先备份，并在 App 重启后复查是否被回写。

## 验证清单

```sh
codex-app-status                      # 两个 App 各自 Running: yes
ps axww | rg "ChatGPT.app/Contents/MacOS/ChatGPT"   # 应有两行，user-data-dir 不同
pgrep -f SkyComputerUse | wc -l       # 官方实例应保持低位稳定
launchctl list | rg codex-gpt-runtime # 应为空
ps -Ao state= | awk 'substr($1,1,1)=="Z"{z++} END{print z+0}'   # zombie 回到个位数
```

## 与 LoopX 的关系

- LoopX agent（loopx-meta 等心跳）在 DS home 运行；GPT home 用于官方模型 + 开发机。
- provider 切换（DeepSeek / 方舟 / OpenCode Go）只作用于 DS home，不碰 GPT home。
- 本 runbook 属于操作知识，不是 LoopX capability；只有当其中的切换机制被抽象成可安装、可测试的 adapter 时，才适合升级到 `docs/integrations/`。
