# 默认切换：按实现证据重算交付边界

- 核对基线：2026-09-24 `main` 的 `d64c4d377`；开放 PR 状态是快照，不是合入承诺。
- 归属：总目标 #4574 R5/G2；shared authority L2–L9/D1–D3；TS 迁移 T1–T4。
- 本次交付：既有 typed projection 与 shadow management 的完整来源传输。
- 本检查点取代此前交付记录中的剩余 PR 数量估算。

## 先纠正统计口径

此前“5–8”“6–8”“7–9”把宽泛工作包写成剩余 PR 数，部分实现合入、额外前置项
出现后又维持原估算。这些数字不是逐项核对过的 PR backlog，现撤回。代码缺口、
开放 PR、集成验收、自然时间资格和维护者晋升决定是不同单位，不能相加或机械扣减。

| 当前基线的事实 | 现在应如何处理 |
| --- | --- |
| #4870 保留 claim 的写入、#4888 reviewed cutover、#4920 drain 规划 | 已实现。验收组合 head，不再重新安排一套替代实现。 |
| #4922 完整 canonical 快照分页、#4960 SQLite runtime 准入、#4961 显示刷新恢复、#4964 共享来源摘要 | 已实现。消费者和打包客户端仍需组合验收，不等于还缺一个全新的分页/恢复实现。 |
| #4967 TS 完整来源组装、#4968 原生 outbox 交付/恢复 | 已实现。下述大型来源 RPC 失败是另一个已复现缺口，不能称为 capture 组装未做。 |
| #5003 event-owned completion 原子提交 | 开放。解决整批发布/重试，不负责 event writer 与 shadow capture 的绑定。 |
| #4994 带 lease 的显式 Agent 交接、#4995 Monitor 命令 proof、#4991 拒绝 poll 后释放预约、#4992 延期且绑定 receipt 的 Turn | 开放。组合各自经过评审的 head 后盘点 caller，不能再开一个 caller 重构 PR 重做它们。 |
| #4931 SQLite retained proof 编码、contributor #4224 | 优化 PR 开放，D2 资格未闭合。提速不等于容量、恢复和 soak 验收通过。 |
| #4915 默认 `.loopx` 目录 | 独立的配置迁移，不会选择 File/SQLite authority。 |

上表有六个相关的开放实现 PR（#5003、#4994、#4995、#4991、#4992、#4931），
另列 #4915 排除目录迁移造成的混淆。它们不是六个尚未动手的新需求，也不宣称每个
都是 storage default 的硬依赖。

## 四个可明确描述的后续交付边界

在整合已有工作之外，规划以下**四个新增交付批次，包含本次**。这是下一步开发
安排，不是保证总共只剩四个 PR。命令清单和精确 profile 的验收仍可能发现缺陷；
届时记录新证据和新边界，不再悄悄维持一个范围数字。

| 批次 | 可观察结果与 owner | 退出证据及剩余依赖 |
| --- | --- | --- |
| A. 完整来源流水线（本次） | 大于 RPC envelope 的来源可完整经过 TS projection、bootstrap、writer capture、inspect、qualify、reviewed promotion。Python 只传字节，TS 保留来源准入及 authority。 | 大型真实 CLI 链路、File/SQLite 完整读取、source witness 拒绝反例、真实来源隔离副本演练。不绑定 event writer，也不宣布 provider 默认合格。 |
| B. 外部 effect executor fence | 复用 lease/effect owner，在真实外部 effect 执行区间保护当前 execution proof，覆盖接管、超时、退出与不确定完成。 | 过期 executor 不能执行或结算被围栏的工作，精确业务 receipt 可恢复。复用 #4994/#4995；执行前查一次 proof 不足以证明整个区间安全。 |
| C. Event writer 绑定与整 Goal 迁移/回滚 | 将真实 event writer 的锁及发布生命周期接入现有 outbox lineage，组合 Markdown/event/lease writer、drain、reviewed cutover、canonical 消费者和 fenced export/rollback。随 TS owner 收口删除替代的 Python 决策。 | 整合 #5003，不重做原子完成。真实绑定通过之前保留 `event_log_writer_not_bound`。闭合 D1 消费者、命令清单与 D3 cohort 证据；若发现需要独立代码批次，明确记录该缺口。 |
| D. 默认/onboarding 与最后一批有界 Python 退役 | 新 Goal、settings、安装和打包 frontend/Lark/CLI 一致选择合格本地 profile；已有 Goal 有显式迁移、停用指导。仅删除 caller 已切换的业务 writer。 | B/C、适用的 D1–D3、回滚及受影响入口读回。保留永久 Python renderer、宿主 IO 和合法 import/export。 |

