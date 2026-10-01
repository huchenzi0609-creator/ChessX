# ChessX 系统设计规格 v2.1

**版本**：v2.1 已确认规则基线与工程执行规格  
**日期**：2026-10-01  
**目标**：交付给实现者的 AI 涌现叙事实验 demo 规格  
**前版**：[v2.0工程评审稿](history/ChessX-Spec-v2.0.md)  
**决策来源**：用户对[设计问题与待确认清单](history/ChessX-v2.0-设计问题与待确认清单.md)中R01–R16的逐项回复。

> **【继承】**为原有且仍适用的规则；**【已确认 Rxx】**为用户本轮确认或修订的规则；**【工程】**为实现要求；**【工程细化】**为将确认结果落实为可执行协议的具体解释。v2.1覆盖前版中与本版冲突的方案，实施无需重新确认已决定的R01–R16。
>
> 优先级：用户之后的明确决定 > 本版已确认规则 > 本版工程细化 > 前版兼容内容。工程细化集中说明，不能冒充用户原话。数值奖惩、骰子裁决、墓碑显示凶手、远程参战、兵升变均不进入本版。

## 0. 阅读与执行入口

先读§0.1、§5–§10及§13.3，再按§19实施。本版是完整规格，前版与问题清单用于追溯，不需要把三份文档拼接才能实现。

| 层次 | 内容 | 执行状态 |
|---|---|---|
| 世界规则 | 移动／视野／休息、50句会话、战斗／结算、object、目标 | 按本版执行 |
| 工程契约 | 单写入者、投影、事件、模型能力校验、持久化、API、测试 | 按本版执行 |
| 规则档 | `demo-v2.1`，冻结R01–R16的确认结果与工程参数 | 进入场景hash和存档 |
| 实验结论 | 涌现程度、角色持续性、和平／冲突分布、模型偏差 | 由运行证据支持，不由规格宣称 |

本次交付为文档修订，未实现或运行demo。

### 0.1 已确认决定与本版落点

| ID | 确认结果 | 落点 |
|---|---|---|
| R01 | 兵正四向一步；斜四向一步仅在目标格有存活棋子时可移动；威胁为斜四向；马路径只有起点与终点；不升变 | §6.1 |
| R02 | 休息禁移动／主动邀请，允许不移动动作与协议回应 | §5.3–5.4 |
| R03 | 配置顺序串行；回合初rest快照；本回合参战不补主行动 | §5.2 |
| R04 | 全部存活占格者参与阻挡；同格整体；目标格一次协议；新到者退原点 | §6.3 |
| R05 | 优势决定权优先；互不能威胁按平等；群体能力聚合与内部一致按推荐A | §7 |
| R06 | 实际位移计rest；马被阻挡留原点、rest=0 | §6.4 |
| R07 | 后来者须在战斗结束前实际到达战场格；禁止远程加入；合并后重新计算max_round | §10.2 |
| R08 | 交涉与对话使用统一会话机制，每次至多50句 | §7.2、§8.2、§10.5 |
| R09 | 有界轮转、额度保留、移动先离席、不旁听 | §8 |
| R10 | 全员分成非空胜／败集合；认输者败；加入者同等承担处置后果；无中立者 | §10.3–10.4 |
| R11 | 自由条件＋本人执行；任何胜者可处决，释放须全体胜者一致同意；有限终止协议 | §10.5 |
| R12 | 初始已知人物由本人资料决定，默认不知；无旁听；叙事者可读参战者记忆，限制对外披露；成稿不回灌 | §11 |
| R13 | 物件独立主行动、不自动捡；接收者可以拒绝转交 | §9.1 |
| R14 | 初始人格只读；目标版本化与细化；定期及事件后自省 | §9.3、§11.5 |
| R15 | demo只接入API显式返回CoT的模型，不支持则报错；完整校验后呈现；故事引用事件 | §13.3、§15 |
| R16 | FastAPI＋React TS＋SQLite，本地单活动世界、模型可替换 | §3 |

### 0.2 关键工程细化

- **马的路径表示**：保存`[origin, target]`，碰撞扫描排除origin、只检查target；原地已有同格者不会因为马离开而再次交涉。
- **移动到场参战**：`move`可附`arrival_action=join_battle`，只有本次移动确实提交到battle.anchor才能登记；途中被截停／引发别的冲突不算加入。已在战场格者可用原地join指令，休息不能让远处角色跳过移动条件。
- **合并重算**：用户未指定人数函数，本版采用重新读取配置`battle_max_rounds`（默认4）、新battle.round=0的规则；不继承前版的最小剩余轮数，也不自创随人数增长的公式。普通单人加入不重置轮数；真正合并两场或更多活动战斗才重算。
- **统一50句**：普通、通行／同格、战后交涉复用同一消息、轮转、token、计数、退出和持久化机制；不同kind的结束条件与时间落点不同。通行是当前移动的结算子流程，战后是当前战斗的结算子流程，不新增世界turn。战后原“2轮上限”由50句替代，随后执行全体胜者终止协议。
- **拒收**：一次accept／reject回应，不强迫接收者额外消耗主行动；拒收不转移物件，发起者本次主行动仍消耗。
- **公开CoT**：指模型API显式暴露的推理文本，不是所有角色都能听见。默认仅本人运行记录与研究者可查看，不进入他人输入／台词／吟游诗人。它不是对未公开内部计算的保证。

## 1. 定位、边界与不可破坏的原则

### 1.1 系统定位【继承】

ChessX 是借用国际象棋部分棋子移动几何、并对兵自定义走法的叙事模拟器。类型规定走法，颜色是身份标签；没有将军、将死、棋局优劣、吃子获分、阵营胜利或其他隐含国际象棋规则。角色由 LLM 决策，故事来自初始关系、记忆差异与互动。

- 棋盘两个维度各为 8–64，棋子总数 0–8；普通视野 3×3，马 5×5。
- 同色可冲突，改色不改变移动／交涉能力；棋子自己有改色权限。
- object 由系统维护，角色不能凭叙述创造、销毁或复制它。
- 各角色自己的奖惩、价值判断、目标、情绪和记忆均为文本；没有忠诚度、仇恨值、战力、HP 等规则数值。
- 战斗通过第三方叙事 Agent 前向推进，胜负不是先计算后包装；各方记忆不能先合并为唯一正确版本。
- 死亡只能发生在战后结算；墓碑不提供凶手。无角色自动获胜，也无“只剩一人即终局”。
- 使用者指定本段 `max_turn`，到达后暂停，可在同一世界继续。

### 1.2 三种数据权威【工程】

v1.0 的“权威数据只有三项”不足以描述可运行软件，需要区分以下三层：

| 数据层 | 示例 | 系统的权威限度 |
|---|---|---|
| 世界结构事实 | 位置、类型、颜色、rest、存活、object、墓碑 | 代码校验，只有引擎能改变 |
| 协议与发生记录 | 会话成员、战斗成员、发言原文、裁决已提交、请求身份 | 代码维护“谁参与了／谁说了什么／哪个结果已生效” |
| 角色及叙事解释 | 某人认为谁背叛了谁、目标为何改变、战斗伤势描述 | 原样保存来源与版本，不判定心理内容正确 |

增加 `battle_id`、会话成员和 `alive` 不等于引入数值战斗。系统必须能知道一个角色死了、属于哪场战斗，才能禁止它继续移动或接收 object。

事件账本可以证实“B 说 A 偷了物件”，不自动证实“A 偷了物件”。文本伤势永远不是 HP 字段。主观记忆可以误记；结构事实不能被误记覆盖。

### 1.3 工程不变量【工程】

1. 一个世界只有一个状态提交者；模型、浏览器、导出文件不能直写世界。
2. 每个 world 的已提交 `seq` 单调递增；一个操作槽至多有一个成功提交。
3. 每颗存活棋子恰有一个合法位置；一格可以有多个棋子；死亡棋子不占用格子。
4. object 必须且只能处于一个位置：地面格子或一名存活持有者。
5. 一个棋子最多参与一场活动战斗、一个普通会话；战斗成员锁定位置。
6. 每颗棋子每个世界回合最多提交一个主行动；协议回应不再次获得移动额度。
7. 模型输出经 schema 和业务校验后才能产生事实；“我杀了你”不是死亡命令。
8. 角色只能收到自身资料、当前局部观测、历史实际收到的信息和自身记忆。角色没有整局事件查询工具。
9. 客户端已提交消息、角色收到的消息、持久化历史使用同一身份；半成品和解析错误不能成为台词。
10. 暂停、重置、重启后，旧请求不能向新运行代际提交；恢复不能丢掉未完成的交涉、记忆工作或裁决。

## 2. 三个项目的取舍与迁移

本文依据用户提供的**本地快照和报告**，不是对在线最新版本的评价。源码复核范围和定位见 §21。

| 来源 | 迁移的具体做法 | 在 ChessX 的实现 | 不直接迁移的部分 |
|---|---|---|---|
| wolfcha | 按角色／阶段现场组装提示词；私密字段来源受约束 | 唯一 `ContextBuilder`，输入必须是经过投影的 DTO | 整局私密状态塞进前端存储的做法 |
| wolfcha | FlowToken 与请求身份；公开输出白名单；固定响应回归 | `generation + operation_slot + context_hash`；完整响应验证后提交 | 庞大 React hook 主循环、原始响应兜底台词 |
| ai-town | LLM 结果作为输入回流；引擎独占世界状态；代际校验 | 世界执行器单写入、异步结果只提交 Intent | 60Hz 模拟、连续寻路、随机规则替角色决定去哪 |
| ai-town | 记忆与反思有来源关联 | 私有记忆版本、`source_receipt_ids`、定期整理 | 默认向量库、按系统 importance 奖惩或筛掉“无用”记忆 |
| jubensha-ai | 身份、记忆、当前任务三层分离 | `Persona / PrivateMemory / StageInstruction` | 固定真凶、揭晓终局、全员公开广播 |
| jubensha-ai | 只传播公开发言，私有证据单独写入；逐条呈现历史 | 字段级事件投影、角色收件箱、可重连事件流 | 任意工具失败变纯文本成功、仅内存保存运行状态 |
| jubensha-ai | 有界发言调度、可观察的阶段推进 | 无额外调度 LLM 的轮转发言许可，UI 显示当前步骤 | 为“剧情精彩”动态改规则、每次发言另调模型选择发言者 |

这些机制主要解决**运行可靠性与信息边界**，不会自动保证好故事。特别是 ai-town 的移动与社交发起在所读实现中主要由规则驱动，ChessX 不能照搬后仍宣称这些动作由角色自主决定。

## 3. demo 范围与已确认架构

### 3.1 最小完成条件

demo 必须支持：配置／导入场景、启动 0–8 角色、逐回合或逐提交观察、路径交涉、普通会话、多人战斗、战后结算、object 操作、角色记忆／目标更新、分段吟游诗人、暂停／续跑、进程重启恢复、无需再次调用模型的历史回放。

首版不必做：真人角色接管、账号／付费、多人在线编辑、语音、图像生成、3D、外部 Agent 工具、自动写剧本、跨机器部署、向量检索、自治剧情导演。基础设施的复杂度应与最多八个主体匹配。

### 3.2 技术方案【已确认 R16】

| 层 | 起始选择 | 理由／边界 |
|---|---|---|
| 前端 | React + TypeScript，Vite，SVG 棋盘 | 棋盘与消息 UI 简单；64×64 按需显示格线、缩放和平移 |
| 后端 | Python + FastAPI + Pydantic | JSON 契约、实验脚本、模型适配与异步请求集中在服务端 |
| 规则内核 | 独立 Python 包，纯函数／显式状态机 | 不依赖 HTTP、UI 或真实模型即可验证 |
| 运行器 | 一个进程、一个世界执行器；首版仅一个活动世界 | 避免持久化任务调度与多 worker 竞争；不同世界历史可同时阅读 |
| 数据 | 本地 SQLite；短事务、WAL、`synchronous=FULL`、外键开启 | 适合单机 demo；LLM 等待不占数据库写事务 |
| 推送 | REST 命令 + SSE 已提交事件 | 用户控制走 POST；服务端单向事件流足够；无需先搭双向 WS 协议 |
| 模型 | 可替换 `ModelAdapter`，能力显式配置与探测 | 不按模型名字猜 schema、token 上限或 reasoning 能力 |
| 测试 | pytest；前端组件测试 + 一条浏览器完整演示路径 | 区分规则正确性、信息隔离与真实模型表现 |

技术栈已按R16确认，首版采用上表方案。具体依赖版本在开工时选可用稳定版本并锁文件，不在规格中写“最新版本”。

