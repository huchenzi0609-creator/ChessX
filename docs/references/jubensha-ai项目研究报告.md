# JianWang97/jubensha-ai 项目研究报告

> **AI 剧本杀 · 全 AI 角色扮演的谋杀之谜推理游戏**
> 一份基于**逐文件源码阅读**的深度技术分析报告（非 README 复述）。
>
> - 仓库地址：<https://github.com/JianWang97/jubensha-ai>
> - 许可证：MIT
> - 报告基准：`main` 分支，最新提交 `2026-08-04`（"refactor: 剧本编辑流程改用 ReAct Agent"）
> - 分析方法：完整克隆仓库后阅读 `backend/`（125 个 Python 文件，其中 `src/` 下 102 个、约 19,000 行）与 `frontend/`（86 个 `.ts` + 75 个 `.tsx`，其中 `src/` 约 20,000 行），所有结论均可回溯到具体文件与行号。

---

## 目录

- [一、项目速览](#一项目速览)
- [二、这个项目到底在做什么](#二这个项目到底在做什么)
- [三、整体架构](#三整体架构)
- [四、核心设计之一：三层解耦的角色 Agent](#四核心设计之一三层解耦的角色-agent)
- [五、核心设计之二：游戏引擎与动态阶段计划](#五核心设计之二游戏引擎与动态阶段计划)
- [六、核心设计之三：发言权调度（ConversationFlowController）](#六核心设计之三发言权调度conversationflowcontroller)
- [七、核心设计之四：证据私有化与信息不对称](#七核心设计之四证据私有化与信息不对称)
- [八、剧本创作侧：两个 ReAct Agent](#八剧本创作侧两个-react-agent)
- [九、LLM 抽象层与推理模型的思考流](#九llm-抽象层与推理模型的思考流)
- [十、多模态层：TTS 与文生图](#十多模态层tts-与文生图)
- [十一、实时通信：WebSocket 契约与游戏主循环](#十一实时通信websocket-契约与游戏主循环)
- [十二、工程基建](#十二工程基建)
- [十三、已核实的缺陷与风险清单](#十三已核实的缺陷与风险清单)
- [十四、前端架构](#十四前端架构)
- [十五、技术栈总表](#十五技术栈总表)
- [十六、上手路径](#十六上手路径)
- [十七、客观评价：优势、局限与风险](#十七客观评价优势局限与风险)
- [十八、横向定位：它在学术与开源版图里的位置](#十八横向定位它在学术与开源版图里的位置)
- [附录：关键文件索引](#附录关键文件索引)

---

## 一、项目速览

| 维度 | 事实 |
| --- | --- |
| 项目定位 | AI 剧本杀（谋杀之谜推理游戏），**所有角色由 LLM 驱动**，真人只是旁观者 |
| 作者 | Wang Jian（`JianWang97`），单一主作者 |
| 创建时间 | 2025-07-09 |
| 最近提交 | 2026-08-04（持续开发约 13 个月，86 次提交） |
| Star / Fork | 约 137 ★ / 23 Fork（另 3 个 open issue，无 Release） |
| 主语言 | Python（约 953 KB）> TypeScript（约 711 KB） |
| 代码规模 | 后端约 19,000 行 Python，前端约 20,000 行 TS/TSX |
| 许可证 | MIT（允许商用、修改、再分发） |
| 部署形态 | Docker Compose 一键部署：前端 `:8009` + 后端 `:8010` + PostgreSQL 16 |
| 一句话概括 | 把剧本杀的**流程规程**编译成一套可执行的状态机与多智能体协议，再用多模态模型把这个流程"演"出来 |

**它不是一个"AI 陪玩工具"，而是一个"AI 剧组"。** 项目里没有"真人玩家操作界面"，只有"观看 + 编剧 + 回放"三种人机关系。这是理解全部设计取舍的前提。

---

## 二、这个项目到底在做什么

### 2.1 剧本杀这一游戏形态的硬约束

要理解项目的设计，先要理解剧本杀本身的机制约束。剧本杀（谋杀之谜）与普通对话式角色扮演（AI 语 C、Character.AI 式聊天）有三个结构性差异：

1. **有唯一正确答案**：凶手是谁是剧本预先设定的，游戏结束时必须揭晓，并且可以判定"玩家是否投对"。这意味着系统需要维护**真值层**（谁是真凶）与**信念层**（每个角色以为谁是凶手）的分离。
2. **信息不对称是玩法本体**：每个角色有私有剧本（背景、秘密、目标），证据搜到后只归发现者所有，可以选择公开或隐瞒。如果所有角色共享同一份上下文，游戏立刻退化为"一群 AI 互相念设定"。
3. **有固定流程与阶段性目标**：自我介绍 → 搜证 → 调查 → 讨论 → 投票 → 复盘，每个阶段的发言目的完全不同。

大多数"LLM 角色扮演"项目只解决问题 3 的表层（用一个 system prompt 描述人格），而 jubensha-ai 的工程价值恰恰在于它**同时正面处理了 1 和 2**。

### 2.2 项目的功能版图

项目实际包含三条独立的产品线，共用同一套剧本数据模型：

| 产品线 | 面向对象 | 实现位置 |
| --- | --- | --- |
| **剧本创作** | 编剧（真人）用自然语言生成/编辑剧本 | `script_generation_agent.py`（736 行）、`script_editing_agent.py`（760 行） |
| **剧本演绎** | 观众观看全 AI 自动对局 | `game_engine.py`（1075 行）+ `character_agent.py` 等 |
| **对局回放** | 观众复盘历史对局 | `game_history_service.py` + 前端 `game-history/` 路由 |

这种"创作 + 演绎 + 回放"的三段式，让项目比单纯的游戏 demo 更像一个**内容生产流水线**：AI 写剧本 → AI 演剧本 → 人看剧本。

---

## 三、整体架构

### 3.1 分层视图

```
┌──────────────────────────────────────────────────────────────┐
│  前端  Next.js 15 (Pages Router) + React 19 + Zustand        │
│  ├─ 剧本中心 / 剧本编辑 / 对局观看 / 对局回放 / 个人中心        │
│  └─ websocketStore：把 WS 事件流折叠成可渲染的游戏运行时状态    │
└───────────────┬──────────────────────────────┬───────────────┘
                │ REST（OpenAPI 生成客户端）     │ WebSocket（实时事件流）
┌───────────────▼──────────────────────────────▼───────────────┐
│  后端  FastAPI (Python 3.13)                                  │
│  ├─ api/routes/     12 个路由模块（auth/script/game/tts/...） │
│  ├─ core/                                                     │
│  │   ├─ websocket_server.py  1661 行：会话管理 + 消息路由      │
│  │   │                       + 游戏主循环 + 三种模式 Handler   │
│  │   ├─ game_engine.py       1075 行：游戏状态机与回合推进     │
│  │   ├─ conversation_flow_controller.py  703 行：发言权调度    │
│  │   ├─ evidence_manager.py / voting_manager.py               │
│  │   ├─ dependency_container.py  自研 DI 容器                 │
│  │   └─ auth_middleware.py       路径正则策略式 JWT 中间件     │
│  ├─ agents/                                                  │
│  │   ├─ character_agent.py + character_identity + memory      │
│  │   │   + phase_director + agent_tools   → 角色智能体         │
│  │   ├─ gm_agent.py              游戏主持人 / 动态阶段规划      │
│  │   └─ script_generation_agent / script_editing_agent        │
│  ├─ services/  LLM / TTS / 图像 / 剧本编辑 / 历史 / 存储        │
│  ├─ db/        SQLAlchemy ORM + Repository + Alembic 迁移      │
│  └─ schemas/   Pydantic 数据契约                              │
└───────────────┬──────────────────────────────────────────────┘
                │
     ┌──────────┴──────────┬───────────────┬──────────────┐
     ▼                     ▼               ▼              ▼
 PostgreSQL 16        MinIO / 本地目录   OpenAI 兼容 LLM  MiniMax / ComfyUI
 （持久化对局事件）    （音频与图片）      （多provider）   （TTS / 文生图）
```

### 3.2 三处最关键的设计决策

读这份代码时，我认为最有价值的三个决策是：

**决策一：游戏运行时状态放内存，持久化只写事件日志。**
`GameEngine` 的 `events` / `public_chat` / `game_state` 都是进程内的 Python 对象（`game_engine.py:47-77`），WebSocket 服务器用两个字典索引会话（`session_id → GameSession`、`websocket → session_id`）。只有**对局事件**通过 `GameEventRepository` 落库。这换来了极低的回合延迟（无需每回合读写数据库），代价是**后端重启即丢失进行中的对局**（详见第十七节）。

**决策二：把"角色是谁"和"现在该做什么"物理分离。**
这是整个 Agent 设计的骨架，下一节展开。

**决策三：所有 AI 行为都退化为"结构化契约 + 兜底降级"。**
无论是角色的行动（搜证/质询/投票）、剧本生成（分步落库）、还是剧本编辑（增量指令），项目都采用同一套模式：**优先用原生 tool calling；模型不支持则回退到 JSON 文本解析；解析失败则降级为纯文本发言**，绝不把异常抛进游戏主循环（`character_agent.py:173-244`）。这是让一个多模型兼容系统能稳定跑完一整局的关键工程妥协。

---

## 四、核心设计之一：三层解耦的角色 Agent

这是全项目最值得学习的部分。传统 LLM 角色扮演把人格、任务、记忆全部塞进一个不断增长的 prompt；jubensha-ai 明确拆成三层：

| 层 | 类 | 内容 | 生命周期 |
| --- | --- | --- | --- |
| **身份层** | `CharacterIdentity` | 姓名、背景、**秘密**、目标、性格、凶手/受害者标记 | 全程不变，作为 `system` message |
| **记忆层** | `CharacterMemory` | 工作记忆（公开对话滑窗）+ 私有事件日志 + 怀疑度表 + 已知证据 | 动态增长，注入 `user` message |
| **指令层** | `PhaseDirector` | 当前阶段的发言任务书 + 可用工具说明 | 无状态纯函数，每回合重新构建 |

代码见 `character_agent.py:154-221`、`character_identity.py:31-67`、`character_memory.py:37-146`、`phase_director.py:27-114`。

### 4.1 身份层：把"秘密"写进 system prompt

`CharacterIdentity.to_system_prompt()` 生成一段稳定的人格描述，其中包含一条非常关键的设计——**凶手提示**：

> 【身份提示】你是凶手。在游戏进行阶段，需要隐藏真相并自然地误导他人；你可以隐瞒或选择性公开自己搜到的证据，发言时也可以说谎；游戏结束后的复盘阶段才需要坦诚交代。
> —— `character_identity.py:75-76`

系统提示词还包含 8 条表达约束，例如"绝对不要说出角色的内心想法、计划或策略"、"严禁在发言中包含任何动作描述、表情描述、心理活动描述"（`character_identity.py:57-66`）。这条约束很实用：它解决了 LLM 扮演角色时最常见的失败模式——把内心独白和舞台指示一起输出，导致"凶手"在上文里自曝。

### 4.2 记忆层：故意不做向量库的双层记忆

`CharacterMemory` 是一个**刻意简化**的记忆系统（`character_memory.py:1-10` 的注释明确说明"针对剧本杀场景简化为无向量数据库的双层记忆"）：

- **工作记忆** `deque(maxlen=30)`：滚动保存最近 30 条公开对话，容量固定、O(1) 淘汰（`:47,52`）。
- **私有日志** `list`，上限 60 条，**溢出时按 `importance` 升序淘汰最低分条目**（`:65-74`）。搜到的证据以 `importance=0.9` 写入，自己的发言以 `0.6` 写入（`character_agent.py:219`、`:270-273`）——即"证据比废话更难忘"。
- **怀疑度表** `suspicion_map: {角色名 → 0.0~1.0}`，初始中性值 0.3，被点名时对提问者 +0.05（`character_memory.py:76-79`、`character_agent.py:258-260`）。
- **私有证据库** `known_evidence`，按 id/名称去重（`character_memory.py:81-90`）。

这套设计的有趣之处在于它把**记忆淘汰策略**也做成了游戏机制：容量硬上限保证了 prompt 长度恒定，`importance` 权重保证了关键线索不会被闲聊挤掉。

### 4.3 指令层：无状态的任务书构建器

`PhaseDirector` 是纯函数式构造器，按阶段返回不同的"任务书"（`phase_director.py:27-114`）。举两个例子看它的精细程度：

- **自我介绍阶段**的指令里包含"如果你是凶手，需要进行伪装性发言隐藏身份"；
- **讨论阶段**包含"适时'跳车'与'站队'，灵活调整怀疑对象"——这是剧本杀玩家的真实话术策略；
- **复盘阶段**明确写"此阶段游戏已结束，应该诚实分享真相，不再需要隐瞒"。

并且它对"行动"和"发言"做了明确的通道分离（`phase_director.py:117-151`）：

> 公开发言：直接作为回复正文输出，保持你的角色口吻；不要输出 JSON、代码围栏或任何格式标记。
> 游戏行动：通过调用系统提供的工具完成（不要只在文字里描述）。

**留言走正文、行动走工具**——这个划分让模型的自然语言能力和函数调用能力各司其职，避免了"让模型在 JSON 里写台词"这种既难解析又伤语感的做法。

### 4.4 工具集：角色的四种行动

`agent_tools.py` 用 OpenAI function-calling 格式定义了 4 个工具，并按阶段授权（`agent_tools.py:114-124`）：

| 工具 | 可用阶段 | 参数 | 作用 |
| --- | --- | --- | --- |
| `search_location` | 搜证 | `location` | 搜查一个地点，一次一处 |
| `ask_question` | 调查 / 讨论 | `target`, `question` | 质询指定角色 |
| `reveal_evidence` | 调查 / 讨论 | `evidence_names` | 公开自己掌握的证据（可隐瞒） |
| `cast_vote` | 投票 | `suspect` | 投出指认对象 |

工具描述本身就是**语义约束**，例如 `reveal_evidence` 的描述强调"evidence_names 必须来自你自己搜到/已知的证据名称，不能编造不存在的证据"（`agent_tools.py:73-77`）——用 prompt 层面的约束对抗幻觉。

`apply_tool_calls()` 把可能并发的多个工具调用合并进同一个 `AgentResponse` 结构（`say` / `action` / `reveal_evidence` / `vote` / `emotion`），未知工具名记警告后忽略，单个调用参数异常不影响其余调用（`agent_tools.py:127-174`）。

### 4.5 广播机制：只广播"说出口的话"

`CharacterAgentManager` 承担多智能体的通信总线（`character_agent_manager.py:60-107`）：

- `respond()` 让某角色发言后，**自动把 `response.say` 广播给其他所有角色**的 `observe()`；
- **`vote`、`emotion`、`action` 等内部字段绝不出圈**——只广播公开发言；
- `notify_evidence_found()` 实现了证据私有化：搜到的证据只写入发现者自己的记忆，其他角色只被告知"某人搜查了某地"这个动作事实（`character_agent_manager.py:100-106`）。

这几十行代码是"信息不对称"能真正成立的技术保障。

---

## 五、核心设计之二：游戏引擎与动态阶段计划

### 5.1 阶段枚举与硬编码流程

`GamePhaseEnum` 定义了 8 个阶段（`schemas/game_phase.py:8-17`）：`BACKGROUND → INTRODUCTION → EVIDENCE_COLLECTION → INVESTIGATION → DISCUSSION → VOTING → REVELATION → ENDED`。

但引擎**并不真的按这个顺序跑**。

### 5.2 GMAgent：让 LLM 决定这一局该怎么排流程

`GMAgent.create_game_plan()` 会根据剧本数据生成一份**阶段计划** `list[PhaseStep]`（`gm_agent.py:28-36, 133-147`）：

```python
@dataclass
class PhaseStep:
    phase_type: GamePhaseEnum
    name: str
    description: str
    max_turns: Optional[int] = None
    gm_instructions: str = ""     # 注入 PhaseDirector 的定制指令
    round_number: int = 1         # 同类型阶段的第几轮
```

- **优先用 LLM 规划**：把角色数、证据量、场景数、难度喂给模型，让它输出阶段序列（`gm_agent.py:199-268`）；
- **失败则回退规则计划**：`_build_default_plan()` 用纯规则构建（`gm_agent.py:42-116`）。规则里包含一个真实的剧本杀设计经验——**当证据 ≥ 8 条或角色 ≥ 5 人时，自动追加第二轮搜证与深入调查**，因为复杂剧本一轮搜证覆盖不完（`gm_agent.py:77-93`）。

同时，每次进入新阶段，GM 会生成一段旁白公告（`gm_agent.py:153-193`），并且可以为每个阶段写 `gm_instructions` 定制指令，注入到角色 Agent 的 user message 里（`game_engine.py:753-755`）。

**评价：** 这是"硬编码状态机"与"纯 LLM 自由发挥"之间的一个务实中间态。流程骨架由代码保证（不会跑飞），节奏参数（几轮搜证、每阶段多少轮）由 LLM 依剧本复杂度裁剪。`PhaseStep` 这个数据结构本身也把"计划"变成了可落库、可回放、可编辑的一等公民。

### 5.3 回合预算：按阶段设定发言轮数

`run_phase()` 为每个阶段设定 `max_turns`（`game_engine.py:739-751`）：

| 阶段 | 默认轮数 |
| --- | --- |
| 自我介绍 | 角色数 × 1 |
| 搜证 | 角色数 × 2 |
| 调查 | 角色数 × 4 |
| 讨论 | 角色数 × 5 |
| 投票 | 角色数 + 3 |

若 `PhaseStep.max_turns` 有值则优先使用计划中的值。讨论阶段给 5 倍于角色数的轮次，符合它作为"核心推理环节"的定位。

### 5.4 主循环与流式回显

`run_phase()` 的循环结构（`game_engine.py:764-935`）是：

```
while turn_count < max_turns:
    1. 取最近 10 条公开对话
    2. 交给 ConversationFlowController 选下一个发言者（失败则回退轮流）
    3. 防重复发言：同一角色连续发言 ≥2 次则强制切换
    4. agent.respond(phase, game_state) → AgentResponse
    5. 投票阶段：暂存结构化回应，供后续计票
    6. 处理证据公开 → 处理搜证行动（证据私有化）
    7. add_public_chat(...) → 立即触发 action_callback（流式推给前端）
    8. 判断阶段提前结束条件 → asyncio.sleep(1) 模拟对话间隔
```

关键点：`action_callback` 让**每一句发言完成即广播**，而不是等整个阶段跑完再一次性返回（`game_engine.py:906-911`）。这是"观感"层面的核心工程决策——用户看到的是一句句冒出来的对话，而不是长时间空屏后的批量填充。

### 5.5 投票与胜负判定

投票阶段收集每个角色的结构化 `AgentResponse.vote`，由 `VotingManager` 计票（`voting_manager.py:9-44`）：

```python
"success": most_voted == murderer
```

即"最高票是否等于真凶"。简洁，但注意它的口径是**相对多数**而非"全票通过"，平票时 `max()` 取字典序首个，属于实现层面的近似（见第十七节）。

---

## 六、核心设计之三：发言权调度（ConversationFlowController）

`ConversationFlowController`（703 行，`conversation_flow_controller.py`）解决一个容易被低估的问题：**下一句话该谁说？**

朴素做法是"按数组顺序轮流"，结果是对话像在开会点名——没有接话、没有追问、没有交锋。这个模块按阶段采用不同策略（`:30-62`）：

| 阶段 | 调度策略 | 依据 |
| --- | --- | --- |
| 自我介绍 | 固定顺序 | 每人一次，公平优先 |
| 搜证 | 规则优先、避免撞车 | 尽量让不同角色去不同地点（`:71-83`） |
| 调查 | **LLM 语义分析**最近对话 | 谁刚被提问 → 优先让被提问者回答；谁刚提问 → 让被问者接话（`:84-136`） |
| 讨论 | 规则 + LLM 混合 | 维护发言频率均衡，避免个别角色话痨（`:137-193`） |
| 投票 | 均衡轮询 | 保证每个人都有投票陈述机会（`:194-201`） |

它内部还有一个辅助工具链：`_extract_question_target()` 从发言文本中抽取"张三，你昨晚在哪里？"里的目标角色（`:412-418`），`_extract_accusation_target()` 抽取指控对象（`:455-461`）。这些是**中文对话的轻量启发式解析**，不依赖额外的模型调用。

**评价：** 这是把"对话的自然性"当成一等工程问题来做的少见例子。用 LLM 分析"谁该接话"而不是让 LLM 生成下一句台词，是一种成本可控的用法——调度决策短、token 少、可控性高。

---

## 七、核心设计之四：证据私有化与信息不对称

`EvidenceManager`（`evidence_manager.py`）只有 123 行，却承载了玩法核心：

```python
def process_evidence_search(action, character) -> (found_list, searched_location):
    location = self._extract_location(action)          # 最长优先匹配已知地点
    if not location: return [], None
    self.searched_locations[location] = character      # 记录谁搜了哪
    for evidence in self.evidence:                     # 该地点所有未发现证据
        if evidence["id"] in discovered_ids: continue
        if self._location_matches(evidence["location"], location):
            item["discoverer"] = character             # 标记发现者
            found.append(item)
```

四个设计要点：

1. **地点匹配用"最长优先 + 双向包含"**（`:106-123`）。`_extract_location` 在已知地点列表中找出现在行动文本里的最长匹配，避免"书房"误匹配到"书房保险柜"这类歧义；`_location_matches` 允许证据地点与搜查地点互相包含。
2. **一次搜查揭示该地点全部未发现证据**，而不是一条条抽（`:59-71`）。这更像真实的"搜证"：翻了一个地方，找到的东西都归你。
3. **零收获也要记录**：`searched_locations` 先标记后查找，所以"我搜了厨房，什么也没找到"是可表达的状态（`:57`）。
4. **多轮搜证支持**：`reset_for_new_round()` 只清空地点搜查状态，不清空已发现证据（`:73-75`），配合 GMAgent 追加的第二轮搜证。

公开渠道与私有渠道的分流在 `game_engine.py:857-893` 体现得很清楚：

- 公开广播："`{角色}搜查了「{地点}」，发现了线索（内容暂未公开）。`"
- 私有写入：`self.agents.notify_evidence_found(next_speaker, item)` → 只有发现者自己的 `known_evidence` 和私有日志被更新。

角色之后再自己决定是否调用 `reveal_evidence` 公开。**"隐瞒"是这个系统里真正存在的能力，而不是提示词里的口头承诺。**

---

## 八、剧本创作侧：两个 ReAct Agent

项目在 2026 年 7-8 月的重构中，把剧本生成与编辑都改成了 **ReAct Agent（Thought → Action → Observation 循环）**，替代了早期的"黑盒 HTTP 一次性生成"和"categorize → parse → execute"两段式管线。这是项目当前最"AI 原生"的部分。

### 8.1 剧本生成：把"写剧本"变成七次有校验的落库

`script_generation_agent.py`（736 行）把生成过程拆成一系列**工具即步骤**：

```
save_script_info → save_background_story → save_characters
→ save_locations → save_evidence → save_game_phases → finish
```

每个工具调用都在**自己的会话上下文**里执行（`with self._db_session_factory() as db`，退出即 commit、异常即 rollback），而不是最后一次性写入。设计要点：

- **幂等替换**：角色/场景/证据/阶段四类都是"整批删除再插入"（如 `save_characters` 先 `delete_characters_by_script` 再逐条 add），重复调用同一工具不会产生重复数据，天然可重试。
- **校验失败作为 observation 回喂 LLM 自我修正**：工具执行结果以 `【工具结果】{tool}: 成功/失败 — {observation}` 的形式追加为一条 user 消息（`script_generation_agent.py:405-408`），错误信息不会抛出中断，而是让模型看到原因后重新调用。例如角色校验会明确回喂"必须恰好 1 名凶手，当前 N 名"、证据会回喂"地点不在已创建场景中"。这是 ReAct 循环的标准用法，用在这里很恰当——LLM 写长剧本时字段缺失是常态。
- **迭代上限 15 轮**：`while self._iteration < self.max_iterations`，WebSocket 侧未覆盖该默认值。到上限仍未收尾时，必需步骤缺失则报 `error`，否则按完成处理。
- **取消是协作式的**：`cancel()` 只置一个 `asyncio.Event`，由循环在顶部与工具批内检查；不强制取消 asyncio 任务。
- **通过 WebSocket 分步流式透出**：前端能实时看到"思考 → 调用工具 → 观察结果"的每一步，而不是盯着转圈。事件类型为 `script_generation_event`（`step_start` / `thought` / `action` / `observation` / `step_end` / `done` / `error` / `cancelled`），event 负载含 `type/step/step_name/step_index/iteration/content/kind/data/completed_steps/timestamp`；完成后追加 `script_data_update`。
- **断线回放**：客户端可发 `get_script_generation_state` 取回当前进度（服务端把事件缓存在会话内存里，上限 500 条；**不落库**，进程重启即丢）。
- **兜底**：必需步骤为 `script_info / background_story / characters / locations / evidence` 五项，缺失即拒绝 `finish`；但 **`game_phases` 不在必需清单里**——若模型没生成阶段计划，系统会补上默认六阶段（背景介绍 / 自我介绍 / 搜证 / 自由讨论 / 投票 / 真相揭晓）并落库，保证剧本一定能开局。

  这里有一个值得注意的细节：补的六阶段里**没有 INVESTIGATION（调查）阶段**，与 `GamePhaseEnum` 定义的 7 个游戏阶段不完全等价。也就是说"AI 生成剧本 + 兜底"路径产出的对局节奏，会比手写剧本少一个调查环节。

**为什么这个设计值得称赞：** 剧本杀剧本是一个**结构化的多表关联数据**（剧本信息 + 背景故事 + N 个角色 + M 个场景 + K 条证据 + 阶段计划），让 LLM 一次生成一个巨大 JSON 的失败率极高。拆成 7 个带 schema 校验的步骤后，每一层的失败都是局部的、可自我修复的、对用户可见的。这是"用工程结构降低 LLM 输出方差"的教科书式做法。

### 8.2 剧本编辑：对话式增量修改

`script_editing_agent.py`（760 行）+ `script_editor_service.py`（994 行）支持"添加一个善良的角色"、"修改凶手的动机"这类自然语言指令。它比生成更复杂，因为要在**已有剧本上做增量修改并保持逻辑一致**：

- **先 plan 再执行**：Agent 先输出编辑计划，再逐个调用工具；迭代上限 12 轮，失败时会回喂最后一条失败原因要求重试；
- **工具集覆盖全字段**：角色/证据/场景的 add/update/delete 共 9 个 + `update_script_info` + `update_background_story` + `game_phases` 四工具 + `bind_character_voice`（自动为角色匹配 TTS 音色）+ `plan` / `finish`；
- **校验复用服务层**：工具内部构造 `EditInstruction{action, target, content, description}`，复用 `ScriptEditorService.execute_instruction` 的校验与增量落库逻辑，避免两套规则；
- **事务由外层统一提交**：编辑模式启动时创建**长寿命 db session**，仓库的增量方法**只 `flush` 不 `commit`**，由 WebSocket handler 在 `success_count > 0` 时统一 `commit()`——保证一批编辑对读方要么整体可见、要么整体不可见。同一剧本还配了一把 `asyncio.Lock`（按 `script_id`）做串行化，避免两人同时编辑同一剧本互相覆盖；
- **`plan_only` 干跑（本项目的技术亮点）**：HTTP 端点 `/api/script-editor/parse-instruction` 传 `plan_only=True`，工具在 SAVEPOINT 内**真实执行完整校验**后无条件回滚：

```python
if self.plan_only and name not in ("plan",):
    nested = self.db.begin_nested()      # SAVEPOINT
    try:
        ok, observation = await self._dispatch_tool(name, args)
    finally:
        nested.rollback()                # 无论成败都回滚
    self.db.expire_all()                 # 清掉会话缓存，避免读到幽灵对象
    return ok, observation
```

  这是"想知道 AI 会怎么改，但先别真改"的正确实现方式：校验逻辑走的是与真实执行**完全相同**的代码路径，而不是另写一套 dry-run 判断。

- **校验强度的不对称**：角色新增的校验最严（必填 6 项 + `background ≥ 50` 字 / `secret ≥ 20` 字 / `objective ≥ 15` 字 / 性格特征 ≥ 2 个 / 性别白名单）；而**证据与场景的新增几乎没有校验**（字段给默认值），update/delete 也只检查名称是否存在。也就是说，编辑质量在很大程度上仍依赖提示词约束。

### 8.3 增量执行与进度回显

编辑过程通过 `script_edit_event` 事件流回显（`{type, step, step_name, content, kind, data, timestamp}`），`step` 取值即工具域（plan/characters/evidence/locations/script_info/background_story/game_phases/voice，中文名映射在 `EDIT_STEP_NAME`），最终 `edit_result` 按 `result.tool_results` 逐条广播。

前端对应 `EditProcessTimeline.tsx` + `scriptGenerationStore.ts`，把 Agent 的执行轨迹渲染成时间线。

---

## 九、LLM 抽象层与推理模型的思考流

`llm_service.py`（413 行）是让"兼容所有 OpenAI API 兼容模型"这句话落地的关键。

### 9.1 双实现 + 工厂

```python
class BaseLLMService(ABC):        # 抽象接口
    async def chat_completion(...) -> LLMResponse
    async def chat_completion_stream(...) -> AsyncGenerator[str, None]
    async def chat_completion_stream_chunks(...) -> AsyncGenerator[StreamChunk, None]

class OpenAILLMService(BaseLLMService)     # openai SDK 直连，支持原生 tool calling
class LangChainLLMService(BaseLLMService)  # LangChain 路径（兼容旧代码）

class LLMService:                 # 工厂
    @staticmethod create_service(provider, **config)
    @staticmethod from_config(config)
```

外加 `_get_or_create_llm_service()` 的全局单例 + 模块级 `__getattr__` 懒加载（`:366-412`）。所有角色 Agent **共享同一个 LLMService 实例**（`character_agent_manager.py:33-36`），避免每个角色各持连接池。

### 9.2 推理模型思考过程的分离

这是当前 LLM 应用的一个真实痛点，项目处理得很细致（`llm_service.py:23-63, 182-247`）：

- `LLMResponse.reasoning_content` 单独承载 DeepSeek / Kimi 等推理模型的思考字段；
- `StreamChunk(type="reasoning"|"content", text=...)` 把流式输出切成两类片段；
- `chat_completion_stream_chunks()` 同时处理**两种思考来源**：
  1. `delta.reasoning_content` 字段（原生推理模型）；
  2. `delta.content` 里**内联的 `<think>...</think>` 标签**（把思考混在正文里的模型）。

第二点的实现细节值得注意——它用了一个 `tag_buffer` 做**跨 chunk 的标签边界处理**：维护 `in_think_tag` 状态，并保留末尾 7-8 个字符不立即输出，因为 `<think>` / `</think>` 这个标签本身可能被切成两个 chunk 到达（`llm_service.py:210-247`）。这是流式解析的经典坑，代码里明确处理了。

`split_think_tags()` 则是非流式路径的对应实现，用正则 `re.compile(r"<think>(.*?)</think>", re.DOTALL)` 拆分。

### 9.3 工具调用的解析与容错

`_parse_openai_tool_calls()` 把 OpenAI 响应的 `message.tool_calls` 转成 `ToolCall(name, arguments)`；**单个调用的 arguments JSON 解析失败时降级为 `{"_raw": 原始字符串}`，不中断其余调用**（`llm_service.py:65-84`）。模型偶尔吐出坏 JSON 时，系统不会整轮崩掉。

### 9.4 两条路径的能力差异（容易踩坑）

这套抽象并非"两条路径完全等价"，实际存在能力落差：

| 能力 | OpenAI SDK 路径 | LangChain 路径 |
| --- | --- | --- |
| 原生 tool calling | 支持（`tools` / `tool_choice` 随 kwargs 直传） | 依赖 `llm.bind_tools()`；**绑定失败只记告警并回退普通调用** |
| `reasoning_content` | 支持（流式与非流式都解析） | 不支持，字段为 `None` |
| 流式思考/正文分离 | 覆写了 `chat_completion_stream_chunks` | **未覆写**，只能用默认实现（全部视为 content），靠 Agent 端的 `split_think_tags` 兜底 |
| token usage | 返回 | 不返回 |

**结论：想要完整的工具调用与思考流体验，应走 OpenAI SDK 路径**（`LLM_PROVIDER=openai`，即默认路径）。LangChain 路径更适合作为旧代码兼容层，其工具调用失败最终由 Agent 的 JSON 降级逻辑兜住，功能可用但会损失"行动与发言通道分离"的优雅性。

### 9.5 超时与重试：分层放置

`llm_service.py` **没有任何显式的 timeout、重试或退避配置**，完全依赖 SDK 默认行为；`LLMConfig` 里也没有 timeout 字段（`core/config.py:10-18`）。重试逻辑被放在**业务层**，而且是分场景定制的：

- 旧版 HTTP 生成服务：最多 3 次重试，每次 `temperature` +0.1（降温改采样），并按错误关键词往提示词里追加修补说明（`script_generation_service.py:139-182`）；
- 剧本编辑服务：有独立的重试与提示修补路径（`script_editor_service.py:772-927`）。

这是一种取舍：把重试放在业务层可以针对不同任务定制策略（生成需要"换个说法再写"，编辑需要"按失败原因修正"），代价是 LLM 抽象层本身不提供统一的可靠性保证，新增调用点时容易漏掉重试。

---

## 十、多模态层：TTS 与文生图

### 10.1 TTS：双 provider + 五级音色回落

`GameTTSManager`（`game_tts_manager.py`）在每次 AI 发言后同步生成语音，然后**把 `tts_url` 合并进 `ai_action` 消息一次性广播**（`websocket_server.py:772-806`）——前端收到一条消息就同时拿到文字和音频，不需要自己对齐两条流。

音色选择是一个**五级回退链**（`game_tts_manager.py:57-97`）：

1. 角色配置的 `voice_id`（剧本编辑时由 `bind_character_voice` 工具写入）
2. 角色名直接映射（`侦探`/`管家`/`女主人`/`秘书`）
3. 性别 + 年龄段组合（`elder_male` / `elder_female` / …）
4. 姓名关键词启发式（"先生/叔/爷" → 男声；"小姐/姐/妹" → 女声）
5. 默认音色

这种"越具体越优先"的链式设计，保证了无论剧本生成得多随意，每个角色总能分到一个还算合适的嗓子。

这里有个值得注意的实现落差：剧本编辑 Agent 的 `bind_character_voice` 工具（写入 `voice_id`）走的是**另一套三级匹配**——`voice_id` 精确 → 音色名称双向包含 → 性别关键词（男/女/男性/女性 → male/female）。**角色的 age、profession、personality 完全不参与匹配**，最终音色是否贴脸，取决于 LLM 自己往 `voice_query` 参数里填了什么描述。也就是说，第一级"角色配置的 voice_id"的质量，实际上被托付给了剧本编辑阶段的模型表现。

TTS 支持两个 provider：

| Provider | 形态 | 实现 |
| --- | --- | --- |
| MiniMax | 云端 API，多语言多音色 | `minimax_service.py`（465 行） |
| CosyVoice 2.0 | 本地部署的中文语音合成 | `cosyvoice_service.py`（200 行） |

`tts_event_service.py` 还维护了 TTS 的**状态机**：`PENDING → COMPLETED / FAILED / SKIPPED`，并且有 `_clean_text_for_tts()` 做文本清洗（`tts_event_service.py:144-167`），以及 `_skip_tts()`——空文本或纯符号直接跳过，不浪费 API 调用。

### 10.2 文生图：抽象层 + 两个后端

`image_generation_service.py` 定义 `ImageGenerationService` 抽象基类，提供两个实现：

- **ComfyUI**（`comfyui_service.py`，364 行）：本地部署的 Stable Diffusion 工作流平台。它通过 `workflow_api.json` 加载节点图，用 websocket 订阅生成进度，可配置模型（默认 `AWPainting_v1.5`）、采样器（`euler`）、scheduler、步数、CFG、尺寸。
- **MiniMax**：云端文生图 API。

用途覆盖角色头像、证据配图、场景图三类，且后端还提供"生成提示词"端点，让 LLM 先把角色设定转成绘图 prompt（`character_routes.py:164`、`evidence_routes.py:111`、`location_routes.py:156`）。

---

## 十一、实时通信：WebSocket 契约与游戏主循环

### 11.1 消息契约

单一端点 `GET /api/ws?token=...&script_id=...`，所有消息按 `type` 字段分发。服务端的 `MessageHandler` 抽象类 + 处理器注册表（`websocket_server.py:168-175`），是标准的**命令模式**实现。

**客户端 → 服务端**（部分）：

| type | 作用 |
| --- | --- |
| `start_game` / `next_phase` / `reset_game` | 游戏控制 |
| `get_game_state` / `get_public_chat` / `fetch_history` | 状态拉取与增量同步 |
| `start_script_generation` / `cancel_script_generation` / `get_script_generation_state` | 剧本生成 |
| `start_script_editing` / `stop_script_editing` / `edit_instruction` | 剧本编辑 |
| `generate_ai_suggestion` | AI 编辑建议 |
| `get_tts_history` / `set_background_mode` | TTS 与背景模式 |

**服务端 → 客户端**（实测 grep 出的完整集合）：

```
ai_action            ai_suggestion        ai_suggestion_generating
background_mode_response                  edit_result
error                game_ended           game_reset
game_result          game_started         game_state
game_state_update    history              instruction_completed
instruction_processing                    phase_changed
public_chat          public_chat_update   script_data_update
script_edit_event    script_editing_started / stopped
script_generation_event                   script_generation_state
session_connected    tts_history          voting_complete
```

### 11.2 游戏主循环

`GameModeHandler.game_loop()`（`websocket_server.py:724-960`）是一个后台 asyncio 任务：

```
while is_game_running and phase != ENDED:
    运行 run_phase(action_callback=流式广播+TTS)
    广播 game_state_update
    广播 public_chat_update
    若是投票阶段 → 计票 + 广播 voting_complete
    按阶段等待 5~10 秒（拆成 1 秒粒度轮询，以便快速响应停止）
    next_phase() → 广播 phase_changed
若是揭晓阶段 → 广播 game_result → next_phase() → 广播 game_ended
```

**工程细节：** 那段"把 30 秒等待拆成 30 次 1 秒等待"的写法（`:882-888`）看着笨，但很实用——它是为了让用户在等待期间点"停止"能被及时响应，而不是卡住 30 秒。长任务的**可中断性**往往就靠这类朴素处理。

### 11.3 断线增量同步

`GameEngine.get_history(from_event_id, limit)` 提供了基于自增事件 ID 的增量拉取（`game_engine.py:97-127`）：

```python
events          # id > from_event_id 的事件
public_chat     # 当前保留的全部公开聊天
truncated       # 是否因 limit 被截断
newest_event_id # 供前端记录断点
earliest_event_id # 用于判断服务端是否已发生裁剪
```

同时返回"最新 ID"和"仍保留的最早 ID"，前端据此能判断自己是否落后太多、是否需要全量重拉。事件本身有 `max_events = 5000` 上限（`game_engine.py:49-50`），防止长对局内存无限增长。

### 11.4 三种运行模式共用一个端点

同一个 WebSocket 连接承载三种模式，由 `GameModeHandler` / `ScriptGenerationModeHandler` / `EditModeHandler` 三个 Handler 类分担（`websocket_server.py:374-1290`）：

- **游戏模式**：`GameSession` 持有 `GameEngine` + TTS 管理器；
- **生成模式**：以 `script_id` 为键的生成任务，带取消与状态查询；
- **编辑模式**：以 `script_id` 为键的**编辑锁** `asyncio.Lock`（`:1332-1336`），防止两个用户同时编辑同一剧本造成写冲突。

用一个连接承载多模式，好处是切换页面不用重连、状态天然共享；代价是 `websocket_server.py` 膨胀到 1661 行，成为后端最大的单文件。

---

## 十二、工程基建

### 12.1 自研依赖注入容器

`dependency_container.py`（435 行）+ `container_integration.py`（142 行）实现了一个轻量 DI 容器，支持三种生命周期（`AGENTS.md:71-79`）：

- **singleton**：`DatabaseManager`、`LLMService`
- **scoped**：per-request，仓库与多数服务，**自动 commit / rollback**
- **transient**：每次解析新实例

在 FastAPI 里手写 DI 容器并不常见（多数项目直接用 `Depends`）。这里的好处是**事务边界与服务生命周期解耦**：scoped 服务的"请求结束自动提交"让业务代码不必到处写 `db.commit()`。项目还保留了 `DEPENDENCY_INJECTION_MIGRATION.md` 记录迁移过程，工程习惯不错。

### 12.2 认证：路径正则策略式中间件

`auth_middleware.py`（246 行）用**路径正则 → 策略**的映射统一鉴权（`AGENTS.md:81-88`）：

| 策略 | 含义 |
| --- | --- |
| `NONE` | 无需认证（登录、注册、健康检查） |
| `OPTIONAL` | 有 token 就解析，没有也放行 |
| `REQUIRED` | 必须登录 |
| `ADMIN` | 需管理权限 |

中间件注入 `request.state.current_user` 与 `request.state.is_authenticated`；token 校验逻辑本身在 `AuthService.verify_token` / `get_user_from_token`，**WebSocket 端点复用同一套校验**——认证只有一条代码路径，不会出现"HTTP 校验了、WS 忘了校验"的漏洞。

另外项目支持**匿名访客模式**（`ALLOW_ANONYMOUS_ACCESS=true`），启动时自动创建 guest 账户（`server.py:47-57`），降低了试玩门槛。

### 12.3 数据层

- **ORM**：SQLAlchemy 2.0（同步 declarative），9 张核心表：`scripts` / `characters` / `evidence` / `locations` / `background_stories` / `game_phases` / `images` / `game_sessions` / `game_events` / `users`；
- **Repository 模式**：`src/db/repositories/` 下 9 个仓库类，业务层不直接写查询；
- **迁移**：Alembic，3 个迁移版本；注意 `init_database()` 启动时还会 `create_tables()` 自动建表（`AGENTS.md:95`）——迁移与自动建表并存，这是开发便利性与生产严谨性之间一个不彻底的折中（见第十七节）；
- **Pydantic 双轨**：`schemas/` 下每个业务实体都有 Pydantic 模型，并且通过 `get_db_model()` 类方法关联到对应 ORM 模型（如 `script_character.py:28-31`），在"API 契约"与"持久化模型"之间做显式桥接。

### 12.4 存储抽象

`StorageManager`（`storage.py`，567 行）用一个 `FILE_STORAGE` 环境变量在两种后端间切换：

| 模式 | 说明 |
| --- | --- |
| `dir` | 本地目录（Docker volume 持久化），默认 |
| `minio` | MinIO 对象存储 |

接口覆盖通用上传、分类上传（封面/头像/证据/场景）、TTS 音频上传与读取、文件列表、存储统计。分类化 API（`upload_cover_image` / `upload_avatar_image` / `upload_evidence_image` / `upload_tts_audio`）比裸 `upload_file` 更贴合业务。

### 12.5 部署

**生产一键部署**（`docker-compose.yml`）：

- `db`：`postgres:16-alpine`，带 `pg_isready` healthcheck，backend `depends_on: service_healthy`；
- `backend`：两阶段 Dockerfile，用 `uv sync --frozen --no-dev` 建虚拟环境后只拷贝 `.venv`（不带 uv/pip 缓存），镜像更小；
- `frontend`：三阶段构建（deps → builder → runner），运行阶段只保留 **Next.js standalone 产物**；
- 卷：`pgdata` / `backend_static` / `backend_data`；
- **注意**：`NEXT_PUBLIC_API_URL` 是**构建期注入**的（浏览器直连后端），换服务器地址必须重新构建前端镜像——这是 Next.js 环境变量语义导致的硬约束，项目在 README 里明确提示了。

**开发混合模式**（`docker-compose.dev.yml`）：只用容器跑 PostgreSQL + MinIO，前后端跑在宿主机上（后端 `:8010`，前端 `:3001`）。这个"依赖容器化、应用本地化"的模式对 Python/Node 开发者体验友好。

### 12.6 测试

`backend/tests/` 下 16 个测试文件，含针对关键子系统的专项测试：`test_character_agent.py`、`test_llm_service_tools.py`、`test_script_generation_agent.py`、`test_script_editing_agent.py`、`test_script_editor_incremental.py`、`test_websocket_editing.py`。

策略上以 mock 为主：`conftest.py` 提供 `test_client`、`mock_db_session`（autouse，全局 patch `db_manager`）、`mock_current_user`，并有 `factories.py` 提供可复用的模型工厂；测试按 `api` / `unit` / `integration` / `slow` 四种 marker 分类。前端 devDependencies 里有 Playwright，但**没有看到成体系的 E2E 测试套件**。

---

## 十三、已核实的缺陷与风险清单

以下问题均在源码中逐一核对过，按"影响面 × 严重度"排序。这份清单不是为了否定项目，而是给准备基于它二次开发的人一份**开工前必读**。

### A. 会导致功能不可用或构建失败

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| A1 | **生成的前端客户端依赖未声明的包** | `frontend/src/client/core/request.ts:5-7` | 顶层 `import axios from 'axios'` 与 `import FormData from 'form-data'`，但 `package.json` **未声明这两个依赖**——目前能构建纯属 npm hoisting 巧合（二者仅作为 devDependency 的传递依赖存在）。执行 `npm ci --omit=dev` 或移除 `@openapitools/openapi-generator-cli` 就会直接 break |
| A2 | **"续玩"链路实际不通** | `pages/game-history/[sessionId]/resume.tsx:37-41` | 调用 `resume(sessionId)` 后只把返回的 `websocket_url` 显示出来；"进入游戏"按钮跳 `/game` 时不带 `script_id`/`session_id`，游戏页因此没有 selectedScript，点开始会落到"请先选择一个剧本！"分支。**这条路径需要前后端一起改才能用** |
| A3 | **后端返回的恢复地址写死且端口错误** | `backend/src/services/game_history_service.py:172` | 硬编码 `ws://localhost:8000/ws`，而实际后端端口是 8010，且远程部署下 localhost 不可达 |
| A4 | 退出日志跳转到不存在的路由 | `frontend/src/components/GameLog.tsx:38` | `router.push('/scripts')`——该路由不存在，404 |
| A5 | 首页页脚 4 个死链 | `pages/index.tsx:151-162` | `/about` `/privacy` `/terms` `/contact` 均无对应页面 |
| A6 | **投票与结算结果前端无落点** | `stores/websocketStore.ts:198-202` | `voting_complete` 与 `game_result` 两个分支是**空 case**，服务端推来的投票统计与胜负结果在前端被直接忽略；`useGameState` 里定义了 `GameResult` 类型但从未使用 |
| A7 | 证据类型枚举不一致（潜在 500） | `backend/src/services/script_generation_service.py:417` | 用 `EvidenceType(ev_data.get("evidence_type", "physical"))` 构造枚举，但枚举值全是大写、且同文件提示词要求模型输出小写——运行时会抛 `ValueError` 并冒泡成 HTTP 500。这是旧版 HTTP 生成路径的问题（新 ReAct 路径不走这里），**未实测** |

### B. 数据一致性与状态管理

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| B1 | **编辑失败时脏数据可能被后续提交带出** | `backend/src/core/websocket_server.py:1178-1185` | 长寿命编辑会话在 `success_count > 0` 时统一 commit；当 `success_count == 0` 时**既不 commit 也不 rollback**，会话里已 flush 的脏数据会一直留到下一次指令提交时被一并写入 |
| B2 | 编辑计划模式下工具互不可见 | `script_editing_agent.py:573-581` | `plan_only` 下每次工具调用后回滚到 SAVEPOINT，因此后续工具看不到前一个工具新建的实体（例如"先建场景、再往该场景放证据"在计划模式下会失败）。这是干跑语义的固有代价，但会影响计划质量 |
| B3 | 重名实体导致寻址错位 | `script_editor_service.py:278-285, 358-368` | 新增角色/证据/场景**不做同名检查**；而 update/delete 是按名称查找并只命中第一条。一旦产生重名，后续编辑会持续改错对象 |
| B4 | 跨实体不变量没有代码级校验 | `script_editor_service.py:236-237, 323` | `is_murderer` / `is_victim` 仅作字段透传；"恰好 1 名凶手 + 1 名受害者"只写在编辑 Agent 的 SYSTEM_PROMPT 里，代码不校验。AI 编辑后完全可能产出 0 个或 2 个凶手 |
| B5 | 生成事件回放不落库 | `backend/src/core/websocket_server.py:377, 437-439` | `generation_events` 是会话内存里的列表（上限 500 条），进程重启或会话回收后 `get_script_generation_state` 无法再回放 |
| B6 | 前端两套游戏状态并行 | `hooks/useGameState.ts:44-55` vs `stores/websocketStore.ts:10-21` | 同一份对局数据被维护两次，靠 `processedEventsCountRef` 游标对齐；三套历史列表读取路径并存 |
| B7 | `authStore.register` 成功后未置登录态 | `stores/authStore.ts:93` | 注册成功后 `isAuthenticated` 仍为 `false`，用户需再登录一次 |
| B8 | `history` 去重键过粗 | `stores/websocketStore.ts:254+` | 用 `character:content` 字符串做去重键，角色重复说同一句话时会被误删 |

### C. 健壮性与安全

| # | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| C1 | **JWT 放在 WebSocket query string** | `stores/websocketStore.ts:370-373` | `?token=<jwt>` 会出现在服务器访问日志、代理日志与 Referer 中。生产环境应改用子协议头或首帧认证 |
| C2 | **无心跳、无指数退避、无重连上限** | `stores/websocketStore.ts:402-413` | `onclose` 后固定 3 秒原样重连，无限重试且不做退避。后端重启时所有客户端会以固定频率持续冲击 |
| C3 | 后端长连接无心跳检测 | `backend/src/core/websocket_server.py` | 未发现 ping/pong 或空闲超时机制（存在 `cleanup_inactive_sessions`，但依赖的是会话活跃度而非连接活性） |
| C4 | 前端缺少必要的缺失数据保护 | `stores/websocketStore.ts:155` | `ai_action` 分支里 `if (!state.gameState) return state;`——在 `session_connected` / `game_state` 到达之前推送的事件会被**静默丢弃**，没有缓冲队列 |
| C5 | reducer 内产生副作用 | `stores/websocketStore.ts:151-189` | TTS 入队与 `startQueueProcessor()` 写在 `set(state => …)` 的更新函数内部；项目开启了 `reactStrictMode`，开发模式下可能重复入队 |
| C6 | 硬编码第三方兜底域名 | `stores/scriptsStore.ts:118`、`components/ScriptCard.tsx:60`、`ScriptDetailDrawer.tsx:86` | 封面缺失时会把请求发往 `https://trae-api-sg.mchost.guru/...` 这个外部实验域名。**建议自行替换掉** |
| C7 | 前端类型检查被削弱 | `frontend/tsconfig.json` | `strict: true` 同时 `noImplicitAny: false`；非生成代码里有约 45 处显式 `any`（`websocketStore`、`ChatEditor`、`gameHistoryService`、`profile/game-history` 是重灾区），两个文件整文件关闭了 `no-explicit-any` |
| C8 | ESLint 实际上不生效 | `next.config.ts:35-37` | `eslint.ignoreDuringBuilds: true`；且 `eslint-config-next@15.4.2` 与 `next@15.3.5` 版本不匹配，仓库只有 `.eslintrc.json` 而 ESLint 9 默认要求 flat config |
| C9 | 依赖声明不一致 | `package.json` | `tw-animate-css`、`playwright` 是未被使用的 devDependency；`axios`/`form-data` 反而缺失（见 A1） |
| C10 | 回放链路遇坏 URL 静默卡死 | `pages/game-history/[sessionId]/replay.tsx:191-194` | 依赖 `onended` 串联音频，而坏 URL 不触发 `onended`；错误处理只有 `console.error`，用户侧无感知、无法自愈 |
| C11 | 全仓 `<img>` 绕过 Next 图片优化 | 8 处 | `next.config.ts:45-58` 虽配置了 `images.remotePatterns`，但业务组件有意改用原生 `<img>`（并留有注释说明是规避优化器风险）；同时正则允许任意 `https` 域名 |

### D. 死代码与残留（可安全清理，但会干扰阅读）

`stores/scriptsStore.ts`（418 行，全项目零引用）、7 个零引用组件（`StatsGrid`、`TopBar`、`Toolbar`、`CharacterList`、`MobileOptimized`、`EmptyState`、`ScriptSelection`）、`services/scriptService.ts` 里保留的旧两步式生成接口（已与后端 ReAct 流程不符）、`tailwind.config.ts`（v4 下失效）、`pages/test-select.tsx`（调试页残留在生产路由）、`_document.tsx:5` 的 `lang="en"`（内容实为中文）。

---

## 十四、前端架构

### 13.1 技术选型

| 层 | 选型 |
| --- | --- |
| 框架 | Next.js 15.3.5（**Pages Router**，无 `app/` 目录） |
| UI 库 | React 19 |
| 语言 | TypeScript 5（`strict: true`，但 `noImplicitAny: false`） |
| 样式 | Tailwind CSS 4（`@tailwindcss/postcss`） |
| 组件基座 | Radix UI + shadcn/ui（`new-york` 风格，26 个 `components/ui/` 组件） |
| 状态管理 | Zustand 5（`persist` 持久化） |
| 图标 / 通知 | lucide-react / sonner |
| API 客户端 | `openapi-typescript-codegen --client axios` 从后端 `/openapi.json` **自动生成**（58 个 model、2 个 service） |

### 13.2 状态管理：7 个 Zustand store

| Store | 职责 | 备注 |
| --- | --- | --- |
| `authStore` | 认证状态与 `user` | 持久化 user；token 另存 localStorage `access_token` |
| `configStore` | API 基址 / 超时 / 重试 / 环境信息 | **只持久化 timeout 与 retries** |
| `websocketStore` | **连接 + 游戏运行时状态** | 前端的心脏 |
| `scriptGenerationStore` | 生成/编辑进度时间线 | 按 `step_start`/`done`/`error`/`cancelled` 派生状态，不持久化 |
| `ttsStore` | 语音开关、播放队列、当前播报角色 | 见 13.5 |
| `gameHistoryStore` | 历史列表、详情、事件分页 | 回放/详情页使用 |
| `scriptsStore` | 剧本列表与筛选 | **全项目零引用的死代码** |

其中 `websocketStore.ts`（508 行）是核心：它把服务端推来的 20 多种事件 `type` 折叠成可渲染的界面状态，并通过 `window.dispatchEvent` 自建了一套 **CustomEvent 总线**（`script_edit_result`、`history_chat_received`、`session_connected`）供业务组件订阅。**前端不做业务逻辑，只做事件流的投影**——这与后端"事件驱动"的设计是对齐的。

但这里也暴露出这套代码最大的结构性问题：**存在两套并行的游戏状态**。`hooks/useGameState.ts` 自己维护了一份 `gameLog / currentPhase / votes / gameResult / isGameStarted`，与 `websocketStore.gameState.events` 内容重复，靠一个 `processedEventsCountRef` 游标做增量对齐。加上 `profile/game-history.tsx` 直接调 `authService.getUserGameHistory` 而绕过 `gameHistoryStore`，同一份数据在前端最多有三条读取路径。这是典型的"增量重构留下的双轨制"。

### 13.3 API 客户端生成

`npm run generate-api` 从 `http://localhost:8010/openapi.json` 生成 `src/client/`（约定 DO NOT edit manually），手写服务在 `src/services/` 里包一层。这形成了**后端 schema → 前端类型**的单向依赖，避免手写接口类型漂移，是 FastAPI + TypeScript 组合的最佳实践之一。

不过有两个瑕疵：

1. **生成物被手工改过**：`client/core/OpenAPI.ts` 顶部被插入了 `import { config } from '@/stores/configStore'` 和一段未被使用的 `getToken`，违反了自己定下的"生成物禁改"约定；下次重新生成会被覆盖。
2. **baseUrl 只在模块加载时求值**：`OpenAPI.BASE` 与 `authService` / `scriptService` / `gameHistoryService` 都在构造或模块期读取 `config.api.baseUrl`，因此运行期调用 `updateApiConfig()` **不会生效**，`useConfigStore` 在所有页面中零引用。配置项存在，但动态改配置的能力实际上没有接通。

### 13.4 页面与设计语言

路由覆盖 13 条：`/`、`/auth/login`、`/auth/register`、`/script-center`、`/script-manager/create`、`/script-manager/edit/[id]`、`/game`、`/game-history/[sessionId]`（含 `/replay`、`/resume`）、`/profile`（含 `/game-history`、`/change-password`）。

设计上项目在 2026-08-01 做过一次全站重构，主题是**"探案案卷（dossier）"风格**，而且落地得相当彻底——Tailwind v4 的 `@theme` 里定义了一整套语义化色板与字体 token（`globals.css:48-65`）：

| Token | 值 | 语义 |
| --- | --- | --- |
| `ink` | `#0E1116` | 底色（墨） |
| `panel` / `raised` | `#151A24` / `#1D2532` | 面板层级 |
| `hairline` / `line` | `#1C2430` / `#262F3D` | 1px 分隔线 |
| `paper` / `mist` / `faint` | `#E8E4DA` / `#9AA3AE` / `#5C6673` | 纸张/次要/弱化文字 |
| `brass` / `brass-dim` | `#C9A15F` / `#8A6B3C` | 黄铜色：焦点、印章、强调 |
| `thread` / `thread-dim` | `#B3473F` / `#7A2E28` | 红线色：装配进度、警示 |

配套三个字体 token：`--font-dossier`（宋体衬线，标题）、`--font-data`（等宽，编号与元数据）、`--font-sans`（正文）。编辑页把这套语言用到了具体交互上：左侧"案卷目录"导航（`RAIL_ITEMS`）、"红线装配进度"（`THREAD_STEPS` + 完成度条）、"剧本案卷·No.{id}" 编号与旋转印章式状态标记。响应式以 `md`/`lg` 为主，桌面是 hover 展开的左侧图标坞（80px → 220px），移动端切固定顶栏 + 滑出侧栏，编辑页的 AI 助手在移动端变成浮动球 + 抽屉。

**需要注意的文档漂移**：`.github/instructions/frontend.instructions.md` 里写的设计规范仍是"深蓝紫渐变 `from-[#1a237e] via-[#311b92] to-[#4a148c]`、`rounded-md`、`xs:475px` 断点"——那是重构前的配色，与现有代码不符。同时 `tailwind.config.ts` 是一个**失效配置**：Tailwind v4 下它没有被 `@config` 引入，里面的 `screens.xs`、`textShadow`、`animation` 等全部不生效（且把 `@tailwindcss/postcss` 写进 `plugins` 是错误用法）。

### 13.5 前端侧的语音链路

TTS 在前端有**两条独立链路**，这是理解这块代码的关键：

**链路一：实时对局（`ttsStore` + `websocketStore`）**

- `websocketStore` 在 `ai_action` 事件里做多候选字段兼容：`tts_url || audio_url || speech_url || ttsAudioUrl`、`tts_status || status`、`tts_voice || voice_id || voice`；
- 入队条件为"启用 TTS 且有 URL 且状态为空 / `completed` / `ready`"，按 `角色+文本` 去重，文本截断 500 字；
- `startQueueProcessor()` 用 **200ms 的 `setInterval`** 轮询播放队列，`playNext()` 走 `fetch → arrayBuffer → AudioContext.decodeAudioData → BufferSource.start() → await onended`，无 AudioContext 时回退 `new Audio(url).play()`；
- 当前播报角色/文本会驱动 `CharacterAvatars` 高亮与游戏页字幕条。

**链路二：历史回放（`game-history/[sessionId]/replay.tsx`）**

- 从事件里筛出 `tts_file_url && tts_duration > 0` 的记录，累加出一条 `ttsTimeline` 时间轴；
- 用 `audioCache` ref 缓存音频，`onended` 串联下一段，支持暂停/续播与点击跳播。

**两个真实的坑：**

1. **自动播放策略**：`ttsEnabled` 默认 `true`，但 `playNext` 在 `audioInitialized` 为假时直接返回——浏览器要求用户手势才能启动 AudioContext。游戏页靠一次性的 `pointerdown` 监听补初始化，**在此之前到达的语音会静默积压在队列里**。
2. **回放的静默卡死**：回放链路用 `onended` 串联，而**坏 URL 不会触发 `onended`**，错误处理只有一行 `console.error` + `setIsPlaying(false)`——一个失效的音频地址会让整段回放停在原地，且没有任何用户可见的提示。实时链路同样只 `console.error`，不重试、不跳过、不提示。

**降级方向是对的**：语音失败不影响文字显示与游戏推进。但错误处理的完备度明显低于后端的降级链。

---

## 十五、技术栈总表

### 后端

| 类别 | 技术 | 版本要求 |
| --- | --- | --- |
| 语言 | Python | ≥ 3.13 |
| 包管理 | uv | — |
| Web 框架 | FastAPI | ≥ 0.104 |
| ASGI 服务器 | uvicorn | ≥ 0.24 |
| LLM 接入 | openai SDK（原生）/ langchain + langchain-openai | ≥ 1.0 / ≥ 0.1 |
| 阿里云模型 | dashscope | ≥ 1.23.8 |
| 数据校验 | Pydantic | ≥ 2.0 |
| ORM / 迁移 | SQLAlchemy 2.0（asyncio 扩展）/ Alembic | ≥ 2.0 / ≥ 1.16 |
| 数据库 | PostgreSQL（psycopg2-binary + asyncpg） | 16（容器） |
| 对象存储 | MinIO SDK | ≥ 7.2 |
| 实时通信 | websockets | ≥ 12.0 |
| 认证 | python-jose（JWT）+ passlib[bcrypt] | — |
| 图像 | ComfyUI（自建）/ MiniMax API | — |
| 语音 | MiniMax API / CosyVoice 2.0 | — |
| 其他 | aiofiles、aiohttp、jsonschema、gradio-client | — |
| 测试 / 类型 | pytest（≥ 8.4）、pyright | — |

### 前端

| 类别 | 技术 | 版本 |
| --- | --- | --- |
| 框架 | Next.js（Pages Router） | 15.3.5 |
| UI | React / React DOM | 19 |
| 语言 | TypeScript | 5 |
| 样式 | Tailwind CSS（PostCSS 插件） | 4 |
| 组件 | Radix UI + shadcn/ui 风格封装 | — |
| 状态 | Zustand | 5 |
| 图标 / Toast | lucide-react / sonner | — |
| API 生成 | openapi-typescript-codegen | 0.29 |
| 测试 | Playwright（已装依赖，未见成套用例） | — |

---

## 十六、上手路径

### 15.1 最快路径（Docker）

```bash
git clone https://github.com/JianWang97/jubensha-ai.git
cd jubensha-ai
cp .env.example .env
# 至少填入：OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
# 如需语音与配图：MINIMAX_API_KEY、TTS_API_KEY、MINIMAX_GROUP_ID
docker compose up -d --build
```

- 前端：<http://localhost:8009>
- 后端 API 文档：<http://localhost:8010/docs>
- 首次启动自动建表；文件默认存本地目录（Docker volume）
- 部署到服务器时：把 `NEXT_PUBLIC_API_URL` 改成 `http://<IP或域名>:8010` **后重新构建前端镜像**

### 15.2 本地开发

```bash
# 依赖服务：PostgreSQL + MinIO
docker compose -f docker-compose.dev.yml up -d

# 后端（:8010）
cd backend && uv sync && uv run python main.py

# 前端（:3001）
cd frontend && npm install && npm run dev
```

`docker-compose.dev.yml` **没有 restart 策略**，容器会保持停止状态直到手动启动，这一点与生产 compose 不同。

### 15.3 模型与服务的可替换性

项目宣称"兼容所有 OpenAI API 兼容的大语言模型"，这个说法在代码层面是成立的：

- **LLM**：改 `OPENAI_BASE_URL` + `OPENAI_MODEL` 即可切到任意兼容端点（DeepSeek、Kimi、Qwen、本地 vLLM 等）；`LLM_PROVIDER` 可在 OpenAI SDK 直连与 LangChain 路径间切换。
- **TTS**：`TTS_PROVIDER=minimax|cosyvoice2-ex`。
- **图像**：`IMAGE_GENERATION_PROVIDER=minimax|comfyui`。
- **存储**：`FILE_STORAGE=dir|minio`。

四个维度都可插拔，是这份代码在架构上最实用的优点之一。

### 15.4 成本提示（重要）

这是**全 AI 自动对局**，成本结构与普通聊天机器人完全不同。以 6 人局、讨论阶段 30 轮估算：一局游戏的角色发言调用量在**百次量级**（自我介绍 6 + 搜证 12 + 调查 24 + 讨论 30 + 投票 9 + 复盘 8 ≈ 89 次角色调用），再叠加 ConversationFlowController 的调度调用、GMAgent 的阶段公告、以及逐句的 TTS 与配图生成。**未配置 TTS/图像密钥时可以只跑文字模式**，成本会显著下降；建议先用本地或低价模型验证流程。

---

## 十七、客观评价：优势、局限与风险

### 16.1 真正做得好的地方

1. **三层 Agent 解耦（身份 / 记忆 / 指令）**。这是全项目最可复用的设计。它让 system prompt 保持稳定（利于 KV cache 与行为一致性），让阶段指令集中可调（`phase_director.py` 一个 dict 就能改所有阶段的任务书），让记忆独立演进。
2. **信息不对称是工程实现而非提示词承诺**。证据私有化、只广播 `say`、私有怀疑度表——这些是真实的数据隔离，不是让模型"假装不知道"。
3. **ReAct + 分步落库 + 校验回喂**。把长文生成拆成带 schema 校验的多步事务，大幅降低失败率，且每步对用户可见。这是 LLM 工程化的正确姿势。
4. **全链路可降级**。工具调用失败 → JSON 兜底 → 纯文本发言；LLM 调用失败 → 固定安慰语；TTS 失败 → 只显示文字；LLM 规划失败 → 规则计划。游戏不会因为一次 API 抖动而崩掉。
5. **四维度可插拔**（LLM / TTS / 图像 / 存储）+ 一键部署，落地成本低。
6. **推理模型思考流的正确解析**，包括跨 chunk 的 `<think>` 标签处理，这是很多项目做错的地方。
7. **对局历史与回放**：事件落库 + 增量同步 + 回放页面，让"AI 演的一场戏"变成了可存档、可复盘的内容资产。

### 16.2 明确的局限

**架构层面**

1. **运行时状态纯内存，重启即丢失**。`GameEngine` 的全部状态在进程内；`sessions` 是进程内字典。后端重启 → 进行中的对局全部消失（数据库里只有事件日志，缺少重建引擎所需的完整快照：角色怀疑度、私有证据、当前发言者等）。`GameResumeService.resume_game()` 实际上只是把 DB 里的状态从 `PAUSED` 改回 `STARTED` 并返回一个 WebSocket URL，**并不真正恢复引擎状态**（`game_history_service.py:156-175`）。
2. **单进程假设**。会话、编辑锁（`asyncio.Lock`）、生成任务都在进程内存里，**无法水平扩展**到多副本部署。
3. **硬编码的 `ws://localhost:8000/ws`**：`resume_game()` 里拼的恢复地址端口（8000）与实际后端端口（8010）不一致，且写死 localhost，远程部署下这个返回值不可用（`game_history_service.py:172`）。
4. **Alembic 迁移与启动自动建表并存**。`init_database()` 调用 `create_tables()`，意味着生产环境靠自动建表也能跑，但表结构演进时的迁移纪律容易被绕过。

**AI 质量层面**

5. **记忆没有语义检索**。`CharacterMemory` 是纯滑窗 + 重要性淘汰，没有 embedding、没有向量库（全项目 grep 不到 embedding/chroma/faiss/milvus）。对 6 人局尚可（30 条公开对话 ≈ 5 轮），但角色**无法回忆起早期被挤出窗口的关键线索**。这直接限制了长局与复杂剧本的推理质量。
6. **发言类型靠字符串启发式判定**。`game_engine.py:816-823` 用 `"?" in action or "吗" in action` 判断是不是提问、用 `"觉得" in action and "是" in action` 判断是不是指控。这会影响前端消息样式与部分调度逻辑的准确性。
7. **证据匹配是子串包含**，没有语义理解（`evidence_manager.py:118-123`）。角色说"我想看看死者书房的抽屉"可能匹配不到名为"抽屉"的证据，或者误匹配到无关地点。
8. **怀疑度更新规则过于简单**：仅"被点名 → 对提问者 +0.05"（`character_agent.py:258-260`），没有基于证据强度、角色关系或发言矛盾的推断。这使"怀疑度"更像一个装饰性字段，而非真正的推理状态。
9. **胜负判定口径宽松**：`most_voted == murderer`，平票时 `max()` 取字典序首个（`voting_manager.py:24-27`），可能出现"平票但判成功/失败"的偶然结果。

**工程细节层面**

10. **`websocket_server.py` 1661 行**，混合了会话管理、消息路由、游戏主循环、生成模式、编辑模式，是全项目最重的单文件，修改风险集中。
11. **TTS 阻塞式串联在回合里**。`action_callback` 中先 `await` 生成 TTS 再广播（`websocket_server.py:774-806`），语音生成耗时直接叠加到回合延迟上；且 `asyncio.sleep(1)`（发言间）+ `asyncio.sleep(2)`（背景分段间，`game_engine.py:706,731`）等固定延迟让一局的墙钟时间偏长。PRD 里作者自己已把"固定时间过于机械化"列为待优化项。
12. **前端没有成体系的自动化测试**（装了 Playwright 但无 E2E 用例且零引用）；后端测试以 mock 为主，缺少真实 LLM 的集成回归测试。
13. **`plan_only` 的 SAVEPOINT 干跑**是亮点，但编辑 Agent 的长寿命 session + 只在 handler 层 commit 的模式，对异常路径的覆盖要求很高——已核实 `success_count == 0` 时不 commit 也不 rollback（见第十三节 B1），属"设计精巧但脆弱"的一类代码。
14. **成本与延迟没有内建约束**。没有看到 token 预算、并发上限、或按会话的成本统计（只有 `total_tts_duration`）。
15. **前端"半迁移"状态**：文档（`.github/instructions/frontend.instructions.md`）还在描述重构前的配色，`tailwind.config.ts` 在 Tailwind v4 下已失效却仍留在仓库，`scriptsStore.ts`（418 行）与 7 个组件成为零引用死代码。这类漂移不影响运行，但会显著提高新人的阅读成本。
16. **构建可复现性隐患**：生成的前端 API 客户端 `import` 了 `axios` / `form-data`，而 `package.json` 并未声明这两个依赖——目前能构建依赖的是 npm hoisting 的巧合（见第十三节 A1）。这是我认为**最应该优先修掉的一条**：它会在某次依赖调整后突然让构建失败，且报错信息会指向生成代码，极难排查。

### 16.3 如果要在这份代码上继续做，我会优先动哪里

按投入产出比排序：

1. **补上 `axios` / `form-data` 的显式依赖声明**（一行改动，消除构建突然失败的风险）。
2. **给角色记忆加语义检索**（轻量向量库或关键词+BM25 召回），这是提升推理质量最直接的一刀。
3. **把 `GameEngine` 状态快照化落库**（每个阶段结束存一次 JSON 快照），让"暂停/恢复"真正可用，并顺带修好 A2/A3 那条断掉的续玩链路。
4. **TTS 异步化**：发言先广播文字，音频生成完再单独推送（新增一个 `tts_ready` 事件），把语音从关键路径上摘掉。
5. **补齐投票与结算的前端落点**（`voting_complete` / `game_result` 目前是空 case），让"AI 到底投对了没有"这个最有信息量的结果能显示出来。
6. **拆分 `websocket_server.py`**，按模式抽出三个独立的 service。
7. **把发言人选择的启发式升级**为基于结构化状态的判断（谁被提问、谁掌握未公开证据、谁被怀疑），而不是正则匹配问号。
8. **清理死代码与文档漂移**（`scriptsStore`、7 个零引用组件、失效的 `tailwind.config.ts`、旧版设计规范文档），降低后续维护成本。

---

## 十八、横向定位：它在学术与开源版图里的位置

### 17.1 与学术工作的关系

"LLM 多智能体玩谋杀之谜"是有明确学术脉络的方向，代表作是 **"Deciphering Digital Detectives: Understanding LLM Behaviors and Capabilities in Multi-Agent Mystery Games"**（[arXiv:2312.00746](https://arxiv.org/abs/2312.00746)），它构建了基于中文剧本杀《沃特顿庄园》的多智能体环境，并提出"侦探胜率"与"信息泄露"等评测指标。

**需要明确的是：jubensha-ai 并没有引用这些学术工作，仓库里 grep 不到任何 arxiv / 论文引用**（唯一的外部理论引用是 datawhalechina 的教程项目 `hello-agents`）。

这带来一个有意思的定位判断：

| 维度 | 学术项目（如 2312.00746） | jubensha-ai |
| --- | --- | --- |
| 目标 | 测量 LLM 的推理与欺骗能力 | 做一个能玩的、好看的、能持续产出的产品 |
| 剧本 | 固定一个剧本，深度评测 | 支持 AI 无限生成新剧本 |
| 度量 | 胜率、泄露率、行为分类 | 无（靠人工观感） |
| 输出 | 论文与数据集 | 网页界面 + 语音 + 配图 + 回放 |

换句话说，**jubensha-ai 是这条学术脉络的一个"产品化支线"**：它把学术圈验证过的多智能体机制（私有信息、阶段性目标、投票）用工程手段落成了一个完整应用，但没有继承其评测框架。这也正是它的空白点——**如果作者补上一套"AI 侦探胜率 / 凶手逃脱率 / 信息泄露率"的统计面板，这个项目会立刻从"有趣的 demo"升级为"可用于研究的平台"**，而这在当前代码基础上并不难（投票结果和证据公开记录都已经落库在 `game_events` 里）。

### 17.2 与同类开源项目的差异

| 项目 | 定位 | 与 jubensha-ai 的关键差异 |
| --- | --- | --- |
| `Zewang0217/ai-jubensha` | AI 剧本杀平台，Java 技术栈 | 用 LangChain4J + Spring Boot + React；jubensha-ai 的流程建模与剧本生成 Agent 更完整，且多了 TTS 与 AI 配图 |
| `ScottishFold007/ai-murder-mystery` | AI 谋杀之谜 | 更轻量；jubensha-ai 的产品化程度（认证、历史、回放、Docker）明显更高 |
| 通用 AI 语 C / 角色卡前端 | 开放式角色扮演 | 只有"对话"，没有胜负、没有证据系统、没有阶段流程——jubensha-ai 的差异恰恰在这里 |

**综合判断：在中英文开源范围内，`jubensha-ai` 目前是"AI 剧本杀"这一细分方向上功能最完整的一个开源实现**——唯一同时具备"AI 生成剧本 + AI 全自动演绎 + 语音 + 配图 + 对局回放 + 一键部署"的闭环。

### 17.3 适合谁读这份代码

- **想做多智能体应用的工程师**：`character_agent.py` + `phase_director.py` + `character_memory.py` 这三件套是干净可抄的模板。
- **想学 LLM 工程化落地的人**：降级链、工具调用兜底、分步校验回喂、SAVEPOINT 干跑、事件驱动的实时前端，都是真实项目里才会遇到的题目。
- **想做 AI 原生游戏的人**：它回答了一个核心问题——如何把一种**人类游戏规程**翻译成 AI 可以执行的协议（阶段枚举 + 回合预算 + 发言权调度 + 私有信息隔离）。
- **想直接用的团队**：MIT 许可、Docker 一键起、四维度可插拔，改成自己的题材（剧本杀之外的圆桌推理、法庭辩论、狼人杀）成本可控。

---

## 附录：关键文件索引

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `backend/src/core/websocket_server.py` | 1661 | 会话管理、消息路由、游戏主循环、三种模式 Handler |
| `backend/src/core/game_engine.py` | 1075 | 游戏状态机、阶段推进、回合循环、事件与公开聊天 |
| `backend/src/services/script_editor_service.py` | 994 | 剧本编辑指令的校验与增量落库 |
| `backend/src/agents/script_editing_agent.py` | 760 | 对话式剧本编辑 ReAct Agent |
| `backend/src/agents/script_generation_agent.py` | 736 | 剧本生成 ReAct Agent |
| `backend/src/core/conversation_flow_controller.py` | 703 | 发言权调度（规则 + LLM 混合） |
| `backend/src/core/storage.py` | 567 | 本地目录 / MinIO 双后端存储抽象 |
| `backend/src/services/script_generation_service.py` | 488 | 剧本生成的业务编排 |
| `backend/src/services/minimax_service.py` | 465 | MiniMax TTS / 文生图客户端 |
| `backend/src/core/dependency_container.py` | 435 | 自研 DI 容器（singleton/scoped/transient） |
| `backend/src/services/llm_service.py` | 413 | LLM 抽象、工具调用解析、思考流分离 |
| `backend/src/services/comfyui_service.py` | 364 | ComfyUI 工作流文生图 |
| `backend/src/agents/phase_director.py` | 355 | 无状态阶段任务书构建器 |
| `backend/src/agents/gm_agent.py` | 316 | 游戏主持人、动态阶段规划 |
| `backend/src/core/game_tts_manager.py` | 310 | TTS 生成、五级音色回退 |
| `backend/src/services/tts_event_service.py` | 307 | TTS 状态机与音频落库 |
| `backend/src/agents/character_agent.py` | 273 | 角色 Agent（三层架构的组装点） |
| `backend/src/agents/character_memory.py` | 146 | 双层私有记忆 + 怀疑度 |
| `backend/src/core/evidence_manager.py` | 123 | 证据发现与私有化 |
| `backend/src/agents/character_identity.py` | 79 | 稳定身份 system prompt |
| `backend/src/agents/agent_tools.py` | 174 | 角色行动的工具定义与合并 |
| `backend/src/agents/character_agent_manager.py` | 121 | 多智能体通信总线与广播 |
| `frontend/src/stores/websocketStore.ts` | 508 | WS 事件流 → 前端运行时状态 |
| `AGENTS.md` | 196 | 作者写给 AI 协作者的架构导览（很好的入门读物） |

---

*本报告基于对仓库源码的逐文件阅读完成。所有架构判断均标注了对应文件与行号，可自行核对。文中对"局限"的判断属于分析者视角，不代表作者立场。*