D2 的容量、crash/restore/upgrade/runtime 覆盖和**至少十天自然经过时间的 soak**，
是精确 SQLite profile 的证据门，不预设为一个或两个 PR；#4224 继续拥有这项工作。
D3 集成和经 owner 批准的 cohort 切换也不自动产生新 PR。这些缺项未闭合前，不给
固定完成日期或精确总 PR 数。File-only 有界切换、合格的 SQLite 默认、所有存量
Goal 迁移是不同验收范围，不能互相证明。

PostgreSQL 复用 typed command 和 AuthorityStore，部署 transport、认证/tenant
策略、restore identity、运维和 capacity 资格仍是独立中期路线。本地默认不等待
PostgreSQL 部署，conformance 通过也不等于生产服务已合格。

## 完整来源传输与预算决定

此基线的 `test_canonical_snapshot_integration` 在 provider 准入之前失败：完整
来源投影超过 2 MiB request 上限。canonical 读取分页已实现，但 source capture
及管理命令仍传完整投影。裁剪来源记录会破坏 digest/parity；扩大通用 RPC 上限会
影响所有方法。

协议名称和字节上限由既有 coordination 合同统一生成给 Python/TS。Python 文件交换
留在现有来源投影适配器，不新增独立维护的同名 Python/TS 模块对。
仅携带来源的 handler 接受本机私有文件 envelope。Python 写临时 request；TS 核验
method、字节数、SHA-256、普通文件身份和私有目录，然后调用原有 handler。TS 排他
创建结果文件并返回紧凑的绑定回执；Python 校验结果字节，在成功和失败时均清理临时
目录。inline 调用继续兼容。这是临时传输，不是第二套 authority store 或持久业务 receipt。

RPC 保持 2 MiB。**新增 artifact 对单次 request/result 分别限制为 16 MiB**：
这是单独的明确容量边界，不是无限流式，也不保证任意 Goal 均可容纳。超大输入在执行前
拒绝；结果交付失败可能发生在业务提交之后，调用方必须按原 operation identity 恢复，
不能把 RPC 失败当成未提交。本次不添加自动 mutation 重试。内存仍包含完整解析对象，
不解决任意大的 provider 历史或容量资格。

16 MiB 容纳多 MiB 完整来源 fixture 及管理请求中的重复表示，同时限制分配规模。
同机、同小型来源、32 对交错 warm 调用：inline/artifact 中位数为 9.15/11.62 ms，
p95 为 11.60/15.93 ms。本次为完整来源调用接受该实测本地 IO 成本，不宣称全局延迟
结论。后续调整大小仍需实测 workload 和既有预算审查。

验证使用真实 File/SQLite 及一次性 PostgreSQL 16 server。大型 CLI 回归仅对合成隔离
Goal 进行资格验证和晋升。本机活跃来源的一次演练检测到并发变化，已丢弃证据；被接受
的真实来源演练先按 capture witness 验证隔离副本一致性，再仅在副本执行变更。私有
原文、标识和原始输出不进入公开产物，没有晋升活跃 Goal。

无需新增前端设置或改变 API 形状：既有 CLI/Python 管理适配器仍调用同一领域 handler、
返回同一结果。公开变化是完整来源不再仅因越过 RPC envelope 而失败。默认配置、权限、
source freshness、event writer hold 和 provider 晋升标准保持原有语义。

相邻 runtime 修复处理客户端未读完超大响应就断连时的 socket 错误，避免一个断连
导致共享 runtime 退出。回归验证后续分页请求仍使用同一进程；不取消或重试业务操作。