SQLite WAL 支持读写并行，但同时仍只有一个写入者，且不能用于跨机器网络文件系统；这与单机执行器方案相符。[SQLite 官方说明](https://www.sqlite.org/wal.html)

FastAPI 的生命周期机制用于初始化／关闭执行器；运行任务需要本规格自己的持久化操作表和恢复逻辑，生命周期钩子本身不提供持久任务保证。[FastAPI 官方文档](https://fastapi.tiangolo.com/advanced/events/)

SSE 具有事件 ID 和重连能力；应用仍须用持久账本实现补发、游标与幂等。[MDN：使用 SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)

### 3.3 模块边界

```text
浏览器 ──REST命令──> API ──> 命令队列 ──> WorldRunner
   ^                                       |
   |                                   RuleKernel
   |                                       |
   └────SSE投影事件──── EventStore <──原子提交─┘
                                           |
                      ObservationProjector / ContextBuilder
                                           |
                      AgentRuntime ──> ModelAdapter
                                           |
                      校验结果 ──Intent回流──> WorldRunner
```

- `RuleKernel`：走法、视野、休息、成员资格、投票、死亡与 object、不变量；不调用 LLM。
- `WorldRunner`：时钟、阶段、行动额度、协议调度、取消、预算、状态提交。
- `ObservationProjector`：从内部数据构造某角色此时可以收到的内容，禁止返回内部对象引用。
- `ContextBuilder`：组合规则、本人资料、观测、收件箱、本人记忆与本阶段协议；唯一角色请求入口。
- `AgentRuntime`：角色／战斗叙事／吟游诗人逻辑；只返回输出，无 Repository 写权限。
- `EventStore`：操作请求、事件、投影收件箱、状态检查点和文本版本；由引擎控制提交。
- `ObserverProjection`：全知研究者、普通观众、指定角色视角三种读模型；不得复用为角色输入。

实现目录建议：

```text
ChessX/
  backend/chessx/
    domain/       # entities, intents, events, invariants
    rules/        # geometry, rest, visibility, negotiations, combat, objects
    runtime/      # runner, phases, scheduler, operation_registry, budget
    agents/       # character, battle_narrator, bard, memory_editor
    contexts/     # projections, builders, token_budget
    llm/          # interface, adapters, capabilities, validators
    storage/      # sqlite, migrations, repositories, replay
    api/          # routes, schemas, event_stream
  frontend/src/   # board, inspector, conversations, battle, timeline, controls
  scenarios/     # JSON + private Markdown + experiment manifests
  tests/         # rules, privacy, recovery, protocol fixtures, e2e
  data/          # 本地运行数据，忽略提交；导出另行生成
```

## 4. 数据模型与文件契约

### 4.1 坐标与身份【工程；兵走法见 R01】

- 内部位置 `{row, col}` 从 0 开始，左上角 `(0,0)`；`row` 向下增加。
- UI 默认显示数字坐标，国际象棋 A1 示例只作为辅助；大棋盘不以单字母列名作为协议。
- `piece_id / object_id / conversation_id / battle_id` 是稳定不透明 ID；姓名不是主键，颜色不是阵营主键。
- 大小写与颜色标签统一按 Unicode 文本保存，限制改色标签至 32 字符，不建立颜色能力表。
- 配置中的私人文件路径必须在场景目录内，禁止 `../` 越界；导入按 ID 绑定，不按角色名猜。

### 4.2 场景配置（示例为严格 JSON）

```json
{
  "schema_version": "2.1",
  "scenario_id": "the-seal",
  "title": "印信与承诺",
  "board": {"rows": 8, "cols": 8},
  "rule_profile": "demo-v2.1",
  "pieces": [
    {
      "id": "p_rook",
      "name": "守桥人",
      "type": "rook",
      "color": "青",
      "position": {"row": 3, "col": 1},
      "private_file": "private/p_rook.md"
    },
    {
      "id": "p_pawn",
      "name": "信使",
      "type": "pawn",
      "color": "白",
      "position": {"row": 3, "col": 3},
      "private_file": "private/p_pawn.md"
    }
  ],
  "objects": [
    {
      "id": "o_seal",
      "name": "旧印信",
      "description": "边缘磨损的铜质印信。",
      "location": {"kind": "ground", "position": {"row": 3, "col": 3}}
    }
  ],
  "limits": {"battle_max_rounds": 4, "conversation_max_messages": 50}
}
```

`private_file` 是加载器配置，不进入棋子公开 DTO。兵无前进方向属性；其正四向移动和斜四向威胁规则对相遇者公开。`limits` 属于场景中所有角色已知规则。

场景加载先检查schema、棋盘边界、ID唯一、0–8人、type枚举、私人文件存在、object位置单选、持有者存活、规则档完整。会话上限不得超过50，正式单句不得超过100token，公开CoT不得超过1000token；battle_max_rounds为正整数，默认4。失败时报告JSON路径与原因，不调用模型补造数据。初始同格是合法配置，不触发第0回合交涉。

### 4.3 私人 Markdown（示例格式）

```markdown
# 守桥人
## 性格与价值观
重视承诺，也不愿把印信视为权力的唯一来源。
## 背景与已知往事
曾被信使救过一次。这是本人认定的往事，未必是其他人的版本。
## 公开目标（可以选择宣称，初始并未自动广播）
让来往者说明自己要去哪里。
## 隐藏目标
找到当年撤走守桥同伴的人。
## 行为风格
言语简短；对熟人也会追问，但不默认诉诸暴力。
```

**【工程】**原始人设文件只读；运行时目标和记忆保存为独立版本，不让LLM改写整个文件。背景／初始已知人物文本可以包含主观认定，仅构成本人初始知识，不自动建立其他角色的共同记忆。默认没有其他角色名册。

**【已确认 R14】**身份／性格是只读初始锚点，demo不自动改写；目标可以变化，变化理由保留。本版不实现自动人格成长，不设目标数量、忠诚度或复仇层数规则。

### 4.4 运行时结构（语言无关契约）

| 实体 | 必需字段 | 校验要点 |
|---|---|---|
| WorldState | `world_id, rule_version, rule_profile, turn, phase, seq, generation, lifecycle, cursor` | 每次提交有序；cursor 可恢复 |
| PieceState | `id, type, color, position, alive, rest_remaining, conversation_id?, battle_id?` | rest 非负；双重战斗禁止 |
| TransferOffer | `id, object_id, sender_id, recipient_id, origin_slot, read_set, status` | pending/accepted/rejected/cancelled；提交时仍须合法 |
| ReasoningTrace | `attempt_id, owner, agent_role, text, token_count, validation_status` | 与正式content分离，不送其他角色 |
| ObjectState | `id, name, description, location` | location 判别联合：ground(position) 或 carried(holder_id) |
| Tombstone | `id, piece_id, position, death_event_id` | 面向发现者的 DTO 不带 killer、死亡过程或不可见回合信息 |
| Conversation | `id, kind, members, initiator, message_count, counted_message_ids, status, speaker_cursor, kick_used_by, pending_vote?` | kind=ordinary/negotiation/settlement；各kind均≤50条，合并不刷新 |
| Negotiation | `id, conversation_id, pending_move_id, mover_party, blocker_party, mode, geometry_snapshot, offers, permission_cycle, result?` | 成员固定到本次协议；没有2轮硬截止；结论结构化 |
| PendingMove | `id, actor, origin, requested_target, path, arrival_action?, next_index, passed_groups, blocked_at?, status` | 交涉后可恢复；到场加入必须实际到anchor；不重走已通过交涉 |
| Battle | `id, anchor, members, created_turn, round, max_rounds, parent_battle_ids, lineage_id, status, narrative_versions, surrender_set, outcome?` | active/awaiting_round/settling/closed；无 HP／伤势数值 |
| BattleOutcome | `winner_ids, loser_ids, surrender_ids, narrative_ref, rationale_summary` | 胜败分区见 R10；不能直接带死亡事实 |
| GoalVersion | `piece_id, version, markdown, update_reason, source_receipt_ids` | 文本内容由本人决定；系统只验证来源权限与版本 |
| MemoryVersion | `piece_id, version, markdown, source_receipt_ids, previous_version` | 不公开；不校正观点，不覆写其他人 |
| Receipt | `piece_id, receipt_id, source_event_id?, delivered_seq, channel, payload` | 当时实际收到的投影；不是之后重新计算的可见性 |
| Operation | `slot_id, attempt_id, generation, stage, actor_id?, context_hash, read_set_hash, status, request_ref, result_ref?` | pending/succeeded/failed/cancelled/obsolete；同槽只提交一次 |
| RunSegment | `segment_id, from_turn, target_turn, started_seq, ended_seq?, status, budget, model_config_hash` | 分段暂停不等于世界结束 |

会话／战斗上限自然受存活总数 8 限制，“不设参战人数上限”不代表允许第九个系统角色自动出现。

### 4.5 存储表与索引【工程】

首版可用 JSON 存结构快照，避免为每个字段建立关系表；文本和事件独立保存。

- `worlds`：当前完整运行结构、`seq/generation/rule_profile`、场景内容哈希。
- `segments`：分段边界、预算、状态；`(world_id, segment_id)` 唯一。
- `operations`：准备好的输入和结果；`(world_id, slot_id)` 标识逻辑槽，attempt 独立子记录。
- `events`：`(world_id, seq)` 唯一，事件封套和内部 payload，不删除已提交历史。
- `receipts`：`(piece_id, receipt_id)` 唯一，索引 `(world_id, piece_id, delivered_seq)`。
- `text_versions`：人格初始文本、目标、记忆、叙事、吟游诗人；按 owner 和 version 索引。
- `checkpoints`：每个回合结束及段暂停点的完整恢复快照；引用文本版本、事件边界与 cursor。
- `reasoning_traces`：attempt绑定的API公开CoT文本、owner、token计数和校验状态，私密读权限，不进角色事件。
- `commands`：用户命令 `command_id` 唯一，重复请求返回之前状态。

写事务同时更新：**世界结构 + 操作完成标记 + 事件批 + 收件箱 + 文本版本引用 + 下一 cursor**。广播发生在事务提交后。Markdown／JSONL 是导出物，不与数据库双写为两个真源。

## 5. 时间、行动额度与执行循环

### 5.1 三种时间【工程】

| 时间 | 用途 | 禁止混淆 |
|---|---|---|
| 世界回合 `turn` | 角色行动额度、休息递减、分段暂停 | 不等于一条发言或一次模型请求 |
| 协议步 `round / message_index` | 交涉轮、战斗轮、会话条数 | 不能暗中多扣休息 |
| 墙上时钟 | 请求超时、UI 动画、服务状态 | 模型等了 30 秒不会使棋子多休息一回合 |

暂停期间世界回合不推进，用户关闭页面不会补算一夜的回合。服务端仍可在已有本段预算内运行；只在本段目标、主动暂停或故障时停止，不因观察者断线自动重开。

### 5.2 世界回合顺序【已确认 R02/R03/R07】

**主行动保留 v1.0 的依次决策语义，不改为全员同时意图。** 每回合按场景配置顺序执行并记录 `actor_order`；固定顺序的偏差作为实验记录，不在本版偷偷轮换。

```text
开始回合 t
  A. 处理已排队控制命令；保存本回合 roster 和 actor_order
  B. 保存 rest_at_start；所有存活角色 rest>0 的值减一
     保存 locked_this_turn：开始时参战，或本回合新加入战斗的角色
  C. 依次处理每名角色主行动槽
       已死亡/locked → 跳过
       本回合已有主行动提交 → 跳过
       注入即时局部观测 + 已送达信息 + 私有记忆
       rest_at_start>0 → 不授权移动或主动邀请
       其余 → 授权可用主行动
       move → 路径／同格会话结算 → 提交实际位置与休息
              如有arrival_action，抵达战场格且战斗仍active才原子登记加入
       join_battle → 仅本人已与战场格同格时合法，加入并锁定位置
       其他 → 校验并提交
  D. 按创建顺序推进活动战斗
       created_turn < t 才推进一个战斗轮
       叙事 → 逐一收集认输 → 如结束则裁决 → 战后结算
  E. 调度普通会话，每成员本回合最多一个发言许可
       死亡/参战成员先移除；后发言者看到已提交的前文
  F. 应完成的记忆／目标整理；检查不变量
  G. 提交 TurnCompleted(t) 与恢复检查点
若 t 达本段 target_turn → 暂停、编排本段故事
否则 → t+1
```

上述顺序是本版执行顺序。通行交涉可以产生多次协议提交，但不额外推进世界回合；任何位置／会话变化都发生在明确提交点，不在等待模型期间“半改变”。

### 5.3 休息的精确定义【工程解释；截停代价见 R06】

`rest_at_start > 0` 则本回合不得移动，即使递减后为 0 也要等下一回合。用回合开始快照，避免角色数组顺序导致某人刚移动就被扣 rest。

例：T1 从 `(0,0)` 移到 `(0,2)`，设置 rest=2；T2 开始为2，减至1，不能移动；T3 开始为1，减至0，不能移动；T4 才能移动。马 T1 跳跃，T2–T4 不能移动，T5 恢复。

移 0 格等价 `wait`，不触发路径、不触发同格新交涉、不设置休息。不得用 0 格移动刷新现有战斗或反复触发 object 自动拾取。

**【已确认 R02】**休息只限制move和主动invite；允许已有会话发言、离席、协议回应，以及pickup/drop/transfer/change_color等不移动主行动。join_battle也可用，但必须已经在battle.anchor同格；远处休息者不能移动到场，因此不能加入。

### 5.4 主行动与协议回应

一次主行动是一个结构化 Intent。下列集合只限定**可改变系统事实的通道**，自由语言内容不受枚举限制。

| 阶段 | 输出类别 | 是否占主行动槽 |
|---|---|---|
| 可行动角色 | move / invite / wait / pickup / drop / transfer / change_color / join_battle | 是 |
| 休息角色 | R02 允许的不移动动作或 wait | 是，但 rest 不变 |
| 被邀请方即时回应 | stay / leave | 否；不附送一次移动 |
| 物件接收回应 | accept / reject | 否；只决定当前transfer |
| 普通会话许可 | say / pass / leave / propose_add / propose_kick | 否；同回合各一次，治理步骤另受上限约束 |
| 交涉许可 | say + pass/stop/fight/undecided 的提案或确认 | 否 |
| 战斗许可 | surrender / continue | 否 |
| 战后结算 | 提条件、接受／拒绝、合法执行请求、release / execute | 否；限本场结算所授权动作 |
| 记忆／目标整理 | 文本版本更新 | 否；不移动、不改 object |

**【已确认 R02】**普通会话中仍可在本人主行动槽离席后移动，邀请不永久消耗被邀请人的行动机会。活动战斗锁定优先于其他权限；R03规定本回合参战后即使战斗结束，也不补发主行动。

### 5.5 调度与故障【工程】

首版只发起下一次依赖调用：后说者、后行动者不能预先按旧上下文生成。无依赖的导出或检查可以并行。未来并行只允许读取同一冻结快照的请求，并按记录顺序提交。

每个阶段 cursor 都是可序列化数据，例如 `{turn: 12, phase: "PRIMARY", actor_index: 3, pending_move_id: "m12"}`。不能只靠 Python 栈保存交涉中断点。一个模型请求失败，暂停在该槽，不把异常自动当“角色沉默／同意／认输”。

## 6. 移动几何、能力判定与路径结算

### 6.1 类型几何【继承 + 已确认 R01】

不引入将军、自陷将军、王车易位、吃过路兵、兵升变或阵营吃子限制。颜色不影响任何类型的移动／威胁几何。

| 类型 | 移动几何 | 持久路径表示与碰撞扫描 | 成功移动的休息 |
|---|---|---|---|
| king | 八邻域一格 | `[origin,target]`，扫描target | 1 |
| queen | 横／纵／斜任意正距离 | 原点及沿途各格，扫描排除origin | `max(abs(dr),abs(dc))` |
| rook | 横／纵任意正距离 | 同上 | 同上 |
| bishop | 斜线任意正距离 | 同上 | 同上 |
| knight | `(abs(dr),abs(dc))`为`(1,2)`或`(2,1)` | **仅`[origin,target]`两个格**，扫描target，没有中间格 | 固定3 |
| pawn | 正四向一格；斜四向一格仅当目标格有其他存活棋子 | `[origin,target]`，扫描target | 1 |
| 任意类型wait | 原地 | 空碰撞路径，不重新交涉原有同格者 | 0 |

兵的移动合法性精确定义：

```text
正向一步：abs(dr)+abs(dc)==1 → 可移动
斜向一步：abs(dr)==1且abs(dc)==1
          且移动发起快照中target有至少一名其他存活棋子 → 可移动
其他位移：非法（原地另归wait）
```

兵的**威胁几何始终是斜四向一格**；正四向可移动但不构成威胁。斜移不是吃子，抵达已有棋子的目标格仍需执行同格交涉或专门到场参战协议。地面object／墓碑不满足“存在其他棋子”；死亡棋子不计。没有前进方向、首次两格、升变字段。

类型几何与占用条件分离：

```text
is_legal_move(piece, target, board, occupancy_at_start) -> bool + error
trace_path(piece, target) -> [origin, ...target]
collision_cells(path) -> path excluding origin
can_threaten(piece, source, other_position, geometry_snapshot) -> bool
visible_cells(piece, board) -> set[Position]
```

can_threaten仅判断几何，不发起攻击／吃子；不受rest、颜色、棋子／object遮挡影响。兵斜移的占用条件只作用于is_legal_move，不把“空的斜格”改为兵的非威胁区域。

### 6.2 视野与盲移【继承】

普通类型 `max(abs(dr),abs(dc)) <= 1`，马 <=2，棋盘边缘截断。默认不使用光线遮挡；object／墓碑不遮挡。棋子可以选择视野外的合法格子；系统不能把完整的“避开其他棋子”合法落点列表注入，否则会泄露盲区。

输入给模型的是：棋盘尺寸、本人类型和位置、本人可用移动几何、局部可见实体；可以在记忆中保留以前看过的远处位置，标注是历史认定，不能替换为其当前真实位置。

### 6.3 占格与路径【已确认 R04】

每次移动发起时冻结本次几何和沿途占用。世界主行动串行，因此没有物理上“同时到达”；后到者看见前一提交的占用，触发同格协议。本版不实现同时意图模式。

**所有被扫描路径格上的存活棋子都是交涉对象**，包括休息者、已轮空者和尚未到主行动槽者；origin不被扫描。这是R04已确认的占用规则，不以rest>0作为阻挡资格。

- 同格的存活棋子构成一个 blocker party，按格子距离从近到远处理；object 和墓碑不参与阻挡。
- 穿过中间格：执行一次通行交涉；让行后继续。
- 目标格已有棋子：执行**一次同格平等交涉**，不再先做通行交涉再做第二次同格交涉。
- 同格“回原处”只适用于此次新到者；不能把一直站在目标格的棋子送回一个未定义的“原处”。保留现有居民，新到者回移动前位置；本版不提供把现有成员送往其他格子的额外移动权。
- 初始同格视为场景已给定共处，不自动做第0回合交涉。
- 路径中某成员已有活动战斗：普通通行可让行／截停，但不对同一成员单开第二场战斗。非anchor格接触不产生加入；到场加入及战场合并的专门分支见§10.2。此权限约束防止用途中冲突绕过R07的到场条件。

### 6.4 PendingMove 与实际提交【工程 + 已确认 R06】

模型提出远距离 move 不等于世界已经移动。先建立 PendingMove，路径交涉期间位置保持原点；将路径推进的计算 cursor 与实体位置分开。

1. 按距离处理阻挡群组，记录已提交的交涉结论。
2. 全部放行，提交目标位置；被截停，提交 blocker 之前的格子；目标格退让，回原点。
3. 若升级战斗，提交移动者到阻挡前一格，阻挡者位置不变；马落点冲突则移动者留原点。
4. 一次提交同时更新位置、休息、PendingMove 状态和事件，原点不会在 UI 被提前清空。

升级战斗后该PendingMove以 `interrupted_by_battle` 终止，原请求目标仅保留在历史中；战后释放不会自动继续余下路径。继续前往原目标需要未来主行动槽重新决策。对路径上的多个群组，首个stop／fight使后续群组不再收到本次交涉。

**【已确认 R06】**按实际位移计 rest，未动为0；马实际落点成功为3，未动为0。战斗期间 rest 每世界回合继续递减，位置锁定独立存在。不按请求距离计算；马未落点就不承担3回合休息。

滑行子被截停的落点可能仍是另一个棋子所在格（前一个阻挡者已经同意让行）。把此前该格的放行解释为同意此次可能停留；本版不退回最近空格，也不递归创建一次新的共处交涉。马不存在“L 路径的前一格”。

## 7. 通行与同格交涉

### 7.1 类别函数【继承 + 已确认 R05】

两方各是一组参与人；每个人分别发言和记忆，没有代表。移动者通常是一人，同格阻挡者可多人。

群体计算：`A_can_B = 存在 A 成员几何上可威胁 B 任一成员`；反向同理，使用**移动发起前**快照。

| A_can_B | B_can_A | 类别 |
|---|---|---|
| true | true | 平等交涉 |
| true | false | A 优势 |
| false | true | B 优势 |
| false | false | 平等交涉 |

同格目的地协议强制平等，不使用同坐标攻击几何。群体取“存在”只是能力聚合，不把某个人的话替换成全体意见。

### 7.2 统一会话协议与交涉收束【已确认 R05/R08】

通行结论为pass／stop／fight；同格结论为coexist／return_mover／fight。语言自由，结论须结构化，系统不从“行吧”“我不怕你”猜协议结果。

ordinary／negotiation／settlement共用ConversationEngine：同一消息封套、单条100正式输出token、单次CoT最多1000token、后说者见已提交前文、稳定轮转、成员退出、最多50条角色台词、持久化消息身份。每一条台词由一个已授权发言许可产生；silent_pass和纯结构化表决不计台词，但有独立协议事件。不能用一个文本段内换行或多句绕过计数。

交涉作为当前移动结算的子流程：

1. 移动者先获得发言许可，其他成员按稳定ID顺序轮转；同一人两次许可间至少另一成员有机会回应。双方各自发言、听闻和记忆，无代表。
2. 每次回应可附结论或undecided；每人最新确认取代本人旧提案，但不改写已发生台词。
3. 平等交涉须全体明确同意同一通行／同格结论；优势交涉只须优势party内部一致，由它决定；劣势方不能否决合法优势决定。
4. 有效结论立即结束，不必耗满50句；平等交涉全体同意fight可提前开战，优势交涉由优势party内部一致决定fight也可提前开战。
5. 第50条提交后先检查其是否形成合法结论；有则执行，无则谈崩、开战。优势party内部仍不一致也属于未达结果。
6. 退出或连续2个完整许可轮全员silent_pass而未达结论，视为提前终止协商、进入fight；已经生效的pass／stop等结果不被之后退出推翻。原参与者仍是冲突当事者，离开会话不撤销本次空间争议。
7. 网络／CoT／JSON错误不计消息，不算拒绝／认输／谈崩，按技术错误暂停。

“2个空许可轮”只防止零台词无限循环，不是旧版“最多2轮交涉”；只要存在有效台词，交涉可以持续到50句。空许可轮指完整成员轮转没有新台词也没有最终有效结果，包含重复未通过的纯结构化提案／表决，不能用反复零字释放投票保持协议永不收束。普通会话的空闲关闭按世界回合计，通行／战后协议的空闲按子流程许可轮计。协议等待不消耗额外world turn、移动额度或rest。

**工程例外**：若阻挡party含已经参战的成员，且本次不是合法到場加入，不能按通常谈崩分支新建第二场战斗；50句／退出／空轮未达pass结果时按stop截停本次移动，保持既有战争。这个有限通行分支的权限和原因在§10.2明确；它不登记后来者、不远程合并。

优势决定权已经优先于“任何阻挡者都有绝对否决权”的旧表述；阻挡者仅在拥有相应决定权时可截停。若移动方单向优势，它能合法决定pass，不保留劣势阻挡者的绝对否决。互不能威胁按平等交涉；群体能力取存在威胁边，群体决定仍需内部一致。

### 7.3 交涉上下文与知情范围

参与者收到彼此当前可用于交涉的身份／类型／颜色与本次冲突位置，但不收到对方私人档案、物件库存、隐藏目标。路径撞上视野外的人时，系统新送达一个“本次接触”收件记录；不是把远处整个周边地图突然公开。

交涉台词只送参与者，不全棋盘广播；附近普通棋子可以看见结构结果（移动／战斗开始），不自动听见全部谈判。R12已确认不设置旁听；“对参与者公开”不是“全员知情”。

### 7.4 无法自动执行的承诺【工程】

“成为我的仆从”“今后永不背叛”“替我复仇”保存为个人话语与本人记忆，不建立强制服从字段。只有已定义的系统操作才可执行，例如本人改色、本人转交物件。系统不验证承诺是否真诚，也不因违约扣分。

普通谈判不会开一个任意代码执行工具。将“赠送 o_seal”变为事实需要后续合法 transfer Intent；协议文本里写“已赠送”只表示该角色如此声称。

## 8. 普通对话与群聊

### 8.1 邀请、成员与合并【继承 + 已确认 R09】

- 本人有主动邀请权限时，只能邀请本人当前视野内的一个目标；每回合最多一次，主行动只能选 invite 或其他动作之一。
- 邀请不可拒绝，目标先加入；随后每名新受邀者都有立即离席机会，**在首句产生之前**处理。离席不退邀请额度。
- 若目标在普通会话中，则邀请该会话全体成员。远处成员只能通过原会话身份加入；邀请者不会因此获得那些成员的当前位置和背包信息。
- **【已确认 R09】**若邀请者和目标都在不同会话，合并为新 conversation ID；旧会话关闭，保留历史。新会话不把加入前另一会话的历史倒灌给新成员。
- 合并后的有效消息预算为 `50 - 两个父会话已计消息之和`；若总和≥50，则关闭父会话，不建立可发言的新会话。投票发起额度按祖先会话已使用人员继承，避免合并刷新额度。
- 邀请已有战斗参与者不建立普通会话；生成“目标正在交战，邀请未建立”，额度消耗。参战者从普通会话移出，剩1人自动解散。

合并时不自动公开私人发言历史，也不把两个会话里的主观关系统一。新ID引用父ID，回放能看到成员从何而来。合并计数使用祖先消息身份的并集，不能把同一祖先历史重复加两次；实现可存`counted_message_ids`或等价的去重谱系。

### 8.2 发言调度【继承 + 已确认 R09】

发起人首先获得许可。之后按稳定成员环轮转，角色可 `say / pass / leave`，所以“自由发言”指可以选择是否和说什么，而不是让无限并发请求抢写。

- 每名成员每个世界回合最多一个普通会话许可；发起人在建立回合用一次许可，不再多发一次。
- 后发言者仅看到此前已提交、并实际送达的消息；不能先把所有人的草稿加入上下文。
- 同一人两次发言之间至少另一成员获得许可；实现冷却用逻辑调度，不用真实 sleep 改模拟时间。
- 一条 `say` 是一条消息，一次输出只能含一句／一个文本段，不用多个数组段绕过计数；短暂 UI 打字动画不占世界时间。
- 单句文本 ≤100 token；CoT按§13.3显式返回并校验，未公开CoT的模型禁止进入demo。
- 普通会话累计50条后关闭。`pass` 不计消息；提议附带的台词计消息，后台投票结果属于系统事件，不计角色台词。
- 到上限／解散／参战移除导致会话关闭时，取消未完成治理提案，旧回调不可再加人或踢人。
- **连续2个完整世界回合全员 pass 则关闭该会话**，不改变角色目标，也不判定关系失败。
- 达本段 max_turn 时暂停但保留会话，续跑仍使用原累计条数。

模型不给任何回复是故障；角色有效提交 `pass` 才是沉默。系统不会生成“他陷入沉思”冒充角色回应。

### 8.3 拉人与踢人【继承；空票结论来自决定清单】

`propose_add(target_id)` 由当前成员提出，target 在提议者视野内，所有当前成员一致同意才建立邀请；新目标立即离席权仍有效。每会话每回合最多一个治理提案，快照固定投票者；成员变动则取消旧提案并记录原因。提议目标已在其他会话时，合并整个会话，而非拆走一个成员；该扩大效应需要把成员清单提供给投票者，见 R09。

`propose_kick(target_id)` 每人每会话最多发起一次；发起提交时消耗额度，即使失败。表决者排除发起者与目标，必须非空且全部明确同意。**两人会话中踢人是空操作，不按空集真值自动踢出**，这已在决定清单中明确。

缺票、超时不是反对票；先技术暂停。任何成员有立即离席权，离席事件立即使包含它的待处理投票失效；不能在它已经退出后仍提交旧投票快照。

### 8.4 跨空间成员与统一引擎【已确认 R09】

普通会话不因成员远离自动解散，但主行动move先让本人离席；整体邀请／合并带来的远处成员可留在会话，接收成员消息而不获别人最新位置。没有近处旁听。

普通会话每成员每世界回合最多一次许可；通行／战后交涉在所属结算子流程内轮转到合法结果、50句或空闲／退出收束，不按普通会话的世界回合配额卡死。两者统一ConversationEngine和消息逻辑，各自保留结构结果与时间语义。当前角色的协议请求串行，不同时生成普通台词与交涉草稿。

## 9. object、颜色、墓碑与目标

### 9.1 object操作与可拒收转交【继承 + 已确认 R13】

| Intent | 前置条件 | 原子结果 | 接收信息 |
|---|---|---|---|
| pickup(object_id) | 本人存活；ground且与本人同格 | location→carried(self) | 本人完整；附近见地面变化，不获背包 |
| drop(object_id) | 本人持有 | location→ground(self.position) | 附近可见地面物件 |
| transfer(object_id,target_id) | 本人持有；target存活且在本人视野内 | 建立TransferOffer，接受后才转移，拒绝保留原持有者 | 双方知提议物件；附近只见动作，不获背包／私有回应 |
| change_color(label) | 本人有当前阶段权限 | 本人color更新 | 当前可见者知改色，盲区不自动广播 |

拾取／丢弃／转交／改色都是独立主行动，没有move附带pickup；不存在容量／重量规则。持有者始终知道精确库存；他人视觉看不到携带态，也不因看见地面物件消失就收到持有者事实。

TransferOffer有稳定offer_id、object_id、sender_id、recipient_id、origin_slot、read_set、status。转交为以下有界子协议：

1. 发起者提交合法offer即消耗本回合主行动；物件仍在发起者手中，不进入“无人持有”的临时状态。
2. 只向接收者提供该物件名称／说明、来源角色和一次accept／reject许可。接收者不消耗自己的主行动，休息中也可回应。
3. accept提交前再校验双方存活、sender仍持有、recipient仍在sender视野；全部满足才原子转为carried(recipient)。
4. reject写ObjectTransferRejected，物件不变；不向发起者自动返回一份隐藏思考。拒绝不扣接收者奖励／关系值。
5. 网络／解析失败是pending技术错误，不自动接受或拒绝。暂停恢复仍是同一offer；重复回应不重复转移。权限失效写OfferCancelled，不把物件送给死人。

普通transfer子协议只收一次有效选择，不另开50句协商；角色想谈交换条件可先通过普通／战后会话。战后败者接受“交出物件”仍需接收者accept，拒收不算已经履约，后续由胜者继续协商／释放／处置。

object和墓碑不阻挡路径；一格可多物件；没有使用、合成、复制、销毁或强化操作。台词声称“烧毁了印信”不会删除object；后端拒绝不存在的销毁Intent。

### 9.2 死亡事务【继承 + 工程】

合法 `execute_loser` 只能来自活动战后结算，并满足执行者胜者资格、目标为该场败者且仍存活。一次事务：

```text
确认结算授权仍有效
piece.alive=false，从占格、会话、战斗活动成员和待行动名单移除
携带的全部object→ground(死亡位置)
创建Tombstone（外部发现投影只有死者身份与死亡位置）
提交DeathCommitted + ObjectDroppedByDeath + MembershipChanged事件
撤销目标的所有未提交操作槽
```

执行动作与死亡事实有内部 actor ID，便于审计；**墓碑投影不泄露凶手**。没有在现场且没收到消息的角色，下次看见墓碑才获得死亡知识；死人不再更新目标或收发消息。原有记忆保存用于历史研究。

### 9.3 目标更新【继承 + 已确认 R14】

系统只要求本人返回 `{decision: keep|replace|refine, markdown, reason_summary, source_receipt_ids}`。

- `replace` 仅在本人认定已达到或永远不可能达到时授权；理由是本人解释，代码不把理由当客观判决。
- `refine` 表示同一长期目标下提出前置行动，例如“复仇”下的“查明凶手”，不伪装成目标已经失败。
- 缺少信息不能由系统直接推导为目标失败；本人是否放弃仍是角色判断。
- 新目标不需要完成预设任务表，不能凭目标内容给予规则奖励。
- **每5世界回合整理一次，且战后结算结束／接收到死亡信息／会话关闭后安排整理**。这是获得一次自省机会，不是自动换目标触发器。

保留目标版本和解释，不做复仇链深度截断。文字越积越长时压缩当前上下文，完整历史仍保留；“文本容量控制”和“禁止某种动机延续”是不同事项。

## 10. 多人战斗与战后结算

### 10.1 建立与位置【继承 + 已确认 R06/R07】

交涉有效谈崩才建立 battle；创建事件保存成员、冲突位置、PendingMove 与双方原始各自记忆引用。没有主动 `attack` 主行动；角色想战斗需通过既有交涉机制。

 `anchor` 为阻挡格／同格冲突格；它是广播中心和 UI 战斗标记，不是把所有参战者传送到同一格。移动者的位置按 §6.4 处理，其他成员保留各自位置。所有参战者位置锁定。

**【已确认 R07】**新战斗从下一个世界回合开始推进，每个世界回合最多一个 battle round。这样获知消息且可合法到场者有下一主行动槽移动参战，不在一个await链里瞬间跑完四轮。

### 10.2 战争广播、走到场参战与合并【已确认 R07/R12；工程细化】

**广播只是战争消息，不是远程参战邀请。** BattleNotice向anchor5×5内存活角色送达battle_id、anchor、公开参战身份、active状态，不含私人记忆、隐藏目标、CoT或全文战况。收到消息仅使角色知道这场战争；不赋予远程join权限，也不保证到场时战争仍在进行。

战场格就是battle.anchor。最初因通行冲突开战的当事者按§6.4留在各自位置；**后来参战者**必须在active结束前实际到达anchor，不能只到广播范围或邻接格。

到场流程：

1. 可行动者用`move {target: anchor, arrival_action: {kind: join_battle, battle_id}}`，走法必须按本人类型合法；这仍只占一个主行动槽。普通移动可以跨多个世界回合逐次走近，系统不替角色自动寻路／连续移动。
2. 沿途其他存活棋子照常触发通行交涉；中途截停／另起冲突使本次arrival_action取消，不因为最初目标是战场就加入。
3. 精确落到anchor时，专门“到场参战”协议替代目的格普通共处交涉；不再先让活动战斗成员重复同格投票。位置、rest、BattleJoined、battle成员和原普通会话离席一次原子提交。
4. 提交时校验：本人存活、实际位置等于anchor、battle.status=active、本人不在另一战斗；任一不满足则不登记。若目标战斗在抵达前已结束，拒绝到场参战请求并让本人重新选择合法普通移动，不远程补入结算。
5. 已经站在anchor且尚未参战者可用原地主行动join_battle；其rest不阻止原地加入。远处休息者不能移动，不能借R02绕过到场条件。
6. 到场者获得完整参战资格，可能判败并被处置。加入既有battle不重置round／max_round；若该battle本回合已具推进资格，新人参加本回合随后生成的战况。

兵的斜移到场也必须满足斜向目标格已有其他存活棋子的条件；只有一个战斗标记而没有棋子的anchor，不足以授权兵斜移。没有自动生成落点棋子或传送行为。

**活动成员的其他格子**：原始参战者可能停在anchor前一格。后来者在这里接触它不算到场。普通通行协议的合法结果限制为pass／stop，不允许让同一角色参加第二场战斗；fight提案返回BATTLE_ALREADY_ACTIVE并要求合法修正，不自动远程合并。该有限通行协议没有pass共识时按stop收束，不能无限反复提出不可执行fight。此限制由“每人最多一场战斗”和“后来者必须到场”共同落实，并作为工程细化记录。

**合并协议**：仅在同一实际战场格存在两场或更多活动战斗、并有合法到场接触时合并；不能把不同anchor的战争仅因广播范围重叠就合并。通过专门到场分支处理，不把已有参战者重复登记到另一场battle。

- 建立新battle_id，父battle_ids保留；父场status=merged，所有旧成员转移到新场，位置不变；到场的新角色只有落点提交后才加入。
- 旧叙事按父场分别保留，不改写为原先已经共同交战；新叙事输入包括每个父场历史与各人的本人记忆。
- 新anchor为实际合并格；`round=0`，`max_rounds=当前配置battle_max_rounds`（默认4）；本版不采用v2.0的最小剩余轮数。记录重算前后的计数与依据。
- 合并后的新场从下一世界回合推进；单人加入或重复join／重复回调不触发重算。合并必须至少两个不同active父场，已merged／settling／closed场不可复用。
- 新场广播仍只送战争消息。旧参与者可能远离anchor但属于初始冲突的继承者，不因此变成“远程后来参战者”。

主行动串行、到场时优先加入已有战斗，因此正常运行通常不产生同anchor的第二场；合并分支仍保留用于合法存档／后续场景扩展。不能为触发重置故意重复创建父战斗。合并可能使同一战争谱系总轮数超过4，预算和回放必须按谱系累计，不再把4当整条历史上界。

新加入者只获得加入后的当前战况概要和未来战况，不自动获得此前私人交涉／回忆或完整旧台词。已有参战者保留其已收到的本场历史。

### 10.3 每一战斗轮

```text
冻结本轮成员与各人自身记忆版本
战斗叙事Agent输入：
  每个参战者各自的记忆版本（分别标主体，不对齐）
  必要局部结构事实 + 本场已提交叙事 + 本轮新加入信息
返回：本轮战斗叙事文本；不得直接改变世界结构
提交BattleNarrated，投影后送达每名参战者
依记录顺序向每名参战者请求 surrender / continue
提交每个被请求的决定；决定缺失时暂停在该操作槽
遇到第一个有效surrender → 立即停止收集其余人，结束全场推进并请求裁决
其余未被请求者标记not_asked，不冒充continue或认输
否则到max_round → 请求裁决
否则 → 等下一世界回合
```

认输结束全场，而不是只退出认输者；这继承已定语义。首版按记录顺序逐人请求，第一名认输后不再询问其他人，避免人为收集一个本应已经结束的战斗里的“全员认输”；未被询问者的胜败仍由叙事者判断。**【已确认 R10】**裁决必须把认输者归入败者；多人是否共同获胜由叙事 Agent 根据前文判断，不能在第一个认输者返回时草率把其他人全判为胜者。

### 10.4 裁决契约【继承 + 已确认 R10】

```json
{
  "winner_ids": ["p_rook"],
  "loser_ids": ["p_pawn"],
  "narrative": "信使承认无法再说服守桥人，放下继续争执的姿态。",
  "rationale_summary": "结合本场此前叙事，信使已明确认输。"
}
```

校验：所有 ID 必须来自本场冻结成员；winner／loser 不重叠；二者非空且并集覆盖所有参战者；认输者必在 loser；不得声明其他棋子死亡、强制改变颜色或转移物件。系统只保证裁决协议合法，**不打分或覆盖胜负判断**。

本版不允许平局、中立者或全员败；所有参战者必须分入非空胜／败集合。不接受空数组或neutral字段逃避裁决。

叙事不应提前写死某人已经死亡。语义检查只能标记风险，无法凭 schema 确保散文完全自洽；发生明显冲突时进入研究者可见的内容复核／重新生成该未提交文本，不制造 HP 或偷偷改事实。

### 10.5 战后50句交涉与全体胜者释放【已确认 R08/R11】

胜者提出自由条件，败者可以交涉；只有结构化合法操作改变世界。仆从／未来效忠等条件仍是承诺，不锁死人格，不产生servant数值或自动服从。

按稳定ID顺序逐个处理败者，每个败者的settlement会话包含该场全部胜者和当前败者，**至多50句**，采用§7.2的共享会话引擎。不能每名胜者各开50句绕过上限；治理／终止投票为结构事件，不是额外台词。未轮到的败者保持锁定，不收到别人的私人条件台词。

- 任意一名胜者可提交execute_loser(current_loser)，只要目标尚存活且本场处置授权有效，立即执行§9.2；不需其他胜者同意。
- release是释放提案，不能单方使败者脱离处置。冻结本场全部存活胜者，逐一请求approve／reject；**全体明确approve才释放**。发起者的释放提案视为其approve，其他胜者必须表决。
- 任一reject意味着本次释放提案失败，继续在同一个50句预算内交涉；reject本身不授权系统自动杀死败者。
- 败者接受条件后在协议槽自愿执行本人change_color／transfer／drop等；转交仍需接收方accept。胜者不能代改色，条件执行也不自动等于释放。
- 释放／处决成功后关闭该败者的结算子会话，撤销相关pending操作；下一个败者获得自己的回应机会。

**有限终止协议**：第50句后、全员2个空许可轮或当前败者离席而仍未释放／死亡，结束自由台词并进入终止阶段。依记录顺序向**每一名胜者**发一个二选一许可：approve_release或execute_loser。任一明确execute立即死亡并停止剩余请求；全部明确approve_release才释放。不能只让第一名胜者决定释放，不能用超时或缺票代替同意／处决。

终止阶段非法／未返回有效选择按统一重试处理，耗尽则SETTLEMENT_PENDING技术暂停，保存已有表决但不自动判生死。一次最终死亡／释放的幂等身份绑定battle_id与loser_id；重复回调不会二次死亡或撤销已完成结算。

结算期间世界不进入下一world turn，其他角色不插入普通移动／新冲突；战斗status=settling，拒绝后来者加入。全部败者已处置后battle.closed、解除存活参战者位置锁定；本回合不补主行动，rest按已发生世界回合数计算。

## 11. 信息投影、上下文和记忆

### 11.1 一条上下文入口【工程】

`ContextBuilder.build(piece_id, stage, permitted_input)` 只接受白名单 DTO。角色请求入口在静态类型／模块依赖上不能拿到 WorldState、EventStore 全表读取接口或其他角色 Markdown。

```text
系统协议与本人初始人格（稳定部分）
本人当前结构状态／库存／当前可用Intent
本人当前视野内结构观测（缺失代表未观测，不代表不存在）
本人实际收件箱中的近期消息与事件
本人当前目标、短期记忆、长期记忆
本次阶段指令／会话剩余条数／本场战斗前文（如有权限）
输出Schema与提交规则
```

普通角色只读本人所有权的 MemoryVersion／GoalVersion；battle narrator 和 bard 使用独立 Builder，不能调用角色 Builder 后再“拼全知状态”。

### 11.2 可见性矩阵【工程 + 已确认 R12】

| 信息 | 本人 | 普通可见邻居 | 对话成员／战斗成员 | 盲区角色 | 全知研究者 |
|---|---|---|---|---|---|
| 本人类型／颜色／当前位置 | 是 | 当前视野内是 | 非在视野内不补最新位置 | 否；旧记忆仍可有 | 是 |
| 私人人格／隐藏目标 | 是 | 否 | 否；本人主动说出的台词另计 | 否 | 可查看，不能回灌 |
| 掉落object名称／描述 | 所在视野内是 | 所在视野内是 | 不因同会话自动知道 | 否 | 是 |
| 携带object完整库存 | 本人是 | 否 | 接收方知道收到的物件，非整个库存 | 否 | 是 |
| 普通会话消息 | 本人在场时是 | 无旁听 | 在该条提交时属于成员才是 | 否 | 是 |
| 路径／战后交涉台词 | 参与者是 | 无旁听 | 当次协议参与者才是 | 否 | 是 |
| 战斗开始／活动通知 | 参与者是 | anchor5×5内是 | 参与者是 | 否 | 是 |
| 战斗叙事 | 参与期间投影版 | 只知活动状态 | 参战者是，过滤他人私密来源 | 否 | 内部全文 |
| 死亡／墓碑 | 已见／被告知才是 | 现场可见死亡；之后只见墓碑 | 在场者见死亡；不在场普通会话成员不自动得知 | 否 | 内部含执行者 |
| 角色记忆文本／目标更新理由 | 是 | 否 | 否 | 否 | 可查看 |
| 吟游诗人成稿 | 不回灌 | 不回灌 | 不回灌 | 否 | 是 |
| 模型API返回的CoT | 本人操作记录可查 | 否 | 不广播，包括战斗叙事者CoT | 否 | 私密调试可查 |

“公开目标”是**允许本人宣称的目标**，不是自动全员知情字段。所谓公开台词是对当前 audience 公开，不意味着全棋盘广播。

**【已确认 R12】**角色开局是否知道某人的存在，由自己的私人资料决定，默认不知道其他角色。ContextBuilder只注入本人资料中明确写有的人物／往事，不自动生成全员名册或其他人初始坐标。为了便于场景作者准确指人，可在本人文件加入“初始已知人物”段，用稳定piece_id加本人认定的描述；未引用的人物不因配置中存在就自动知情。名字或记述属于本人认定，不自动认证其中的颜色／位置／关系。

相遇、会话邀请、战争消息等合法送达事件可以使角色之后得知新人物存在；记忆里已知的人离开视野也不会被强制遗忘。研究者可以查看完整场景配置，但不得因此替角色补知识。

### 11.3 事件投影与知情记录【工程】

每个内部事件先生成按角色区别的投影：`project(event, recipient, state_at_commit)`。在提交时保存确定的 recipient IDs 和各自 receipt payload；之后不因角色走近而补发旧事件全文。

一名角色离开会话后，不再收到后续台词；后来被邀请者只收到加入事件和之后消息。它可以主动向别人问前情，别人回答形成新的语言事件。

不要仅用事件级 `audience` 防泄漏。例如死亡事件的内部 payload 有 killer，但墓碑 receipt 只能有死者与地点；BattleNotice 不应附带本轮 narrative 原文。UI 切换角色视角应显示该角色收件箱与可见当前结构；研究者视角在单独接口读取。

### 11.4 战斗叙事者的特殊信息权限【已确认 R12】

为了保留“各方各自记得的版本”，叙事者可以读取每个参战者的**本人记忆文本**，分别标注来源；不读取非参战者记忆，也不自动读取参与者未进入记忆的隐藏目标／完整人格档案。

这带来一个真实的侧信道：记忆可能写有私人秘密，叙事者生成公共战况时可能泄露。推荐分别保存内部叙事结果和面向参战者的战况投影，禁止直接引用他人的私人回忆、隐秘动机或不可知往事；用提示词、来源标记和泄漏回归样本检查。**文本语义保密无法仅靠字段白名单完全证明。**

本版采用已确认的“叙事者内部读取各自记忆、限制对角色披露”方案，不新增先公开分享回忆的步骤。叙事者的API推理文本也只进私密操作记录，不作为角色战况。

### 11.5 有界文本记忆【工程建议 + 已确认 R14】

首版不必引入 embedding。私有存储分三部分：

1. `working_receipts`：最近实际收到的最多30条记录，入上下文还受 token 预算约束。
2. `memory.md` 当前版本：角色对经历的主观整理，不强制与历史事实对齐。
3. `archive`：所有 receipt 与 MemoryVersion 历史，只存不全量注入；检索只在本人已知归档内进行。

短期超过预算，启动独立有界整理，把本人旧收件与当前记忆交给本人或同人格 memory editor；返回新记忆及来源 receipt IDs，不能看世界整局日志补全它忘记的事。整理成功后才推进处理 watermark。失败的 memory job 保持 pending，避免 ai-town 参考实现中启动后提前清除记忆任务的丢失风险。

重要性不由系统固定赋给“死亡／背叛／印信”。角色可以在文本中表达珍视，检索起始策略用本人目标关键词、相关角色 ID、回合新近性和本人文本关联；这些是上下文选择机制，不是世界奖惩。任何检索都先按 owner 过滤，再检索；不能全库检索后只删输出。

每次整理的来源 ID 仅验证“本人确曾收到”，不验证它的推断正确。误记、信谎和错误归因保留为主观内容；重新观测时准确结构数据仍注入，允许角色面对矛盾。

## 12. Intent、事件与错误契约

### 12.1 Intent 封套【工程】

以下是服务端封装后的命令，身份字段由服务端填入，不让模型自行声明：

```json
{
  "protocol_version": "2.1",
  "world_id": "w_01",
  "slot_id": "turn:12:primary:p_rook",
  "attempt_id": "a_03",
  "generation": 7,
  "actor_id": "p_rook",
  "stage": "primary",
  "context_hash": "sha256:...",
  "read_set_hash": "sha256:...",
  "intent": {"kind": "move", "target": {"row": 3, "col": 6}}
}
```

模型只输出intent的严格判别联合，禁止额外字段。move含kind／target，可显式附arrival_action；transfer含kind／object_id／target_id；invite含kind／target_id；原地join_battle含kind／battle_id。普通会话输出say／silent_pass／leave／治理动作，通行交涉输出speech及proposal；各kind有独立判别schema，不使用所有字段都可选的万能对象。普通会话原文中的pass是silent_pass的别名，通行结论pass是放行，适配器不得混淆。

到场移动的模型正式输出示例：

```json
{
  "kind": "move",
  "target": {"row": 3, "col": 3},
  "arrival_action": {"kind": "join_battle", "battle_id": "b_01"}
}
```

物件接收输出为`{offer_id, decision: accept或reject}`；actor／world／generation由服务器填，不接受模型代另一角色回应。公开CoT由ModelAdapter单独解析，不是这些正式Intent的额外字段。

合法拒绝代码：`OUT_OF_BOUNDS / ILLEGAL_GEOMETRY / RESTING / BATTLE_LOCKED / TARGET_NOT_VISIBLE / NOT_HOLDER / OBJECT_NOT_HERE / TARGET_DEAD / MEMBERSHIP_CHANGED / SLOT_ALREADY_COMMITTED / STALE_GENERATION / STALE_CONTEXT / BATTLE_ALREADY_ACTIVE / BATTLE_NOT_AT_ANCHOR / BATTLE_CLOSED / RULE_PROFILE_UNSUPPORTED`。错误附可理解原因、可选类型与本人合法信息；不得在错误中写“失败因为盲区 B 正在这里”。

一次非法输出可以用原合法输入加错误原因修正一次；结构化能力和模型角色不同，不改变其人设。持续非法则暂停，不直接替它选 wait。

### 12.2 Event 封套【工程】

```json
{
  "event_version": "2.1",
  "world_id": "w_01",
  "seq": 104,
  "event_id": "w_01:104",
  "turn": 12,
  "phase": "PRIMARY",
  "type": "MoveCommitted",
  "actor_id": "p_rook",
  "slot_id": "turn:12:primary:p_rook",
  "payload": {
    "from": {"row": 3, "col": 1},
    "requested_to": {"row": 3, "col": 6},
    "actual_to": {"row": 3, "col": 2},
    "rest_assigned": 1,
    "resolution": "stopped"
  },
  "receipt_refs": ["r_rook_104", "r_pawn_104"],
  "previous_state_hash": "sha256:...",
  "resulting_state_hash": "sha256:..."
}
```

事件类型至少覆盖：`WorldCreated / SegmentStarted / TurnStarted / RestTicked / IntentRejected / MoveProposed / NegotiationStarted / NegotiationMessage / NegotiationResolved / MoveCommitted / ConversationCreated / ConversationMerged / ConversationClosed / ConversationMessage / MemberJoined / MemberLeft / VoteProposed / VoteCast / VoteResolved / ObjectPicked / ObjectDropped / ObjectTransferOffered / ObjectTransferRejected / ObjectTransferOfferCancelled / ObjectTransferred / ColorChanged / BattleStarted / BattleJoined / BattleMerged / BattleNarrated / SurrenderDeclared / BattleOutcomeCommitted / SettlementMessage / ReleaseProposed / ReleaseVoteCast / ReleaseProposalRejected / LoserReleased / DeathCommitted / TombstoneCreated / MemoryUpdated / GoalUpdated / TurnCompleted / SegmentPaused / TechnicalFailure / BardCreated`。

系统日志的 `RequestStarted / TokenUsage / RetryScheduled` 放操作日志，不作为角色世界事件。`IntentRejected` 只给本人必要反馈及研究者诊断，不自动传播给其他棋子。

### 12.3 一次执行的生命周期【工程】

```text
1. 准备：建立唯一slot，持久化输入快照／上下文／配置哈希
2. 请求：创建attempt，发起模型调用；不持有写事务
3. 接收：私密操作记录分别保存正式content、exposed_reasoning和usage
4. 校验：公开CoT存在／计数、正式格式、业务权限、来源ID、token预算
5. 提交：短事务确认generation、slot、read_set仍匹配
         写入结果、事件、世界、receipts、cursor
6. 呈现：事务成功后推送事件；重发由事件ID去重
```

HTTP200只表示传输成功，不表示内容成功。若事务提交后进程在广播前退出，重连按 seq 补发；不重调模型。若接收后提交前退出，恢复可使用已持久化、验证且输入仍匹配的结果；不可恢复的 pending 调用只能重试，输出可能不同，不宣称模型端 exactly-once。

slot 表示语义位置，attempt 表示重试次数。消息身份是 `slot_id:message_index`，重复短句可以是不同位置的合法台词；不能用文字去重。结果文本、投影文本、事件 ID 和收件记录绑定。

## 13. 模型接口、token 与失败处理

### 13.1 适配接口【工程】

```text
ModelAdapter.generate(request) -> ModelResult
Request: role, messages, output_schema, max_output_tokens,
         timeout, sampling_config, cancellation_token
Result: content, structured_output?, exposed_reasoning, reasoning_token_count,
        usage?, finish_reason, provider_request_id?, latency_ms, capability_notes
```

模型角色分开配置：`character / memory_editor / battle_narrator / bard`，初始可用同一模型，但实验 manifest 记录各角色配置。身份人格同样参与动作决策，不能像部分参考实现只影响台词。

支持能力以 `(provider, endpoint, model, adapter_version)` 为键记录：严格 schema、JSON 模式、工具调用、streaming、usage、exposed_reasoning、reasoning预算、context容量、tokenizer。所有实际调用的模型必须先通过公开CoT能力探测，不支持则MODEL_COT_UNSUPPORTED；先用无真实角色秘密的样例探测，再进行少量真实场景验证。

优先使用严格结构输出；不支持时用 JSON 模式加相同 schema 指令并业务校验；连 JSON 模式也无时允许完整文本 JSON 校验。这是输出格式兼容，不是允许错误输出降级成世界事实。不按模型前缀猜能力。

### 13.2 初始上下文预算【工程建议】

| 调用 | 输入目标上限 | 正式输出上限 | 内容 |
|---|---:|---:|---|
| 角色主行动 | 4,096 token | 512 | 正式JSON；公开CoT另计，最多1000token |
| 普通对话／交涉发言 | 4,096 | 256封套，其中 `speech/text` ≤100 | 遵守v1短句限制 |
| 战斗认输选择 | 4,096 | 128 | 本人记忆＋本人可见战況 |
| 记忆／目标整理 | 6,144 | 1,024 | 私有文本版本与来源引用 |
| 战斗叙事／裁决 | 12,288 | 768 | 每参战者记忆暂分配≤800，已有叙事另计 |
| 吟游诗人单块 | 12,288 | 2,048 | 事件块＋受控引文＋引用 |

这些是起始控制值，不是模型质量保证。完整本场叙事可能超过认输输入预算，须压缩本人版战況，不丢当前认输协议；如果仍放不下，技术暂停并调参数。

模型上下文须满足input＋正式completion预算＋公开CoT预算＋协议余量。正式输出与CoT分别计数，再按提供方实际总completion限制配置请求。适配器必须有与所用模型对应的可验证token计数方案；不可准确计数时启动报TOKENIZER_UNAVAILABLE，不以字符估算冒充100／1000token硬上限。超长台词或CoT在提交前重试／报错，不字节截断JSON，不把超长推理截短后宣称原响应合规。

### 13.3 公开CoT模型准入、隔离与完整呈现【已确认 R15】

demo只支持模型API显式返回推理文本的配置。这里“公开”指提供方允许客户端拿到的reasoning字段／明确标记段；不尝试恢复提供方未公开的内部思考，也不宣称返回文本就是全部内部计算。

**能力准入**：

1. 启动时对character／memory_editor／battle_narrator／bard所配置的每个provider＋endpoint＋model＋参数组合探测；必须能在正式content之外返回可识别、非空的公开CoT样例，且有token计数方案。
2. 不支持／只有“推理模型”名称却不返回文本／只返回reason_summary／只能得到reasoning token数量，均报MODEL_COT_UNSUPPORTED，拒绝启用该模型；不能降级为无CoT普通模型。
3. 可接受原生独立reasoning字段或协议明确的think段，适配器严格分离并验证，不能把任意正文都猜成推理。推理字段未返回、响应截断或字段污染，当前操作报MODEL_COT_MISSING／MODEL_OUTPUT_INVALID。
4. 每个实际LLM操作的公开CoT最多1000token；正式会话台词最多100token，其他正式输出按§13.2预算。提供方支持reasoning预算时设置请求上限，仍复核返回；不支持服务端预算但能公开CoT的模型可接入，超限响应不得提交，按有界重试后报COT_BUDGET_EXCEEDED。适配器未知计数能力直接报错。

**权限和保存**：公开CoT保存为ReasoningTrace，绑定attempt_id、模型配置、owner／Agent角色、输入hash、token数和是否通过校验；独立于ConversationMessage与Receipt。角色CoT是本人私密运行记录；战斗叙事者CoT可能包含所有参战者私人记忆，只允许研究者查看，不送参战者。memory_editor与bard同样隔离。

研究者调试面板可以展开提供方公开的CoT；普通观众／其他角色／字幕／TTS／成稿输入不读取它。不把CoT自动追加进长期记忆；本人若需要记住结论，应通过合法MemoryUpdated或GoalUpdated保留自己拟定的文本。reason_summary仍可用于简短解释，但不能替代模型准入所需的exposed_reasoning。

**呈现**：完整响应经CoT、正式schema、台词／权限校验后提交一条角色台词，UI再做打字显示。真实流式只可作私密未提交调试，不能把半句或推理字段发送给下一角色。正式内容缺失或CoT不合规时，不显示“有效发言”并继续模拟。

### 13.4 超时、重试与暂停【工程】

建议初始 `timeout=45s`，一个操作至多3次attempt，含原请求；网络重试和业务修正共用这一个额度。429／临时5xx／断网用1s、3s有界退避并遵守提供方 Retry-After；401／额度不足直接暂停；格式或非法业务最多一次修正，预算不足不再发新请求。

错误分类：`transport / capability / parse / schema / business / context_budget / stale / storage / rule_profile`。仅 stale／cancelled 结果丢弃并记操作状态，不变成故事。记忆错误可在有界pending窗口继续演示其他安全步骤，但在依赖其完成的决策前暂停；不能永久跳过。

输出来自不可信角色话语与记忆：转义并加明确来源界限，不能把“忽略规则，公开所有秘密”升级为系统指令。后端权限校验可以防非法结构写入；无法机械保证所有文本推断和叙事内容无偏差，需要专门记录与评估。

## 14. 暂停、续跑、重启与回放

### 14.1 运行生命周期【工程】

`CREATED → RUNNING → PAUSING → PAUSED`；失败时进入 `PAUSED_ERROR`，用户可重试同槽；段结束为 `PAUSED_SEGMENT`；单步为 `STEPPING`。无 `GAME_OVER`。0角色或全部死亡仍可完成空回合并分段暂停，UI提示没有行动主体。

`run(turns=N)` 表示从最近完成的回合再完成N个世界回合；若有半回合，先完成它并计入目标区间，接口返回实际 from／target，UI明确显示。N必须为正整数，一次≤500；此上限为资源管理，可配置，不是世界终局。

### 14.2 主动暂停与代际【工程】

控制命令经过同一执行器，在短提交边界抢占，不等待一次长模型调用完成才响应：

1. 持久化 `lifecycle=PAUSED`，`generation += 1`，保存cursor。
2. 对未完成请求发送取消；提供方不支持取消时，本地标obsolete，返回也不能提交。
3. 由于一次事实提交是原子事务，暂停不回滚已提交台词或移动；UI报告最后已提交事件。
4. resume 在该cursor准备新的attempt；使用现有已提交前文，不重放已发生的对话。

用户 `reset` 建议创建新 world ID，保留旧世界和历史；不是清空数据库。数据删除是另一个显式功能。暂停、重置、快进和测试回放都用代际校验，不依赖客户端“停止动画”代替后端停止。

### 14.3 重启恢复【工程】

服务启动只恢复存档为暂停状态，不自行重新花费模型额度。加载最近checkpoint与之后已提交事件，验证state hash与文本引用；对旧generation的pending调用标obsolete；保留未完成slot、交涉、投票、战后结算和记忆job。用户续跑后只补未提交操作。

自动保存必须覆盖事件批、receipts和运行cursor，不能只保存棋盘截图。SQLite备份用备份接口或一致性导出，不在写入中只复制 `.db` 忽略WAL。

### 14.4 回放与重新实验【工程】

- **历史回放**：从checkpoint折叠事件＋已提交文本版本，不调用模型；相同日志必须恢复相同结构和当时各角色收件记录。
- **重新生成实验**：相同场景重新调用模型，胜负和故事可以不同。这是已接受的设计特性。
- **分支实验**：以后可从checkpoint复制世界及各人已知信息，建立新world；manifest保留parent world/seq，不能把未来记忆带回过去。

确定性的日志回放不否定战斗生成的非确定性。seed只约束本地调度等软件随机性，不能保证所有提供方输出相同。

## 15. 吟游诗人与叙事产出

### 15.1 输入与作用【继承 + 已确认 R12/R15】

每段完成后生成本段故事；研究者主动暂停半回合时可以生成明确标注“未完片段”的稿件。生成失败只使稿件待重试，不撤销已发生模拟。

默认“事件叙事版”：已提交世界／语言事件、战斗公开叙事及上一段故事摘要，不自动加入所有人的隐藏目标、私人记忆与未公开理由。另可研究者主动生成“全知研究版”，有明显标签，永不回灌棋子。

吟游诗人只编排，不决定下一回合动机，不创设系统事件，不修改裁决。“某人这样声称”“某人记得”要保留主体；对公开冲突的含义可以文学解释，不能把指控写成已经证实的物件变化或死亡。

### 15.2 可追溯输出【已确认 R15】

```json
{
  "segment_id": "s_02",
  "title": "桥前的停步",
  "paragraphs": [
    {
      "text": "守桥人没有走到桥的另一端。他停下了，信使仍守着那个格子。",
      "event_refs": ["w_01:104", "w_01:105"],
      "claim_kind": "event_based"
    }
  ]
}
```

每段至少一个本段或明确允许的历史event引用，引用只能来自实际提供的输入。自动校验：ID存在、world正确、位于允许区间、没有未公开私密字段引用。语义上“引用确实支持每句话”只能辅助校验／人工抽查；存在引用**不能保证**散文没有幻觉。

长段按有序事件块生成带引用摘要，再合成篇章，引用展开回原始事件，不只指向摘要。保留版本、prompt hash、模型配置与重试记录；故事覆盖允许多次重新编排，不能覆盖原始事件。

## 16. 用户界面与 API

### 16.1 演示界面

1. **场景页**：棋盘尺寸、棋子类型／颜色／起点、物件、私人档案与初始已知人物编辑入口、demo-v2.1规则摘要；加载校验可定位问题。
2. **运行页**：棋盘＋事件时间线＋会话／战斗面板；当前回合／阶段／actor、模型等待状态、累计调用与token、预算状态。
3. **控制栏**：开始N回合、暂停、继续N回合、单步一次提交、单步完成一个世界回合、重试当前失败、导出、历史回放。
4. **视角切换**：研究者全知、普通观众、某棋子；棋子视角显示雾区、本人观测／收件箱／记忆，不能只把地图变暗却仍展示全知消息。
5. **详情面板**：位置／rest／参与状态；自己的记忆与目标版本；全知研究模式可检查各角色输入、正式输出与API公开CoT；CoT默认折叠，不作为普通事件台词，不尝试展示未公开的内部思考。
6. **段故事页**：吟游诗人稿件、事件引用跳转、待重试状态；不阻止读取已完成世界历史。

移动动画从from到actual_to，只是事件呈现；动画途中不制造新遭遇，鼠标暂停动画不能改变世界结果。64×64用视区渲染格线／缩放，8个棋子与地面实体独立绘制；角色样式区别type与color，不能凭颜色暗示固定友敌。

### 16.2 REST 与 SSE 契约【工程】

| 接口 | 作用 |
|---|---|
| `POST /api/scenarios/validate` | 校验配置／私人文本，返回字段路径错误，不调用模型 |
| `POST /api/worlds` | 从冻结场景创建世界，返回world与初始seq |
| `GET /api/worlds/{id}?view=observer` | 状态snapshot、seq、phase、cursor摘要 |
| `GET /api/worlds/{id}/perspectives/{piece_id}` | 当前局部观测＋本人已知投影，明确仅供本地研究者查看 |
| `POST /api/worlds/{id}/commands` | `{command_id, kind, turns?}`；kind为run、pause、resume、step_event、step_turn、retry或reset；返回202和command状态 |
| `GET /api/worlds/{id}/commands/{command_id}` | 查询命令进度、实际目标回合／失败原因 |
| `GET /api/worlds/{id}/events?after_seq=...&limit=...` | 升序补发已提交事件投影与next_cursor／has_more |
| `GET /api/worlds/{id}/stream?view=...` | SSE；`id=event_id`，支持Last-Event-ID或显式游标 |
| `GET /api/worlds/{id}/segments/{segment_id}/story` | 分段故事与引用 |
| `POST /api/worlds/{id}/segments/{segment_id}/story` | 请求生成／重编稿件，独立操作槽 |
| `POST /api/worlds/{id}/export` | 导出指定可见范围与版本清单 |
| `GET /api/worlds/{id}/replay?at_seq=...` | 无LLM折叠到指定点 |

初始实现绑定localhost；模型密钥从后端环境／本地配置加载，不进入场景、事件、前端bundle或导出。若之后公开部署，必须补鉴权和研究者视角权限，不能认为上述 observer接口天然适合多人使用。

前端先GET snapshot得到S，再从`after_seq=S`连接；后台补发事件直到追上，不遗漏GET和连接之间提交的数据。按event_id去重，批量按seq排序。积压时分页补发，禁止仅返回最后N条后把cursor跳到最新。

SSE断线只影响呈现；慢客户端不阻塞世界执行器；推送队列超限时断开并要求从账本补发。心跳不作为世界事件，避免角色看到“网络心跳”。

### 16.3 导出契约

导出zip包含 `manifest.json / scenario.json / events.jsonl / checkpoints.json / stories/`，全知研究导出可另含 `private/piece_id/{persona,goals,memory_versions,receipts}` 和模型请求／正式输出／API公开CoT记录；普通观众导出不含私人资料或CoT。Manifest包括schema／rule／prompt／adapter版本、源场景hash、角色顺序、各模型配置、规则、区间和预算用量。

不得导出密钥和连接鉴权头。导出前后hash可复核。导出格式为资料交换；数据库仍是运行真源。

## 17. 成本、吞吐与资源边界

### 17.1 按实际调用计算

令 `D`=主行动请求数，`S`=普通会话发言许可请求数，`G`=交涉发言／确认请求数，`J`=邀请即时回应、转交accept／reject及治理投票请求数，`M`=记忆／目标整理请求数，`B`=战斗叙事请求数，`U`=战斗认输选择数，`O`=战斗裁决数，`L`=战后结算请求数，`A`=吟游诗人请求数，`R`=额外attempt：

```text
总请求数 = D + S + G + J + M + B + U + O + L + A + R
每世界回合 D ≤ 8，S ≤ 8（普通会话每成员一次许可）
每个交涉／战后会话的角色台词 ≤ 50，但静默／表决请求另外计量
每个未合并战斗epoch B ≤ battle_max_rounds（默认4），U ≤ Σ每轮人数
最终裁决O=1；被合并父场尚未裁决，战争谱系总B可超过4
token总量 = Σ各请求真实输入＋正式输出＋提供方报告的计费推理token
价格估算 = Σ按实际模型计费单位折算；价格表属于运行配置，不写死于规则
```

不会因为结构化Intent返回就额外执行一次“LLM工具调用”；视野主动注入，没有这笔固定双倍成本。交涉、对话与整理是额外调用，应按日志计量。

例：8人20回合，假设有160个主行动请求、80个普通会话许可、2次两人交涉各实际4句（8请求，不是2轮上限）、一场3人4轮战斗（4叙事＋12认输选择＋1裁决）、16次记忆整理＋1次成稿，共 **282请求**；这里没有计邀请回应／治理／战后结算／重试，必须另加 `J+L+R`。这只是明确假设的预算样例，不是整局上界或实测。

无合并的单场8人4轮：4次叙事＋32次认输＋1次裁决=37基础请求，再加战后结算和重试。叙事者每轮输入各人记忆约随人数增长；若把全体记忆再注入每个人的认输请求，合计可能近二次增长且泄密。正确做法：叙事者看分别标注的各方记忆，各人只看自己记忆＋已送达战況。

### 17.2 运行预算【工程建议】

初始本段设置 `max_calls=1000`、`max_total_tokens=2_000_000`、`max_wall_seconds=1800`；用户可调整，UI启动前显示，未提供模型usage时标记估算值。一次运行首版仅一个活动世界，模型并发默认1。

每次发请求前预留输入＋最大completion预算，余额不足则暂停于安全提交点，保留cursor。实际usage回来后结算；提供方隐藏计费和在途请求无法本地绝对控制，所以金额只能是估算，**不能承诺严格账单硬封顶**。最大请求数可以本地严格执行。

预算耗尽叫 `PAUSED_BUDGET`，不自动“无人认输”“所有人和平离场”，不完成尚未完成的世界回合。前端动画和音频不阻塞引擎；首版不启用TTS以减少串行等待。

## 18. 测试矩阵与验收

### 18.1 不靠模型验证的规则测试

| 编号 | 场景 | 必须满足 |
|---|---|---|
| T01 | 0棋子／0物件，连续运行3回合 | TurnCompleted共3个；段暂停；无终局／模型调用 |
| T02 | 边缘视野、3×3与5×5 | 精确截断；盲区身份／物件不进入上下文 |
| T03 | 兵正四向、斜向有活人／空格／墓碑、马起终点、改色 | 兵条件精确；马无中间格；改色不改几何；无升变 |
| T04 | T1滑行2格／马一次 | 普通T4、马T5才恢复移动；等待不受墙钟影响 |
| T05 | 路径多个阻挡格，同格3成员 | 由近及远，一格一次协议；不是3个代表制交涉 |
| T06 | 双向、单向、互不能威胁 | mode符合R05；能力冻结在移动发起前 |
| T07 | 被截停／同格退让／马落点冲突 | 无非法中间位置；rest按R06；不双重触发目标协议 |
| T08 | 邀请后立刻离席 | 邀请额度消耗；先退出再决定是否有首句 |
| T09 | 两人踢人、三人投票、合并会话 | 两人空操作；快照表决；额度与消息预算不重置 |
| T10 | 第50条台词／成员pass | 第51条不提交；pass不计消息；上限回合外也不重置 |
| T11 | object拾取／接受／拒收／重复回应／丢弃／死亡 | 接受才转移；拒收不变；发起行动仍耗；所有权唯一、死亡原子 |
| T12 | 广播范围内远处join、移动被截停、实际到场、最后一轮前抵达 | 远程join拒绝；到场才登记；完整胜败分区、无中立者 |
| T13 | 胜者释放／杀死／非法执行／仆从承诺 | 授权正确；承诺不篡改代理权限；墓碑不含killer |
| T14 | 同格初始、真正合并、重复join、多胜者释放／处决 | 合并轮数重算；单人加入不重置；释放全票、处决任一人授权 |
| T15 | 通行／同格／战后50句、空许可轮、末句结论 | 同引擎计数；第50句先判结果；未达成按各kind收束；无2轮上限 |
| T16 | 无CoT／缺失CoT／超1000token／正式content混推理 | 模型准入或操作报错；推理不入台词／他人receipt；无降级 |

### 18.2 信息边界：同时测“应有”和“不得有”

用唯一哨兵文本，不用常见词判断泄漏：A的隐藏目标 `SECRET_A_7f2`、B曾听到的台词 `HEARD_B_93d`、盲区物件 `BLIND_OBJ_c8a`。

- A的决策、发言、投票、认输、自省全部包含自己的合法资料；不含B隐藏目标。
- B确实听到的台词在其近期输入中应存在；C当时不在场则不存在，即使之后加入。
- 目击者能收到死亡执行事实；只看墓碑者只能得到死者身份与地点；内部killer不穿透投影。
- 载物者看到本人库存；邻居视觉观测中没有该库存；接收者只知自己收到之物。
- bard默认事件版不含私人哨兵；battle narrator特殊输入权限与面向角色输出分开测试。
- 导出与UI角色视角不得串用observer数据；角色API错误文本不能泄露盲区具体人名。
- 开局资料没写其他人则输入无自动名册；资料明确知道B则保留本人认定，不补B当前坐标。
- 所有API公开CoT哨兵只在授权研究者／本人操作记录可查，不出现在台词、他人输入和成稿。

这些测试说明某些字段没有从软件通道泄漏，不证明模型永远不会猜中秘密；猜测要作为猜测记录。

### 18.3 协议、异步与恢复

固定响应样例包含：字段顺序变化、未知字段、截断JSON、未转义引号、`reasoning`嵌套、聊天封套、重复短句、空字符串、超长台词、无usage、不支持schema、无公开CoT／推理混正文／超1000token、超时与429。

必须验证以下窗口：

- 用户暂停／重置后旧请求返回，零旧事实提交。
- 同一slot重复回调、网络重试和浏览器重复command，只执行一次。
- 提交成功但广播失败，补发恢复，不再次调用模型。
- 在交涉中、投票中、战斗轮之间、裁决前、战后结算中、拒收协议pending、记忆整理中杀进程，恢复为暂停且可接续cursor。
- 存储失败不推进cursor、不显示成功台词；未持久化消息不进入下一角色输入。
- 回放前后state hash、goal／memory版本引用、各角色收件箱相同。

### 18.4 真实模型与叙事验收

先做2人5回合接触，再4人20回合、8人20回合。真实模型先通过公开CoT能力探测，分别记录输入→API公开CoT／正式输出→事件→下一角色输入；不得仅以HTTP200或“跑完了”作为成功。

运行验收：不变量违规0、跨角色字段泄漏0、旧请求提交0、恢复丢事实0、固定日志回放差异0；达到本段回合数或清楚报告技术暂停／预算暂停。对于已批准规则档的完整演示场景，不接受用未支持规则分支结束。

另外人工记录：角色是否持续体现自己的价值观、是否自主使用object、是否出现信息传播／误认／违约、战斗结论是否有前文支撑、吟游诗人是否编造结构事实。可以评价文本质量，但不得把分数回写成角色奖励。

### 18.5 实验记录

建议准备：无冲突共处、目标相容、物件意义相反、隐藏目标冲突、墓碑调查、三人同格、第三方加入战斗、大棋盘稀疏探索八种场景。固定响应用于工程边界测试，不用脚本强迫真实角色战斗。

同一场景重复多次，保存模型／prompt／规则／角色顺序／初始文本。比较时一次只改一个因素：单一人设句、目标文本、初始布局、视野规则档或行动顺序；无需数值忠诚度才能做文本干预，但结果解释必须考虑措辞和模型随机性。

描述性指标：每10回合遭遇／会话／交涉数、有效行动分布、目标更新理由类别、信息传播路径、战斗人数与轮数、总calls/token/延迟、无互动区间。和平或轮空多是实验结果，不能由导演偷偷制造威胁来“修复”。

## 19. 实施计划与交付顺序

R01–R16已确认，实施先冻结本版规则档和工程参数，再打通纵向流程。按依赖分阶段，不承诺未经估算的工期。

| 阶段 | 实施内容 | 退出条件 | 依赖 |
|---|---|---|---|
| P0 | 固化demo-v2.1、schema／模型公开CoT探测样例、规则版本 | 本版确认结果映射一致，接口与几何可验证 | 本规格 |
| P1 | 场景加载、兵／马几何、视野／rest、Intent、纯规则内核 | T01–T04通过；零LLM可运行 | P0 |
| P2 | SQLite、操作槽、事件／投影、cursor、恢复／回放 | 移动／wait恰执行一次；杀进程恢复一致 | P1 |
| P3 | 合格ModelAdapter、ContextBuilder、共享50句会话、2人移动交涉与成稿 | 真模型5回合；CoT与台词分离；故障可重试 | P2 |
| P4 | 普通会话／合并／投票、物件可拒收、改色、目标／记忆 | 4人20回合；额度／所有权／私密矩阵通过 | P3 |
| P5 | 到场参战、合并重算、认输、胜败分区、全体释放与死亡 | 战斗／结算边界闭合；T12–T16通过 | P4 |
| P6 | SVG、SSE重连、研究者CoT面板、导出、8人／64×64、实验场景 | §18验收通过；本地启动与故障档案齐全 | P5 |

固定响应fixture用于工程测试，真实前向叙事战斗必须用合格模型验证。每阶段交付启动命令、必要测试、可复核存档、限制及版本；本次文档修订未完成这些实现验收。

## 20. v2.0 → v2.1 变更与覆盖关系

| v2.0候补方案 | v2.1执行规则 | 来源 |
|---|---|---|
| 兵独立前进方向／斜前威胁 | 正四向一步、占人斜四向一步、斜四向威胁；无方向／升变 | R01自定义 |
| 马只描述落点扫描 | 持久路径明确[起点,终点]，扫描排除起点 | R01澄清 |
| 范围内原地远程加入战斗 | 必须走到anchor，广播只是消息；被截停不算加入 | R07修订 |
| 合并取最小剩余轮数 | 新epoch round=0，按配置重算max_round（默认4） | R07＋工程细化 |
| 通行交涉最多2轮 | 与对话共用引擎，最多50句，提前合法结果／退出／空轮收束 | R08修订 |
| 一名胜者释放即全场生效 | 处决任一胜者可执行，释放全体胜者一致 | R11选择B |
| 战后2轮＋第一胜者终止 | 战后50句＋全体胜者终止选择，任一执行／全票释放 | R08／R11联动 |
| 初始资料之外是否给全员名册待议 | 本人资料决定已知人物，默认不知，不自动补名册 | R12修订 |
| 转交无需接收者同意 | 一次accept／reject，接受才移交，拒绝不变 | R13修订 |
| 无公开CoT可降级接入 | 公开CoT为模型准入硬条件，不支持报错；推理记录隔离 | R15修订 |
| 其他Rxx仍是未批准建议 | 用户选A／推荐方案的内容成为本版执行规则 | R02–R06、R09–R10、R14、R16 |

不得在v2.1保留兵forward_row_delta、远程join权限、交涉2轮强制开战、单方release、最小剩余合并预算、无CoTfallback等旧路径。前版存档如要导入，须显式迁移规则版本；历史回放仍使用原规则与事件，不把旧世界行为改写成符合新规则。

## 21. 依据、源码复核与适用范围

### 21.1 设计基线与研究报告

参考项目研究报告保留在仓库外的 `Project_APVH/docs`，不随本 Git 仓库分发；下列本机链接仅用于追溯。

- [ChessX v1.0](history/ChessX-Spec-v1.0.md)、[设计决定清单](history/设计决定清单.md)。早期评估文档用于理解演变，不覆盖后续已定立场。
- [wolfcha项目研究报告](../../../Project_APVH/docs/02-wolfcha/wolfcha项目研究报告.md)：§4信息隔离、§6公开输出、§7异步身份。
- [ai-town项目研究报告](../../../Project_APVH/docs/01-ai-town/ai-town项目研究报告.md)：§3–5世界写入、§7规则驱动边界、§8记忆。
- [jubensha-ai项目研究报告](../../../Project_APVH/docs/03-jubensha-ai/jubensha-ai项目研究报告.md)：§4三层角色、§6调度、§7私密证据、§11事件通信。

v2.0研究阶段重点读取上述机制与配套技术资料，不把社区星数、在线活跃度或报告中的旧依赖版本作为架构选择依据。

### 21.2 本地源码核对位置（2026-10-01）

| 参考ID | 本地文件与位置 | 核对内容 |
|---|---|---|
| W1 | `../Project_APVH/.research/wolfcha/src/lib/game-flow-controller.ts:17`、`:26` | 捕获token，interrupt使旧token失效 |
| W2 | `../Project_APVH/.research/wolfcha/src/lib/speech-request.ts:10`、`:17` | 对局／阶段／发言轮身份与FlowToken联合校验 |
| W3 | `../Project_APVH/.research/wolfcha/src/lib/streaming-speech-parser.ts:16`、`:35`、`:45`、`:70`、`:84` | 公开字段白名单、role过滤、对象闭合后发布与半句防护 |
| A1 | `../Project_APVH/.research/ai-town-snapshot/convex/engine/abstractGame.ts:127`、`:133`、`:173` | 读取与提交generation校验，单调输入序号 |
| A2 | `../Project_APVH/.research/ai-town-snapshot/convex/aiTown/agent.ts:57`、`:94`、`:103` | 超时清操作、记忆任务启动后提前清待记忆标记 |
| A3 | `../Project_APVH/.research/ai-town-snapshot/convex/agent/memory.ts:205`、`:298`、`:374` | 检索维度与reflection来源ID；借鉴来源追踪，不照搬评分 |
| J1 | `../Project_APVH/.research/jubensha-ai/backend/src/agents/character_identity.py:31`、`:43` | 稳定身份与私密资料块 |
| J2 | `../Project_APVH/.research/jubensha-ai/backend/src/agents/character_memory.py:46`、`:61`、`:65`、`:81` | 有界工作记忆、私有日志、私有证据；不移植怀疑度数值 |
| J3 | `../Project_APVH/.research/jubensha-ai/backend/src/agents/character_agent_manager.py:60`、`:80`、`:94` | 只广播say、私密证据单独写；ChessX不能照搬全员广播范围 |
| J4 | `../Project_APVH/.research/jubensha-ai/backend/src/core/game_engine.py:47`、`:62`、`:97` | 内存运行结构与历史切片；取事件思路，补持久恢复 |

wolfcha所读工作目录当前HEAD：`d07929afefd9c7454ecb7c7bfd594f4693d76c17`，并不宣称工作目录内容一定等于该提交。ai-town-snapshot与jubensha-ai本地目录没有可读Git元数据，未虚构提交号。用于定位本次读取内容的文件SHA-256：

```text
wolfcha/src/lib/game-flow-controller.ts
7be97c53f05d35fe73aff3dc529760de75db9254929510bb3d56c0a8ff2de008
ai-town-snapshot/convex/engine/abstractGame.ts
9e115dacfffc46c03b2134bf20f7f21b64cff933fa74e2777881626f6669a9c1
jubensha-ai/backend/src/agents/character_memory.py
759271617b0f8e89fe0f2d9e5de15fa637526ca971c445a3ebec606c481e8b46
```

源码定位与hash沿用v2.0的静态复核记录；本轮v2.1修订根据用户确认更新规则及工程联动，没有重新运行参考项目或调用真实LLM。技术栈已确认，预算仍是工程起始值，不是实测。以后三者代码改变时，先重新核对引用，不直接按旧行号复制代码。

## 22. 最小执行示例与规则档落地

### 22.1 一个无战斗的固定响应验收样例

以下是**工程fixture**，用于验证权限、事件与恢复，不规定真实角色应做这些选择。

初态：8×8；A为车，位置`(0,1)`；B为车，位置`(0,2)`；两人rest=0；印信ground`(0,2)`；actor_order=[A,B]。二者互相看得见；双方记忆和目标不同且互不公开。

| 步骤 | 固定Intent／回应 | 预期事实与知情 |
|---|---|---|
| T1-A主行动 | move至`(0,5)` | 建立PendingMove，当前位置仍为`(0,1)` |
| T1路径交涉 | 双方最终明确stop | 双向威胁为平等；移动停`(0,1)`，实际位移0、rest0，PendingMove关闭 |
| T1-B主行动 | pickup印信 | object唯一位置变carried(B)；B知道库存；A见地面印信消失，不获完整B背包 |
| T1结束 | 完成阶段F/G | 两人行动槽都已用；checkpoint可恢复；没有“因未移动自动开战” |
| T2-A主行动 | invite B | 建立会话；B先stay；A尚未发言的私有草稿不送B |
| T2-B主行动 | wait | 不消耗未来移动能力，不自动离席 |
| T2会话A | say“我看到印信不见了。” | 一条已提交消息，B实际收到；普通观众可按会话投影阅读 |
| T2会话B | say“你是在指责我吗？” | B输入包含A刚才原文；这是一句反问，不是偷窃事实 |
| T2结束 | 本段目标回合=2 | 世界暂停，会话仍2条；吟游诗人输入含发生记录，不含B隐藏目标 |
| 续跑T3 | 用户再跑1回合 | 不重调T1/T2已提交槽；会话仍从2条继续；本人是否交出印信由后续决策决定 |

这一fixture在A与B之间使用了视野内公开变化，但没有让A从“消失”直接读取B当前物件字段。它可以猜测、指控和调查，猜测属于角色层。

### 22.2 暂停窗口样例

准备槽`turn:3:ordinary:c1:A`，generation=5；请求进行中。用户pause提交generation=6，cursor仍指该槽。旧请求返回：即使JSON完整，也只记obsolete，不写ConversationMessage、不计50条、不送B。resume为同一slot创建新attempt，按现有合法输入请求；成功只产生一个消息身份。不能把旧请求与新attempt的两句都显示为已发生台词。

### 22.3 已确认规则档与工程参数

以下manifest进入场景hash、存档和请求规则摘要；Rxx对应用户决定，工程值为本版落实方式，可在新版本调整，运行中冻结。

```json
{
  "profile_id": "demo-v2.1",
  "status": "confirmed",
  "spec_version": "2.1",
  "selections": {
    "R01": "pawn_orthogonal_step_diagonal_occupied_threat_diagonal_knight_two_cells_no_promotion",
    "R02": "rest_blocks_move_and_invite_only_join_requires_anchor",
    "R03": "serial_config_order_start_rest_snapshot_turn_lock",
    "R04": "all_live_occupants_endpoint_once_mover_returns",
    "R05": "advantage_priority_neither_equal_any_edge_unanimous_party",
    "R06": "actual_displacement_pre_blocker_knight_origin_rest_zero_if_blocked",
    "R07": "physical_arrival_anchor_next_turn_round_merge_recalculate",
    "R08": "shared_conversation_engine_fifty_messages",
    "R09": "ordinary_one_per_member_per_turn_merge_preserves_budget_move_leaves",
    "R10": "complete_nonempty_winner_loser_partition_no_neutral_surrender_loses",
    "R11": "any_winner_execute_all_winners_release_terminal_all_winners",
    "R12": "initial_knowledge_persona_default_unknown_no_eavesdrop_private_narrator_no_bard_feedback",
    "R13": "standalone_object_actions_recipient_accept_or_reject",
    "R14": "readonly_initial_persona_versioned_goals_five_turn_reflection",
    "R15": "exposed_cot_required_private_trace_validate_then_display_event_referenced_bard",
    "R16": "local_fastapi_react_sqlite_one_active_world"
  },
  "engineering_parameters": {
    "battle_max_rounds": 4,
    "merge_round_counter": "reset_zero_use_configured_max_rounds",
    "conversation_max_messages": 50,
    "speech_max_tokens": 100,
    "exposed_cot_max_tokens": 1000,
    "ordinary_idle_world_turns": 2,
    "negotiation_idle_permission_cycles": 2,
    "reflection_interval_turns": 5,
    "transfer_response_policy": "one_valid_accept_or_reject_no_main_action_cost",
    "model_max_attempts_per_slot": 3
  }
}
```

加载器验证16项齐全、参数有效、模型公开CoT能力合格。需要改变规则时建立新世界或显式新规则分支，不在已有战斗里悄悄替换胜败／释放／计数协议。

### 22.4 新规则的边界示例

| 示例 | 预期 |
|---|---|
| 兵位于(3,3)，到(3,4)空格 | 正向一步合法，rest=1；不表示兵能威胁该正邻格 |
| 兵到(4,4)，目标只有墓碑／object | 斜移非法；斜向威胁几何仍成立 |
| 兵到(4,4)，目标有存活棋子 | 斜移合法，目标同格交涉；没有吃子 |
| 马(1,1)→(3,2)，中间格有人，终点空 | 路径只有两格，不检查中间；成功rest=3 |
| 马终点被截停／拒绝共处 | 留(1,1)，rest=0，主行动已用，不当回合再跳 |
| C在anchor5×5内但不同格，提交join | BATTLE_NOT_AT_ANCHOR；广播不提供远程权限 |
| C移动目标是anchor，中途被截停 | arrival_action取消，不获战况、参战资格或结算权 |
| C确实到达active anchor | 位置／rest／加入一起提交；普通加入不重置max_round |
| 两个active父战斗在同战場格合法合并 | 新ID、round=0、max_round=配置值4；保留父叙事与累计成本 |
| 三胜者中两人同意释放、一人反对 | 本次释放失败，不自动死亡；仍可继续交涉或有效处决 |
| 任一胜者明确execute_loser | 合法授权即执行死亡事务，其他胜者不享有否决 |
| B拒收A的印信 | 仍carried(A)，A行动已耗，B主行动未耗 |
| 提供方只返回答案／简短理由，没有公开CoT | 模型准入报错，不启用无CoTfallback |
| 第50句形成全员同意的通行stop | stop生效，不因达到上限额外开战 |
| 第50句没有合法通行结果 | 谈崩→battle；战后50句则进入全体胜者终止协议 |
