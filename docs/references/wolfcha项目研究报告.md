# oil-oil/wolfcha（猹杀）项目研究报告

**分析对象**：`oil-oil/wolfcha` — AI 驱动的狼人杀（Werewolf / Mafia）社交推理游戏
**代码快照**：`main` 分支，本地快照位于 `.research/wolfcha-snapshot/`（2026-09-22 最后推送）
**报告日期**：2026-09-24
**证据规则**：本报告所有非常识性结论均标注 `文件:行号`，路径相对快照根目录。行号可在快照中直接复核。凡来自第三方博客而未能被源码证实的说法，一律在文中显式标注为"未证实"。

---

## 目录

1. [项目速览](#一项目速览)
2. [它到底在解决什么问题](#二它到底在解决什么问题)
3. [整体架构](#三整体架构)
4. [核心设计之一：信息隔离](#四核心设计之一信息隔离多人在线改单机的真正难点)
5. [核心设计之二：双层人格与"诚实的缺陷"](#五核心设计之二双层人格与诚实的缺陷)
6. [核心设计之三：流式发言协议与容错流水线](#六核心设计之三流式发言协议与容错流水线)
7. [核心设计之四：异步竞态治理](#七核心设计之四异步竞态治理)
8. [游戏规则的完整度](#八游戏规则的完整度)
9. [技术栈总表](#九技术栈总表)
10. [工程质量体系](#十工程质量体系)
11. [商业化与服务端](#十一商业化与服务端)
12. [内容与 SEO 策略](#十二内容与-seo-策略)
13. [已核实的缺陷与风险清单](#十三已核实的缺陷与风险清单)
14. [对同类项目的可迁移经验](#十四对同类项目的可迁移经验)
15. [附录：数据与规模](#十五附录数据与规模)

---

## 一、项目速览

| 项 | 值 |
|---|---|
| 仓库 | [github.com/oil-oil/wolfcha](https://github.com/oil-oil/wolfcha) |
| 一句话定位 | 你坐一席，其余 7–11 个座位全部由 LLM 扮演的狼人杀 |
| 线上地址 | [wolf-cha.com](https://wolf-cha.com) |
| 起源 | 观猹（Watcha）× 魔搭（ModelScope）环球黑客松 |
| 名称 | Wolf（狼人杀）+ Cha（猹）——既在桌上参与，也像观众围观一群 AI 人格互撞 |
| 主要语言 | TypeScript（约 290 万字节，占绝对主体） |
| 代码规模 | `src/` 约 67,800 行；全仓 TS/TSX 约 72,700 行 |
| Star / Fork | 718 / 63（抓取于 2026-09-24，GitHub API） |
| 创建 / 最后推送 | 2026-01-07 / 2026-09-22 |
| 释放版本 | v1.0.0（2026-02-16） |
| 贡献者 | 6 人，其中 `oil-oil` 245 次提交、`phdwdm-lang` 124 次提交 |
| 许可 | **LICENSE 文件是 Apache-2.0，但三份 README 都写 MIT**（见缺陷清单 A-6） |
| 运行形态 | Next.js 全栈单体，Vercel 优先部署；附带一份已失效的 Dockerfile |

### 这个项目最值得注意的一点

绝大多数"AI 玩游戏"的 Demo 走同一条路：写一段很长的系统提示词把规则讲清楚，反复强调"请认真推理"，然后期待模型表现聪明。

**Wolfcha 反过来做**：它显式给每个 AI 玩家写入认知缺陷。

```ts
// src/types/game.ts:78-108
export interface Persona {
  voiceRules: string[]; mbti: string; gender: ...; age: number;
  werewolfExperience?: string;   // 狼人杀水平
  vocabularyStyle?: string;      // 用词习惯
  reasoningStyle?: string;       // 推理风格
  pressureStyle?: string;        // 被质疑时的反应
  uncertaintyStyle?: string;     // 不确定时怎么表达
  mistakePattern?: string;       // ← 常见误判（认知缺陷）
  wolfDeceptionStyle?: string;   // 拿到狼牌后怎么演
  ...
}
export interface PlayerMind {
  courage: string;            // 敢不敢诈
  memoryBias: string;         // 记忆偏好
  suspicionThreshold: string; // 怀疑阈值
  selfProtection: string;     // 自保倾向
  logicDepth: string;         // 推理深度
  tablePresence: string;      // 场上话语权重
}
```

`mistakePattern` 这个字段的含义直白到近乎挑衅：**认知缺陷**。它被注入提示词为"常见误判：…"（`src/lib/prompt-utils.ts:248,261`），并被明确要求"其中的缺陷和不确定倾向偶尔体现即可，不要每次发言都表现出来"（`src/lib/prompt-utils.ts:251`）。

作者对这个字段的设计意图很清晰：一桌滴水不漏的 AI 会让狼人杀失去张力。玩家真正想要的情绪是"想让 7 号闭嘴"——而只有会犯错的 7 号才能激发这种情绪。

> **外部佐证**：CSDN/共绩算力的一篇博客（[链接](https://blog.csdn.net/gongjisuanli/article/details/161707831)）专门以此为主题讨论该项目。该文中的 `Persona`/`PlayerMind` 双层命名与 `mistakePattern` 字段均已在源码中逐一核实；但该文所述的"发言温度 1.1、复盘 0.1"与源码不符（实际为 1.0 / 0.3），本报告以源码为准。

---

## 二、它到底在解决什么问题

### 2.1 狼人杀这个游戏形态的硬约束

狼人杀的核心乐趣依赖三个物理条件，而这三个条件在单机场景下全部崩塌：

| 线下条件 | 单人化后的困难 |
|---|---|
| 8–12 个真人凑齐一桌 | 约局本身就是最大成本，这也是项目 README 开篇的动机 |
| 每个人掌握**不同**的私有信息 | 单一 LLM 上下文天然全知，必须人工制造信息不对称 |
| 发言是**实时、连续、有性格**的 | 需要流式生成 + 语音 + 口型，否则像在读公告 |

### 2.2 项目的功能版图

- **8–12 人**对局，人类玩家占 1 席（也可开**观众模式**，全部由 AI 打）
- 8 种角色：村民、狼人、白狼王、预言家、女巫、猎人、守卫、白痴（`src/lib/role-configuration.ts:3-9`）
- 完整流程：夜间行动 → 警长竞选（上警/发言/投票/PK）→ 白天讨论 → 放逐投票 → 遗言/开枪/警徽移交
- 实时生成的发言（流式字幕 + 可选 TTS + 口型动画）
- 赛后复盘：规则层算客观事实，模型写主观评语与奖项标签（`src/lib/game-analysis.ts`）
- 双语（中/英）、两套模式（普通 / 原神模式）
- 开发者控制台（可跳阶、可预览任意阶段提示词、可直连 TTS）

### 2.3 一句话总结它的技术主张

> **把"每个 AI 主观视角下的世界"当作一等公民来建模，而不是把"上帝视角的游戏状态"喂给所有人然后嘱咐它别作弊。**

这句话是理解整个代码库的钥匙。下面第三章到第七章都在展开这一主张。

---

## 三、整体架构

### 3.1 分层视图

```
┌────────────────────────────────────────────────────────────────┐
│ 表现层  src/app/page.tsx (1758) + src/components/ (84 文件)      │
│         DialogArea / PlayerCardCompact / TalkingAvatar /         │
│         WelcomeScreen / RoleRevealOverlay / DevConsole(2036)     │
├────────────────────────────────────────────────────────────────┤
│ 流程编排  src/hooks/useGameLogic.ts (2357 行，主循环)            │
│          + src/hooks/game-phases/{useDayPhase,useBadgePhase,     │
│            useSpecialEvents}                                     │
│          + src/hooks/useDialogueManager.ts (字幕队列)            │
├────────────────────────────────────────────────────────────────┤
│ 阶段层   src/game/core/PhaseManager.ts                           │
│          + src/game/phases/{NightPhase(713), DaySpeechPhase(504),│
│            VotePhase(453), BadgePhase, HunterPhase,              │
│            WhiteWolfKingBoomPhase}                               │
│          每个 GamePhase 负责"生成本阶段该给这个玩家看的提示词"    │
├────────────────────────────────────────────────────────────────┤
│ 规则层   src/lib/game-master.ts (2036 行，纯函数)                │
│          发牌 / 阶段转换 / 胜负判定 / 计票 / 夜间结算              │
│          + src/lib/role-configuration.ts + vote-rounds.ts        │
│          + src/lib/game-flow-controller.ts (FlowToken)           │
│          + src/lib/SmartJumpManager.ts (1586 行，仅 DevTools 用) │
├────────────────────────────────────────────────────────────────┤
│ 提示词层 src/lib/prompt-utils.ts (1132 行)  ← 隐私闸门所在        │
│          + src/lib/character-generator.ts (角色生成)             │
│          + src/lib/ai-config.ts (温度/模型路由)                   │
├────────────────────────────────────────────────────────────────┤
│ 状态层   src/store/game-machine.ts (1143 行，Jotai)              │
│          gameStateAtom = 唯一真源 + localStorage 持久化           │
├────────────────────────────────────────────────────────────────┤
│ 传输层   src/lib/llm.ts (1115 行) → /api/chat → 三家网关          │
│          + src/lib/streaming-speech-parser.ts (逐字符状态机)      │
└────────────────────────────────────────────────────────────────┘
```

### 3.2 三处最关键的设计决策

1. **每个 `(阶段, 玩家)` 组合现场组装一次提示词**，而不是维护一份全局消息历史再分发给所有人。
   唯一分发点是 `resolvePhasePrompt(phase, state, player)` → `phaseManager.getPrompt(...)`（`src/lib/game-master.ts:126-146`）。所有 LLM 调用（发言、投票、技能、上警）都经过它，**没有旁路**。

2. **状态是单一全权限对象，隔离不靠数据脱敏，靠组装期过滤。**
   `GameState` 包含所有角色的全部私有记录，直接持久化到 localStorage。信息隔离完全由第 4 章描述的"取值来源约束"实现。这是一把双刃剑（见缺陷 B-1）。

3. **发言输出协议选用 JSON 字符串数组**，而不是对象或工具调用。
   原因不是审美，而是工程约束：数组元素可以在**任意 chunk 边界**上闭合即发布，而对象必须等整个 JSON 闭合（`docs/单人上下文约束.md:60` 明确讨论了这一代价）。

---

## 四、核心设计之一：信息隔离（"多人在线"改"单机"的真正难点）

### 4.1 问题陈述

一局 11 人游戏里，各角色的已知信息差异极大：

| 角色 | 独有信息 |
|---|---|
| 狼人 | 队友是谁、每晚刀谁 |
| 预言家 | 逐夜查验记录 |
| 女巫 | 药水状态、当晚刀口（仅持解药时） |
| 守卫 | 逐夜守护路线 |
| 村民 | 什么都没有 |

如果给村民的提示词里混进 `state.nightActions.seerHistory`，AI 村民立刻变成全知上帝，游戏彻底失去意义。这是个**安全性质**的要求：不能"大多数时候对"，必须结构性正确。

### 4.2 实现机制：单一闸门 + 取值来源约束

**闸门**：`buildRolePrivateInfo(state, player, options)`（`src/lib/prompt-utils.ts:621-770`），按 `player.role` 分支：

```ts
if (player.role === "Seer")   → <your_seer_checks>  读 state.nightActions.seerHistory   // :630-644
if (player.role === "Witch")  → <your_potions>       刀口仅在"当晚仍持解药"时可见        // :646-704
if (player.role === "Guard")  → <your_guard_info>    全部夜次守护路线                    // :706-728
if (isWolfRole(player.role))  → <your_wolf_team>     队友 + 狼队出刀记录                 // :730-767
return null;   // ← 村民走到这里：私有块在提示词中根本不存在  // :769
```

关键在最后一行：**不是把私有信息过滤掉，而是根本不生成**。村民的提示词里不存在这些标签块，不存在"忘了删"的可能。

私有块被强制放在上下文最前（`src/lib/prompt-utils.ts:787-789`，注释标明"第一优先级"），确保模型先看到自己的秘密再看公共信息。

**取值来源约束**是更精妙的一层。公开角色配置块刻意**只读静态配置表，不读 `state.players`**：

```ts
// src/lib/prompt-utils.ts:74-104
/** 展示开局前公开的角色构成。刻意读取公开的人数配置，
 *  而不是 state.players —— 这样没有任何座位-角色或存活-角色信息会泄露。 */
export const buildPublicRoleConfiguration = (playerCount: number): string => { ... }
```

注解原文是 "no seat-to-role or alive-role information can leak"。也就是说，公开信息块的数据来源被限制为"主持人会当众念出来的东西"（人数配置、已翻牌事件），任何"从游戏状态推导出来"的字段都被禁止进入公开块。

同理，死亡原因被匿名化：普通夜死写"死因未公开"，只有公开技能致死（猎人开枪、白狼王自爆、放逐）才具名（`src/lib/prompt-utils.ts:782-813`）。

### 4.3 零信任的验证方式：证据矩阵

这是整个项目在工程上最成熟的部分。`src/game/phases/context-regressions.test.ts` 对 **中文/英文 × 普通/原神模式** 四个组合，**同时**断言两件事：

1. **应出现的**：村民提示词里**包含**已出局预言家的公开查杀原文、遗言、警徽流转与末置位归票（`context-regressions.test.ts:57-80`）
2. **不应出现的**：同一提示词**不匹配** `<your_seer_checks>|<your_guard_info>|<your_wolf_team>`

```ts
// 开发规范 docs/单人上下文约束.md:24
// 「提示词遗漏用独特证据标记；信息泄露同时测试『应出现』和『不应出现』。」
```

这条"双向断言"的规范值得单独记住。只测"私有信息没泄露"会漏掉"该给的信息没给"，而后者会让 AI 做出看似愚蠢的推理——作者在 `docs/单人上下文约束.md:31-43` 记录了一次真实回归：系统提示词里有"只讨论存活玩家"、原神模式有"禁止围绕出局玩家复盘"，这些措辞**压制**了模型使用已出局预言家的合法公开证据，导致玩家反馈"原文漏传"。修复方式是删除表达禁令的 `bannedDiscussion` 文案，改为"按本阶段可选范围决定**行动目标**，继续允许根据出局者的公开证据**推理**"。

### 4.4 区分"行动"与"已公开结果"

这是另一个反复出现的设计原则（`docs/单人上下文约束.md:26`）：

> 「增加上下文字段时明确它是**行动**、**已公开结果**，还是**玩家声明**；不要从当前状态倒推过去事件。」

落地为一个显式开关：夜间结算时写 `resultsAnnounced: false`，天亮公布后写 `true`；判断逻辑集中在 `areNightResultsVisible(state, day)`（`src/lib/night-visibility.ts:4-13`）。**切换提示词阶段时不重新判断，而是保持原可见性**——这正是 `docs/单人上下文约束.md:15` 记录的一个已修 bug（原先依据提示词 phase 判断信息公开性，导致临时切换决策阶段时泄露夜间结果）。

同样原则体现在守卫记录上：每条记录同时标注"守护目标生死"和"该夜全场公开死亡结果"，并显式声明"守护目标未出局不代表全场平安夜，也不能证明守护生效"（`src/lib/prompt-utils.ts:713-727`）。`buildDecisionGrounding` 再加两条防御："记录缺失，不能判为平安夜"、"累计几次平安夜不等于连续几夜"（`src/lib/prompt-utils.ts:275,293`）。

---

## 五、核心设计之二：双层人格与"诚实的缺陷"

### 5.1 两层独立的人格

| 层 | 作用域 | 字段数 | 决定什么 |
|---|---|---|---|
| `Persona` | 表层，对外可见的性格 | 20（`src/types/game.ts:78-99`） | 说话口吻、用词习惯、被质疑时的反应、拿到狼牌怎么演 |
| `PlayerMind` | 深层，稳定的牌桌心智 | 6（`src/types/game.ts:101-108`） | 敢不敢诈、记忆偏好、怀疑阈值、自保倾向、推理深度、话语权重 |

两层都通过"不要向其他玩家明说"的隐藏块注入（`<hidden_communication_profile>`、`<hidden_player_mind>`，`src/lib/prompt-utils.ts:239-265,298-323`）。**它们与游戏身份（`role`）始终分离**：角色与胜利条件来自 `getRoleText`/`getWinCondition`（`src/game/phases/DaySpeechPhase.ts:130-131`），persona 单独注入 system 头部。这实现了 README 里那句"每个 AI 都有稳定性格，再叠加一层隐藏的游戏身份"。

一个值得注意的细节：**persona 只进发言与上警阶段，不进投票与夜间决策**（`DaySpeechPhase` / `BadgePhase` 注入，`VotePhase` / `NightPhase` 不注入）。即"说话像个人，决策像台机器"——这是刻意的：性格影响表达，但不污染理性判断。

### 5.2 温度阶梯：用一个系统表达五种"心智状态"

```ts
// src/lib/ai-config.ts:6-49
AI_TEMPERATURE = { STRICT: 0.1, LOGIC: 0.4, BALANCED: 0.7, CREATIVE: 1.0, WILD: 1.2 }

GAME_TEMPERATURE = {
  CHARACTER_GENERATION: WILD,      // 1.2  角色档案要多样性
  CHARACTER_PERSONA:    BALANCED,  // 0.7  完整 persona 要 JSON 结构稳定
  SUMMARY:              STRICT,    // 0.1  总结要准确
  SPEECH:               CREATIVE,  // 1.0  发言要生动（注释：取 1.0 而非更高，避免立场漂移）
  ACTION:               LOGIC,     // 0.4  投票/技能：略高一点让"蠢萌冲动"角色有概率出现
  BADGE_SIGNUP:         BALANCED,  // 0.7  上警是早期 meta 决策，允许更多变化
}
```

源码注释本身就说明了取舍逻辑，尤其是 `ACTION` 那条：

> 「稍微提高一点点(0.4)，让"蠢萌"或"冲动"角色的行为有概率出现，但主要还是依靠 Prompt 中的逻辑来驱动」

这是一个比"用统一温度 + 超长提示词"更克制的做法——**用采样参数承担多样性，用提示词承担正确性**。

### 5.3 角色生成：两段式结构化生成

`src/lib/character-generator.ts`（752 行）：

- **Stage A**：基础档案（姓名、性别、年龄、MBTI、背景），`WILD` 温度，严格 `json_schema`，`minItems === maxItems === count` 保证一次生成恰好 N 个（`:451-484,559-566`）
- **Stage B**：按 3 人一批流式生成 `voiceRules` + 8 个狼人杀字段 + 6 个 playerMind 字段，`BALANCED` 温度，批次并发 `Promise.allSettled`（`:486-547,597-604,734-743`）
- 校验失败即抛错（`:568-570,743-747`）——**没有模板兜底**，这是一个真实的开局失败面（见缺陷 B-4）

### 5.4 原神模式：一个"减法"模式

原神模式几乎绕过 LLM：`generateGenshinModeCharacters` 只用模型品牌名 + 桩 persona（`src/lib/character-generator.ts:148-191`），且 `buildPersonaSection` 在该模式直接返回空字符串（`src/lib/prompt-utils.ts:325-326`），发言要求换成"不要编造人设背景"（`src/i18n/messages/zh.json:1019`）。

**这实际上与"人格"卖点相冲突**：该模式下所有 AI 的差异化只剩模型品牌名与语气规则。可以理解为一种"让玩家看清多个模型原生差异"的实验模式，而非人格模式。

---

## 六、核心设计之三：流式发言协议与容错流水线

### 6.1 输出协议

```jsonc
// 发言：JSON 字符串数组，逐元素流式发布
["第一段公开发言。", "第二段公开发言。"]
```

提示词里的原文约束（`src/i18n/messages/zh.json:1020`）：

> 「返回 JSON 字符串数组，每个元素是一条消息气泡。…保留最外层方括号，每个元素直接写台词文本。**不要返回聊天记录、role 字段、提示词复述或作为字符串的嵌套 JSON。**」

最后那句是对真实故障的直接回应。`src/lib/fixtures/speech-live-responses.json` 保存了三条**真实模型响应**：

| 故障 | 后果 | 现在如何处理 |
|---|---|---|
| 整个回答被 `{"messages":[...]}` 包装 | 曾被替换成省略号，导致女巫被指责"没有有效发言"并被投出 | 识别为公开包装字段 |
| `[{role:"user",content:提示词},{role:"assistant",content:"..."}]` 会话封套 | 提示词与编码 JSON 被当成台词朗读 | 对象闭合后确认 `role`，过滤非 assistant 消息 |
| 无外层数组的逗号分隔对象 | 只显示首句 | 仅在完整 JSON 值之间接受分隔逗号 |

### 6.2 解析器：逐字符状态机 + 公开字段白名单

`src/lib/streaming-speech-parser.ts` 是一个逐字符推进的 JSON 状态机，维护一个 `Frame` 栈（`:8-15`）。核心安全规则：

```ts
// src/lib/streaming-speech-parser.ts:16
const PUBLIC_FIELDS = new Set(["speech","content","message","messages","text","value","segments","speeches"]);

// :35-38  只有"公开数组"或"键名在白名单内"的值才可能被发布
private isPublicValue(): boolean {
  const frame = this.frames.at(-1);
  return !frame || (frame.public && (frame.type === "array" || PUBLIC_FIELDS.has(frame.key ?? "")));
}
```

文件头注释把意图写得毫无歧义：

> **「仅解析协议中的公开发言；分析字段及其整个子树永远不能进入字幕或 TTS。」**

也就是说 `{"analysis": {...}}` 或 `{"reasoning": "..."}` 的**整棵子树**被丢弃，不可能因为某个深层字符串看着像台词而被朗读出来。加上 `role !== "assistant"` 的过滤（`:45-67`），提示词本身也不会被念出来。

**半句防护**：字符串闭合后的下一个非空白字符必须是 `,` 或匹配的闭括号，否则整个解析标记为 `invalid`（`:84-99`）。这修的是真实故障——`docs/单人上下文约束.md:72` 记录 7 号的未转义引号把发言截成半句，下一位角色据此误判其"没有表态"。

### 6.3 恢复路径：宁可停下来，也不把错误写成台词

流结束时若 `!hasCompleteDocument()` 或无任何段落，进入 `recoverPublicSpeech`（`src/lib/game-master.ts:874-914`）：

- 用**原提示词 + 已公开段落清单**重试一次
- 强制 `{segments:[...]}` 严格 schema，全部校验通过才提交
- **损坏原文绝不回灌给模型**（否则错误会自我强化）
- 完整重复前缀按**位置**剥离
- 恢复耗尽 → 停止阶段推进 + 显示系统错误与重试入口（`src/hooks/game-phases/useDayPhase.ts:239-251`）

这条"失败即暂停"的策略值得单独评价：它牺牲了流畅性（一条坏响应会卡住游戏直到用户点击），换取了**"屏幕上出现的每一句话都确实由模型生成且完整"**这一不变式。对于一个以"AI 在说话"为核心体验的产品，这个取舍是对的。

### 6.4 JSON 容错的多层流水线

| 层 | 位置 | 作用 |
|---|---|---|
| `<think>` 块剥离 | `src/lib/llm.ts:466-498,971-1034` | 流式状态机剥离 MiniMax 等把思考嵌在 content 里的模型输出（支持 think/thinking/analysis/reasoning/thought 五种标签） |
| 宽松 JSON 修复 | `src/lib/llm-json.ts:1-50`（`ai-json-fixer` aggressive 模式） | 修补悬挂引号、提取 markdown 围栏、首个 `{` 到末个 `}` 提取 |
| 动作类结构化输出降级 | `src/lib/game-master.ts:1206-1222` | `supportsStrictJsonSchema` 只认 `deepseek*` 前缀，其余退回 `json_object` |
| 供应商级适配 | `src/lib/tokendance-response-format.ts:13-31` | TokenDance 把 `json_schema` 降级为 `json_object`，并把 schema 作为前置 system 消息重新注入 |

**注意最后一条**：AGENTS.md 里的规范明确写了不能靠模型名前缀猜能力——

> 「不能仅凭模型名称前缀判断严格 Schema 支持。按实际 Provider、模型和 `response_format.type` 实测。」

---

## 七、核心设计之四：异步竞态治理

### 7.1 问题：一整局是一条长链 await

游戏推进是一串串行的异步脚本：LLM 流式返回 → 逐段解析 → 逐段 TTS → 逐段播放 → 推进下一位。用户可以在任何时刻重开局、用 DevTools 改状态、跳阶。每次 `await` 之后，回调都可能属于一个**已经过期的世界**。

### 7.2 FlowToken

```ts
// src/lib/game-flow-controller.ts:6-28
getToken() { const capturedValue = this.tokenValue; 
             return { isValid: () => this.tokenValue === capturedValue }; }
interrupt() { this.tokenValue += 1 }
```

自增计数 + 闭包快照。规范是"`await` 前捕获，`await` 后校验"（`NightPhase.ts` 内约 25 处）。`restartGame` 先 `interrupt()` 再重置（`src/hooks/useGameLogic.ts:1804-1816`），因此重开时旧夜晚协程无法回写新局。

### 7.3 三重身份：为什么"按文字去重"是错的

这是整个项目里最容易被低估的设计。发言段落的身份是 **`${requestId}:${index}`**，即"请求 ID + 段落位置"。

`docs/单人上下文约束.md:10` 记录了放弃文字去重的原因：

> 「按文字去重，短句另走兜底，导致遗漏和乱序」

对应测试用例保留了两次"不对。"（`src/lib/streaming-speech-parser.test.ts:7` 用 `["不对。","我刚才的意思是先核对发言。","不对。"]`）。源码注释把道理讲得很直白（`src/lib/game-master.ts:930`）：

> 「数组位置就是段落身份；相同文字可以是两个有意重复的段落。」

同一个身份被用于三处，缺一不可：

| 用途 | 身份 | 说明 |
|---|---|---|
| 字幕/历史幂等 | `${requestId}:${index}` | 位置即身份 |
| TTS 缓存复用 | `makeAudioTaskId(voiceId, text, provider)`（`src/lib/audio-manager.ts:26-28`） | 内容寻址，同一句话只合成一次 |
| TTS 播放去重 | `playbackId = ${requestId}:${index}`（`src/hooks/useDayPhase.ts:158`） | 同一句话出现两次 = 1 次合成、2 次播放 |

`SpeechRequest.isValid` 同时校验 `gameId`、`day`、`phase`、`currentSpeakerSeat`、`speechRoundStartMessageIndex`、`devMutationId`、`!winner`（`src/lib/speech-request.ts:5-15`）——恰好对应 AGENTS.md 里"对局、天数、阶段、角色、轮次、开发修改和 FlowToken"。

### 7.4 预取身份

AI 发言人说完最后一句后立即**预取**下一位 AI 的发言（用预测的 post-state）。复用的判据不是"消息数相同"，而是把**实际提示词与模型配置**序列化后做哈希（`getSpeechContextKey`，`src/lib/game-master.ts:2032-2036`）。`useDialogueManager.test.ts:18,44` 专门覆盖"消息数相同但提示词不同时不得复用"。

### 7.5 一个诚实的负面发现

`AsyncFlowController` 的 `pause`/`resume`/`isPaused` 在整个 `src/` 中**零调用者**，是死代码。真实的暂停机制是轮询 `gameState.isPaused`（100ms 一次，`src/hooks/useGameLogic.ts:240-244`）。也就是说，架构文档里描述的"内建暂停/恢复能力"并未被实际使用。

---

## 八、游戏规则的完整度

规则细节的还原度是该项目最容易被低估的资产。以下全部经源码核实：

### 8.1 角色配置随人数变化（`src/lib/role-configuration.ts:3-9`）

| 人数 | 配置 |
|---|---|
| 8 | 3 狼 + 预言家 / 女巫 / 猎人 + 2 村民 |
| 9 | 3 狼 + 预言家 / 女巫 / 猎人 + 3 村民 |
| 10 | 2 狼 + **白狼王** + 预言家 / 女巫 / 猎人 / **守卫** + 3 村民 |
| 11 | 3 狼 + 白狼王 + 预言家 / 女巫 / 猎人 / 守卫 / **白痴** + 2 村民 |
| 12 | 同 11 + 1 村民 |

发牌后按玩家偏好角色对调人类座位（`src/lib/game-master.ts:228-247`）。

### 8.2 胜负判定

```ts
// src/lib/game-master.ts:399-413
狼人全出局            → 好人胜
存活狼人 ≥ 存活好人   → 狼人胜
```

项目采用"人数判定"而非线下常见的"屠边"。纯函数，在每个决策边界重复求值（`DaySpeechPhase.ts:342,399`、`VotePhase.ts:363,445`、`useSpecialEvents.ts:154` 等）。

### 8.3 夜间结算的细节规则

| 规则 | 实现 | 位置 |
|---|---|---|
| 行动顺序 | 守卫 → 狼人 → 女巫 → 预言家，全部记录后一次性判定 | `src/hooks/game-phases/useSpecialEvents.ts:177-272` |
| **同守同救（奶穿）** | 被守卫保护**且**被女巫解救 → 仍然出局，死因记 `milk` | `useSpecialEvents.ts:203-213` |
| 毒药无视守护 | 毒药无条件致死，与守卫无关 | `useSpecialEvents.ts:216-219` |
| **死亡延迟公布** | 先写 `pendingWolfVictim`/`pendingPoisonVictim`，首日警徽竞选结束后才真正 kill 并公布 | `useSpecialEvents.ts:227-228` → `DaySpeechPhase.ts:231-314` |
| 守卫限制 | 可自守，不可连续两晚守同一人 | `src/game/phases/NightPhase.ts:622` 等 |
| 女巫限制 | 解药需已知刀口；解药已用后不再告知刀口；毒药排除自己 | `NightPhase.ts:663-666`、`game-master.ts:1699-1705` |
| 猎人限制 | **被毒死不能开枪**（单独被毒或与狼刀重叠，`hunterCanShoot=false`） | `DaySpeechPhase.ts:267-283` |
| 白痴 | 首次被放逐翻牌免死，此后**永久失去投票权** | `VotePhase.ts:304-311,345-370` |
| 白狼王 | 仅白天可自爆，带走一人，无遗言，警长死则警徽强撕，随后直接入夜 | `useGameLogic.ts:696-761` |

### 8.4 警长与投票

| 规则 | 实现 |
|---|---|
| 警长放逐票 **1.5 票** | `game-master.ts:508`、`VotePhase.ts:223` |
| 但**竞选本身不加权** | `useBadgePhase.ts:190` |
| 竞选平票 → PK 发言重投；二次平票 → 撕毁警徽（上限 2 轮） | `useBadgePhase.ts:204-256` |
| 单候选自动当选，不投票 | `useBadgePhase.ts:508-530` |
| 放逐平票 → 一次性 PK，**PK 参与者不得投票**；再平票则无人出局 | `VotePhase.ts:397-433` |
| 弃票哨兵 `-1`，计票跳过 | `game-master.ts:1164-1170` |
| 逐轮投票保存独立快照 `voteRounds` | `src/lib/vote-rounds.ts:4-11` |

**最后一个"逐轮快照"是后期修复的结果**：`docs/单人上下文约束.md:19,27` 记录原先"每日一个票型对象覆盖复投"，导致 PK 复投、警徽复投的历史丢失。现在已结算轮次按 `gameId:day:kind:round` 幂等追加，旧 `voteHistory`/`badge.history` 只作为"每日最后一轮"的兼容入口。

### 8.5 一个刻意的信息设计

白天投票阶段**不向 AI 展示实时票型**（`src/lib/prompt-utils.ts:1055-1058`）。目的是防止从众效应（bandwagon）——AI 应该基于推理投票，而不是看到别人投谁就跟票。这类"刻意不给信息"的设计与第 4 章的"刻意给信息"同样重要。

---

## 九、技术栈总表

### 9.1 前端

| 类别 | 选型 | 备注 |
|---|---|---|
| 框架 | **Next.js 16.1.1** App Router | 显式使用 `--webpack` 而非 Turbopack（`package.json`） |
| UI 库 | **React 19.2.3** | 启用 **React Compiler**（`next.config.ts:12` + `babel-plugin-react-compiler@1.0.0`） |
| 语言 | TypeScript 5（`strict: true`） | 路径别名 `@/*` → `./src/*` |
| 样式 | **Tailwind CSS 4**（`@tailwindcss/postcss`） | **无 `tailwind.config.*`、无 `@theme` 块**；`globals.css` 首行只有 `@import "tailwindcss"`，设计系统完全活在手写 CSS 自定义属性里 |
| 组件原语 | **Radix UI**（avatar / dialog / dropdown-menu / scroll-area / select / slot / tabs / tooltip 共 8 个包） | 无样式原语，无 shadcn 生成层 |
| 动画 | **Framer Motion 12.24** | 页面级 `opacity+y+blur(10px)` 切换；`AnimatePresence` / `LayoutGroup` |
| 状态 | **Jotai 2.16** | `gameStateAtom` 单真源 + localStorage 持久化 |
| 富文本 | **Tiptap 3**（StarterKit + `extension-mention` + `suggestion`） | 用于 `@座位号` 提及输入，序列化为纯文本供 LLM 直读 |
| Markdown | `react-markdown` + `remark-gfm` / `streamdown` | 复盘与指南页渲染 |
| 图标 | `@phosphor-icons/react` + `lucide-react` | 两套并存 |
| 其他 | `ahooks`、`clsx` + `tailwind-merge`、`cva`、`sonner`、`qrcode.react`、`html-to-image`（分享海报）、`uuid`、`undici` | |

### 9.2 服务端与基础设施

| 类别 | 选型 |
|---|---|
| API | Next.js Route Handlers（`src/app/api/**`） |
| 数据库 / 认证 | **Supabase**（`@supabase/supabase-js` 2.90 + `auth-ui-react`） |
| 支付 | TokenPay（TokenDance）/ 爱猹收 Watcha Pay（`watcha-pay`）；**Stripe 仅保留历史 webhook** |
| LLM 网关 | ZenMux / 阿里云百炼 DashScope / TokenDance（3 家） |
| TTS | MiniMax（`speech-01-turbo` / TokenDance 的 `minimax-speech-2.8-turbo`） |
| 分析 | `@vercel/analytics` + GA4（`G-3SSRH8KPLY`，直接写在 layout head） |
| i18n | **next-intl 4.7**（但**没有 `[locale]` 路由段**，见 12.5） |
| 包管理 | pnpm（`pnpm-workspace.yaml` 存在但配置未完成，见缺陷 A-7） |

### 9.3 动画与音频的真实实现

这一部分特别值得记录，因为 README 的描述（"lip-sync animation"）容易让人误以为是音素级口型同步。

**口型同步的真实做法**（`src/components/game/TalkingAvatar.tsx:94-122`）：

1. 头像是 **DiceBear Notionists 远程 SVG API**（`https://api.dicebear.com/7.x/notionists/svg?...`，`src/lib/avatar-config.ts:227`），通过 URL 参数决定发型/眼睛/嘴唇/背景
2. 嘴唇变体共 30 个，禁用 3 个，**说话专用只有 2 个**：`TALKING_LIPS = ["variant04","variant11"]`（`src/lib/avatar-config.ts:57`）
3. 说话时 `setInterval` **每 120ms** 在两个口型之间循环，停止则清除定时器回到静止口型
4. 触发源是 **TTS 播放回调**（`audioManager.setCallbacks(onPlayStart, onPlayEnd)` → `talkingPlayerId`，`DialogArea.tsx:410-419`），**不是音频波形/音素分析**

结论：这是**固定节奏的开合动画**，与语音内容无关。作为氛围手段完全够用（成本几乎为零），但不是真正的 lip-sync。

**音频管线**：

- `AudioManager` 单例，**严格串行队列**（`src/lib/audio-manager.ts:285,253-272`）
- 三层去重：内容寻址缓存 Map / `inFlight` 合并并发同 ID 请求 / `playbackId` 播放去重
- **首段先等 TTS 就绪（≤15s）才放字幕**，后续段落只串行预取、不阻塞字幕（`src/hooks/useDayPhase.ts:166-193`）
- **打字机速度由音频时长反推**：`durationMs / text.length` clamp 到 10–60ms（`src/app/page.tsx:660-679`）——字幕节奏自动贴合语音长度
- 旁白是**另一条独立管线**：预生成 MP3 存于 `public/audio/narrator/{zh,en}/`（各 29 个文件），由阶段类直接 `await` 调用
- BGM 不走 AudioManager，是 `page.tsx` 自持的 `HTMLAudioElement` + 手写 rAF 交叉淡入淡出

**一个已确认的缺陷**：`src/app/page.tsx:673` 调用 `makeAudioTaskId(voiceId, text)` 时漏传第三个参数 `provider`（默认 `"minimax"`），而 `useDayPhase.ts:157` 传了真实 provider。因此 TokenPay/TokenDance 用户查不到音频时长缓存，打字机速度退化为固定 25ms（见缺陷 B-5）。

### 9.4 昼夜"眨眼转场"的实现

不是 CSS 动画类，而是**三段式遮罩 + 可取消调度器**：

```ts
// src/app/page.tsx:67-71
DAY_NIGHT_BLINK = { closeMs: 360, holdMs: 120, openMs: 620 }
```

- `scheduleDayNightBlink` 用 token + timeout refs 保证可取消（`page.tsx:244-278`）
- 夜晚需等仪式字幕播完（`RITUAL_CUE_DURATION_SECONDS = 2.2`）才闭眼（`page.tsx:616-629`）
- **闭眼瞬间才切主题**：`document.documentElement.setAttribute("data-theme", ...)`（`page.tsx:225`）
- 视觉层是两个伪元素模拟上下眼睑：`translate3d(0, calc((-100%) + (var(--wc-lid) * 100%)), 0)` + 羽化 `box-shadow`（`globals.css:1311-1345`）

`globals.css` 共 4499 行 / 96KB，含 **54 个 `@keyframes`**、282 个 `.wc-*` 组件类、307 处 `var(--…)`。其中约 15 个 `wc-night-*` 关键帧专门做夜间行动特效（爪痕、狼影、雾旋、预言家透镜、猎人冲击波等，`globals.css:3682-4003`）。**"复古氛围"几乎完全靠手写 CSS 堆出来**，没有用任何动画库做这些特效。

---

## 十、工程质量体系

### 10.1 测试规模

- **51 个测试文件**（`src/` 28 个 + `server/` 20 个 + `scripts/` 1 个 + 其他），约 **200 个测试用例**
- `src/game/phases/context-regressions.test.ts`（600+ 行）：上下文证据矩阵，中英 × 普通/原神四组合
- `src/game/phases/factual-context.test.ts`：事实文案的语义正确性
- `server/*.test.ts` 分两类：
  - **源码文本契约测试**（读路由/SQL 源码并正则匹配）。例如 `credit-idempotency-contract.test.ts:6-20` 断言 plpgsql 体内子句顺序必须是 `lock < 重放查询 < 重放返回 < 余额检查 < 扣减 < 插入`
  - **行为级测试**（带 mock fetch）：TokenPay 的 AES 换密钥解密失败、status URL 白名单、重试策略、流 `DONE`/提前取消；爱猹收的 scheme/二维码白名单、沙箱密钥拒绝、409→`insufficient_quota`

**两个必须说明的验证边界**：

1. **没有任何测试连真实数据库**（逐一核实为真）。grep 命中 DB 符号的 4 个文件都是假阳性——`credit-consume-route-contract.test.ts:37` 是在对源码文本做正则时匹配到 `supabaseAdmin.rpc(`；`tokenpay-service.test.ts:5`、`watcha-oauth-callback.test.ts:99`、`watcha-pay-route.test.ts:7` 只是赋 dummy 环境变量让模块能 import。**plpgsql 的正确性只有文本断言。**
2. **9 个服务端契约测试不被任何 npm script 调用**（见缺陷 A-9），仓库也没有 CI workflow。也就是说这些契约的"持续"验证依赖发版前有人记得手工跑。

### 10.2 三种审计脚本（这是最有特色的部分）

| 脚本 | 是否联网 | 作用 |
|---|---|---|
| `scripts/single-player-context-audit.ts`（26.5KB） | **否** | 跑**生产提示词路径** + 桩传输，断言 `externalNetworkCallCount === 0`，全阶段 × 全角色覆盖，产物中不含密钥/Bearer（`:289,704-716,733-739`），输出指纹化报告到 `dry-runs/`。配套的 `single-player-context-audit.test.ts:24-53` 是**行为级**测试：断言 11 名玩家、`aiLogCount >= 90`、`transportRequestCount >= aiLogCount`、`phaseRoleCoverage.length >= 79`、两次运行指纹一致、递归脱敏（伪 `sk-…`/JWT/`abc.def.ghi` 均被替换为 `REDACTED`） |
| `scripts/live-single-player-context-audit.ts`（21KB） | 是 | 拦截 `/api/chat` 调用并转发到真实 TokenDance 网关，**拒绝任何其他网络调用**（`:271-274,310-315,349`） |
| `scripts/live-character-generation-audit.ts`（18.7KB） | 是 | 角色生成实测，支持针对真实部署的应用路径模式 |

三档设计对应三种验证强度：**离线可复现 / 真实模型单点 / 端到端实测**。这是把"提示词这类不可单测的东西"变得可回归的实用方案。

### 10.3 开发规范文档的罕见质量

`docs/单人上下文约束.md`（95 行）不是普通文档，而是一份**故障档案**。它的结构是"原因 → 当前约束 → 验证位置"的三列表格，共 13 行，每一行都对应一次真实事故。文末还包含两次真实对局的完整复盘，**包括明确标注失败的部分**：

> 「浏览器空间后来丢失，这局没有取得终局证据，**不能记为完整通过**。」（`:72`）

> 「边界：这局在第 2 天讨论前结束，**不能据此证明长局语义一致性全部通过**；本局没有自然触发格式恢复、警徽多候选复投、白痴免死和白狼王实际自爆，也没有逐段音频听觉验收。」（`:95`）

并且明确区分了验证强度：

> 「测试调用生产提示词、阶段结算、Hook、队列和解析代码，模型与音频返回使用可控桩…**通过测试表示上下文传递与状态约束成立，不代表模型在每一局都会作出正确推理**。」（`:66`）

这种"把测试通过的含义边界写清楚"的做法，在开源项目里相当少见。

### 10.4 模型输出的观测

`src/lib/ai-logger.ts` 记录每条请求的完整 messages / 原始响应 / 解析结果 / 缓存命中情况。但**仅在开发环境**启用（`NODE_ENV !== "production"`，`:14-18`），localStorage 上限 800 条。生产环境只保留计数（provider/model/耗时/tokens），且是 best-effort（失败被吞掉，见缺陷 B-3）。

---

## 十一、商业化与服务端

### 11.1 LLM 网关

所有对局 LLM 流量经过**唯一收口** `/api/chat`（`src/app/api/chat/route.ts`，44.7KB）：

| Provider | 端点 |
|---|---|
| ZenMux | `https://zenmux.ai/api/v1/chat/completions` |
| DashScope（百炼） | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` |
| TokenDance | `{TOKENDANCE_BASE_URL}/chat/completions`（默认 `https://tokendance.space/gateway/v1`） |

**注意**：`.env.example` 与 `AGENTS.md`/`CLAUDE.md` 都描述了第四个 provider `NewAPI`，但 **`src/` 中 `NEWAPI` 零引用**。文档已过期（缺陷 A-5）。

关键工程点：
- 批量模式：`body.requests` ≤ 12，`Promise.all` 并行（用于上警报名这类"多人同时决策"）
- 流式：SSE 直通 + `trackSseAttempt` 统计，**只有收到 `[DONE]` 才算成功**（`src/lib/sse-attempt-tracker.ts:11-13`）
- 超时：路由 `maxDuration = 300`（注释说明是为 Vercel Fluid Compute）；上游 60s AbortController；客户端 45s 流空闲超时
- 重试**只在客户端**（`src/lib/llm.ts`）：流式 4 次尝试；可重试状态码 `{429,500,502,503,504}`，但 **TokenPay 路径只重试 429**，因为上游没有幂等键（`:244-245,404-423`）——这是个有意识的保守选择

### 11.2 自带密钥（BYO Key）

用户可在浏览器填入 ZenMux / DashScope / TokenDance 的 API Key，**明文存 localStorage**（`src/lib/api-keys.ts:9-11`），并在每次调用时作为请求头重新发送（`src/lib/llm.ts:110-117`）。服务端不存储、不加密。

模型来源是一个状态机：`project`（用项目额度）/ `tokenpay`（用 TokenPay 连接）/ `custom`（用户自己的 key），带一个"显式选择"标志以避免旧的自动写入值覆盖实时 TokenPay 链接（`api-keys.ts:174-220`）。

### 11.3 积分与支付

| 路径 | 状态 |
|---|---|
| 项目额度（project credit） | 主路径，Supabase RPC 单事务扣减 |
| TokenPay / TokenDance | 接入中，AES-256-GCM 加密存储用户凭证 |
| 爱猹收 Watcha Pay | 正式接入，按局额度 |
| Stripe | **已停用**：`/api/stripe/payment-link` 恒返回 410 `payment_method_retired`；但 webhook 仍存活以处理历史订单（`server/legacy-payments.test.ts:4-19` 锁定该行为） |

**开局扣费的幂等设计**（`supabase/migrations/20260901132040_idempotent_game_start_requests.sql:65-168`）值得记录，它是全项目最严谨的一段 SQL：

```
SELECT ... FOR UPDATE  (锁定额度行)
  → 校验人数 + 64位十六进制指纹
  → 按 (user_id, start_request_id) FOR UPDATE 查重放   ← 在余额检查之前
  → 指纹/来源不一致则 idempotency_conflict
  → 扣减 1 + 同事务插入 session 行
```

配套约束：部分唯一索引 `(user_id, start_request_id) WHERE NOT NULL`；CHECK 约束保证幂等列"全有或全无"。客户端按用户维度持久化 `(requestId, fingerprint)` 24 小时，只重试 408/425/429/5xx（`src/lib/game-start-idempotency.ts`）。

存储不可用时**抛错并返回 503，不用内存态兜底**（`api-keys.ts:37-40`、`useCredits.ts:169-187`）——这是一个正确的选择：拿不到幂等保证时，宁可拒绝扣费。

### 11.4 数据模型

13 张表 + 1 个视图（`src/types/database.ts`）：`user_credits`、`referral_records`、`campaign_daily_quota`、`demo_config`、`sponsor_clicks`、`redemption_codes`/`records`、`game_sessions`、`game_session_events`、`custom_characters`、`payment_transactions`、`tokenpay_connections`/`oauth_flows`。

**服务端只存元数据与计数**：`player_count`、`difficulty`、`winner`、`lifecycle_status`、`duration_seconds`、`ai_calls_count`、`ai_*_tokens`、`used_custom_key`、`credit_authorized`、`model_used` 等（`database.ts:219-247`）。**不存提示词、不存响应、不存角色分配、不存牌局状态。**

牌局状态完全在客户端 localStorage。这个选择的后果见缺陷 B-1。

### 11.5 认证

- **Supabase Auth**（邮箱密码 + OAuth），服务端用 `supabaseAdmin.auth.getUser(token)` 验证
- **观猹 OAuth2**（可选）：`state` 为 16 字节随机 hex 存于 httpOnly/Secure/SameSite=Lax cookie，600s TTL，回调逐字节比对；**无 PKCE**（`src/lib/watcha-oauth.ts:25-34`）。回调不返回 provider token，而是重定向到 Supabase 的 `verify?token=…&type=magiclink` 完成会话建立
- **TokenPay OAuth**：使用**真正的 PKCE S256**（48 字节 verifier，state 做 sha256 后入库，verifier 用 AES 加密，10 分钟 TTL，原子单次消费）

有趣的是同一个项目里两套 OAuth 的安全强度差异明显，说明 TokenPay 那套是后写的、吸取了教训。`docs/watcha-login-recovery.md` 记录了一个影响 11,070 名生产用户的登录故障及其修复。

---

## 十二、内容与 SEO 策略

这是一个技术报告里容易被跳过、但对理解项目全貌很重要的部分。Wolfcha 有一套**刻意的程序化 SEO 策略**，且落地存在明显断层。

### 12.1 手写引导内容：19 个页面

`src/app/guides/` 下 18 篇文章 + 1 个 hub，全部手写、**没有共享模板**（不存在 `GuidePage` 之类的 helper）。体量从 79 行（`how-to-bluff`）到 813 行（`werewolf-rules`）不等。

关键词簇形态：
1. 规则/阶段：`werewolf-rules`、`werewolf-night-phase`、`werewolf-day-phase`
2. 新手意图：`werewolf-for-beginners`（带 HowTo schema）
3. 按角色战术：`seer-strategy`、`witch-strategy`、`hunter-strategy`、`guard-strategy`、`how-to-win-as-*`
4. 通用技巧：`how-to-spot-a-liar`、`how-to-bluff`、`how-to-build-trust`、`how-to-control-the-vote`、`common-werewolf-mistakes`
5. 体裁与竞品：`werewolf-vs-mafia`、`social-deduction-games`
6. **"一个人玩"长尾**：`how-to-play-werewolf-with-ai`

### 12.2 程序化落地页：4 族 54 条记录，只有 25 条可达

| 族 | 数据文件 | 条数 | 路由 | 索引状态 |
|---|---|---|---|---|
| 角色页 | `roleLandingData.ts:8` | 5 | ✅ `/roles/[role]`（`dynamicParams=false`） | 索引 + FAQPage |
| 模型页 | `modelLandingData.ts:17` | 10 | ✅ `/models/[model]` | **noindex** |
| 模型 A/B 对比 | `modelComparisonData.ts:17` | 10 | ✅ 同上（共路由） | **noindex** |
| Solo 意图页 | `soloLandingData.ts:21` | 13 | ❌ **无路由文件** | 在 sitemap |
| 体验/形态页 | `experienceLandingData.ts:16` | 8 | ❌ 无路由 | 在 sitemap |
| 竞品对比页 | `gameComparisonData.ts:15` | 8 | ❌ 无路由 | 在 sitemap |

**这 29 条无路由的 slug 被写进了 sitemap**（`src/app/sitemap.ts:89-108`），且 `src/app/[slug]/` 目录下**只有 `analysis/page.tsx`**，没有 `page.tsx`。也就是说：**项目向搜索引擎提交了 29 个 404**。

不过需要精确区分故障面。这 29 条 slug 中，只有 **4 条**被真实存在的路由页面内链引用，会渲染成用户可见的死链：

| 死链 slug | 被哪些活页面引用 |
|---|---|
| `play-werewolf-alone` | `ai-werewolf/page.tsx`、`features/page.tsx` |
| `werewolf-game-with-ai-opponents` | `ai-werewolf/page.tsx`、`features/page.tsx`、`how-to-play/page.tsx`、`guides/werewolf-rules/page.tsx`、`guides/how-to-play-werewolf-with-ai/page.tsx`、`components/seo/landing/roleLandingData.ts`（属真实存在的 `/roles/*` 路由） |
| `werewolf-game-browser` | `features/page.tsx` |
| `free-werewolf-game-online` | `features/page.tsx` |

其余 **25 条**（8 条体验页 + 8 条竞品对比页 + 9 条 solo 页）只出现在 `soloLandingData.ts` / `soloLandingDataContent.ts` / `experienceLandingData.ts` / `gameComparisonData.ts` 这四个**自身路由不存在、数据永不渲染**的文件里，因此不会产生可见死链。

真正的损失是：这 25 条落地页内容（含 `soloLandingDataContent.ts` 的整套 62KB 文案）是**已写好但永远不可达的死代码**。修复优先级应是"补 `src/app/[slug]/page.tsx` 或把 29 条从 sitemap 摘掉"，4 条站内死链是同一次修复的附带收益。

模型族被主动 de-index（`robots:{index:false}`，`models/[model]/page.tsx:123,159`；`ai-models/page.tsx:21`），推测是商标风险规避。但 `/ai-models` 也因此缺席 sitemap。### 12.3 结构化数据与 i18n 的技术债

- 全站 JSON-LD：`WebSite` + `VideoGame` + `Organization`（`layout.tsx:132-134`）
- 但 `JsonLd` 组件用 `next/script strategy="afterInteractive"`（`JsonLd.tsx:8-16`）→ **结构化数据由客户端注入而非服务端 HTML**
- 19 个引导页中 **17 篇发 `Article`**，但其中只有 **6 篇**写了完整字段（author/publisher/datePublished）：`werewolf-rules`、`werewolf-night-phase`、`werewolf-day-phase`、`seer-strategy`、`witch-strategy`、`how-to-win-as-werewolf`（`grep -rl datePublished src/app/guides/*/page.tsx` 实测）；其余 11 篇是 headline-only stub。`werewolf-for-beginners` 只发 `HowTo` 不发 `Article`（唯一的 HowTo-only 页）；`FAQPage` 在引导页中**仅** `werewolf-rules/page.tsx` 一篇
- **`public/og-image.png` 被引用 27 次但文件不存在**（`layout.tsx:59,69`、`JsonLd.tsx:28` 等）→ 所有 OG/Twitter 卡片实际无图（缺陷 A-4）
- **全站没有任何一条有效的 hreflang 声明**。全站仅有两处 `alternates.languages`，两处都坏：`layout.tsx:76-82` 声明 `{en:"/en", "zh-CN":"/zh"}` 但 `/en` 既无路由也无 rewrite（指向 404）；`landing/page.tsx:42-45` 的 `en` 与 `zh-CN` **值完全相同**（都指向 `https://wolf-cha.com/landing`，自指）
- `en.json:303` 有 `seo.nav.blog: "Blog"` 导航文案，但**不存在 `src/app/blog` 路由**

### 12.4 i18n 实现：有意的"无路由段"方案

next-intl 4.7，但**没有 `[locale]` 路由段**。`next.config.ts:12-18` 只有三条 rewrite：

```ts
{ source: "/zh",  destination: "/" },
{ source: "/zh/", destination: "/" },
{ source: "/zh/:path*", destination: "/:path*" },
```

即**同一棵路由树挂在两个 URL 空间下**，`/zh` 前缀由 `middleware.ts:22-43` 手写协商（cookie `wolfcha.locale` → 307 跳转 `/zh{path}`；否则嗅探 `Accept-Language`）。

消息文件：`zh.json` 2420 行 / 116KB，`en.json` 2338 行 / 118KB。我核实了两边**顶层命名空间 61 个、叶子键 1692 个，完全一致（0 缺 0 多）**。

一个有意思的细节：**连 LLM 提示词本身都做了双语**（`prompts`、`promptUtils` 是最大的两个命名空间之一），并由 `src/lib/game-texts.ts` 生成全部系统文案与正则。也就是说这个项目把提示词当作产品文案来管理。

代价：两个 JSON 被静态 import 并整体传给客户端 Provider（`messages.ts:1-2`），约 234KB 未拆分；SSR 的 `<head>` 元数据永远用英文生成（`layout.tsx:12,14-93` 在模块作用域用 `getMessages(defaultLocale)`）。

---

## 十三、已核实的缺陷与风险清单

以下全部经源码核实。按严重度分级。

### A. 会导致功能不可用或构建失败

| ID | 问题 | 证据 |
|---|---|---|
| **A-1** | **Dockerfile 已失效**：它复制 `/app/.next/standalone` 并运行 `node server.js`，但 `next.config.ts:8-10` 已显式移除 `output: "standalone"`（注释说明"不需要，且会让部署变大"）。因此镜像不含服务端入口，构建出的镜像无法运行 | `Dockerfile:58,69` vs `next.config.ts:8-10` |
| **A-2** | **29 条 sitemap URL 无对应路由**（solo 13 + 体验 8 + 对比 8），等于向搜索引擎提交 29 个 404；`src/app/[slug]/` 只有 `analysis/page.tsx`，没有 `page.tsx` | `sitemap.ts:89-108` + `find src/app/[slug]` 实测 |
| **A-3** | **4 条站内链接指向 404**（A-2 的 slug 中被活页面引用的部分）：`play-werewolf-alone`、`werewolf-game-with-ai-opponents`、`werewolf-game-browser`、`free-werewolf-game-online`，集中在 `/features`(4) 与 `/ai-werewolf`(2) 两个**高优先级活页面**。另 25 条落地页文案是纯死代码 | 逐 slug × 逐文件精确匹配实测 |
| **A-4** | **`public/og-image.png` 不存在但被引用 27 次** → 所有社交分享卡片无图 | `ls public/og-image*` 无结果；`grep -rn "og-image.png" src/` 计数 27 |
| **A-5** | **文档漂移**：`AGENTS.md` / `CLAUDE.md` / `.env.example` 都描述了 NewAPI provider，但 `src/` 中 `NEWAPI` **零引用**；同时文档描述的 Docker standalone 部署路径也已失效 | `grep -rn NEWAPI src/` 无命中；`.env.example:37-38` |
| **A-6** | **许可证不一致**：`LICENSE` 文件是 **Apache-2.0**（GitHub API 也识别为 Apache-2.0），但 `README.md:85`、`README.zh.md:85`、`README.en.md:117` 三处都写 MIT。这对使用者是实质性的法律歧义 | 已逐一读取核实 |
| **A-7** | **`pnpm-workspace.yaml` 未完成迁移**：`allowBuilds` 下是字面占位文本 `'@parcel/watcher': set this to true or false`，同时 `sharp` 被放入 `ignoredBuiltDependencies` → 自托管构建可能缺 native 图片优化器 | `pnpm-workspace.yaml` |
| **A-8** | **数据库 schema 无法从仓库重建**：两个临时迁移位于 `scripts/sql/` 而非 `supabase/migrations/`，命名约定不一致（`20260320_…` vs `20260827…`）。其中 `scripts/sql/20260320_game_sessions_user_id_text.sql:3-17` 删除了 `game_sessions → auth.users` 的外键、把 `user_id` 改为 `text`，并加约束 `user_id ~ uuid正则 OR user_id LIKE 'guest_%'`。最早的 game_sessions/积分表与基础表 RLS 策略完全不在仓库内 | `scripts/sql/*` 实测；`supabase/migrations/` 仅 5 个文件 |
| **A-9** | **没有聚合测试脚本、没有 CI**：`package.json` 只有 `test:single-player-context` 与 `test:tokenpay` 两个测试脚本，二者都是硬编码文件列表。以下 9 个服务端契约测试**不被任何脚本调用**、只能手工运行：`chat-observability-contract`、`credit-consume-route-contract`、`credit-idempotency-contract`、`game-session-observability-contract`、`game-session-route-contract`、`legacy-payments`、`watcha-oauth-callback`、`watcha-pay`、`watcha-pay-route`。仓库无 `.github/`，无任何 CI workflow → 积分/支付/可观测性契约可静默腐化 | `package.json` scripts 实测；`ls -a .github` 不存在 |

### B. 状态一致性与健壮性

| ID | 问题 | 证据 |
|---|---|---|
| **B-1** | **牌局状态完全信任客户端**：`GameState`（含所有角色身份）存在 localStorage，服务端不校验、不存牌局。可篡改角色/改胜负/伪造战绩，服务端只能数 AI 调用次数。消费闸门仅为"24 小时内活跃且有 `credit_authorized` 的 session"，且每次授权调用都会刷新该窗口 | `src/store/game-machine.ts:301-374`（客户端持久化）；`src/lib/api-auth.ts:66-108,93-97` |
| **B-2** | **RLS 无法从仓库验证**：全仓**没有任何 `CREATE POLICY`**；只有 3 张表在仓库内有 RLS 配置（`tokenpay_connections`、`tokenpay_oauth_flows`、`game_session_events`）。所有服务端代码用 service-role 客户端绕过 RLS。基础表的策略只存在于线上项目 | `supabase/migrations/*` 实测；`src/lib/supabase-admin.ts:7-9` |
| **B-3** | **成本核算可能静默丢失**：服务端观测是 best-effort，路由层 `recordAttempt` 吞掉异常（`chat/route.ts:140-142`），helper 有 2s 超时（`server-game-observability.ts:6`），无告警 | 同左 |
| **B-4** | **角色生成无兜底**：任一 Stage A 校验失败或 Stage B 批次失败即向上抛，直接卡住开局，没有模板降级路径 | `src/lib/character-generator.ts:568-570,743-747` |
| **B-5** | **打字机速度对 TokenPay 用户退化**：`page.tsx:673` 调 `makeAudioTaskId(voiceId, text)` 漏传 `provider`（默认 `"minimax"`），而 `useDayPhase.ts:157` 传了真实 provider → 缓存键不匹配，查不到音频时长，速度退化为固定 25ms | `src/app/page.tsx:673` vs `src/hooks/useDayPhase.ts:157` |
| **B-6** | **积分/支付路径的边界处理较脆弱**：TokenPay 充值的 `amount` 由客户端提供（1..100000）；Watcha Pay 超时采用"保留未授权行 + 返回 503"策略（这个是对的），但整体缺少速率限制 | `src/app/api/tokenpay/payment-sessions/route.ts:15-28` |

### C. 安全

| ID | 问题 | 证据 |
|---|---|---|
| **C-1** | **`/api/validate-key` 无鉴权**（全文无 `authenticateRequest` 引用），且它会对调用者提供的 key 直接探测上游并**回显上游状态与错误信息** → 一个开放的探测 oracle | `grep -c authenticateRequest src/app/api/validate-key/route.ts` = **0** |
| **C-2** | **SSRF 形态的任意服务端请求**：请求头 `x-tokendance-base-url` 被**原样使用**（只追加 `/chat/completions`），无 HTTPS/白名单校验。同一个头在 `/api/chat` 也被接受（`:823,892`）。对比之下 TokenPay 网关 URL 走 `normalizeConfiguredUrl` 做了协议校验 | `validate-key/route.ts:118-123,330-335`；`chat/route.ts:823,892`；`tokenpay-config.ts:4-19` |
| **C-3** | **自带密钥明文存 localStorage**，且每次调用作为请求头重发；另有一份"上次验证成功的 key"副本（`wolfcha_validated_*`）使暴露面翻倍 | `src/lib/api-keys.ts:9-11,22-24`；`src/lib/llm.ts:110-117` |
| **C-4** | **无任何速率限制**：middleware 只做语言路由；`/api/chat` 单次批量可触发 12 次上游调用；`/api/sponsor/click` 是无鉴权无限制的插入端点 | `src/middleware.ts:5-46`；`chat/route.ts:43` |
| **C-5** | **Demo 模式的访客身份可猜测**：`guest_` + UA/语言/核心数/屏幕/时区的 **32 位 JS hash**，`isGuestUser` 只做前缀检查；`authenticateRequest` 也接受访客身份用于 TokenPay 支付/兑换与 vote-batch | `src/lib/demo-mode.ts:11-56,72-74`；`api-auth.ts:23-29` |
| **C-6** | **观猹 OAuth 无 PKCE**（仅 `state` 校验）。同一项目里 TokenPay OAuth 用了完整 PKCE S256，说明这是历史遗留而非有意设计 | `src/lib/watcha-oauth.ts:25-34` vs `src/lib/tokenpay.ts:241-311` |

### D. 代码健康度

| ID | 问题 | 证据 |
|---|---|---|
| **D-1** | **巨型文件**：`useGameLogic.ts` 2357 行、`game-master.ts` 2036 行、`globals.css` 4499 行、`DialogArea.tsx` 1912 行、`DevConsole.tsx` 2036 行、`page.tsx` 1758 行、`SmartJumpManager.ts` 1586 行 | 实测 `wc -l` |
| **D-2** | **规则逻辑重复实现**：计票两份（`game-master.ts:486-532` vs `VotePhase.ts:205-227`）；夜间结算两份（`useSpecialEvents.ts:177` vs `SmartJumpManager.ts:1352,1504`）；票型文案两份（`VotePhase.ts:229-273` vs `useBadgePhase.ts:90-124`）；阶段顺序三处维护，且 `SmartJumpManager.ts:16-18` 的 `PHASE_ORDER` 漏了 `DAY_PK_SPEECH` 与 `WHITE_WOLF_KING_BOOM` | 同左 |
| **D-3** | **死代码**：`src/hooks/useGameLogicRefactored.ts` **0 字节**；`AsyncFlowController.pause/resume/isPaused` 零调用者；`handleHumanHunterShoot` 直接 `return {} as GameState` 但仍被导出（`useSpecialEvents.ts:168-174`）；`MAX_REVOTE_COUNT`（`game-constants.ts:13`）零引用；`/api/vote-batch` 无站内调用者；`audio-manager` 的 `prefetchTasks`/`isCached` 未被调用 | 逐一 grep 核实 |
| **D-4** | **强约束只写在文档里**：`VALID_TRANSITIONS` 存在，但非法转换只 `console.warn` 仍会写入（`game-machine.ts:1049-1062`）；`PhaseManager` 单实例被 4–5 个 phase 共用，`DaySpeechPhase` 靠可变字段 `isMovingToNextSpeaker` 防重入（`:48,456-458`），仅在单局串行前提下成立 | 同左 |
| **D-5** | **`PhaseManager` 被 new 了三次**（`game-master.ts:67`、`useGameLogic.ts:137`、`DevConsole.tsx:45`），阶段注册表因此有三份运行时实例 | 同左 |
| **D-6** | **狼人夜晚被简化**："首个狼人决定，其余自动跟票"（`NightPhase.ts:205-217`）——这不符合线下"狼队讨论"的玩法，是一个玩深度上的取舍 | 同左 |
| **D-7** | **前端 `page.tsx` 是纯 prop-drilling 外壳**：`useAtom` 被 import 但全文未使用（`page.tsx:37`），所有状态经 `useGameLogic()` / `useSettings()` 以 props 下传 | 同左 |
| **D-8** | **条件 Hook 写法**：`TalkingAvatar.tsx:36-41` 的 `useMemo` 位于提前 `return` 之后 | 同左 |

### E. 提示词与模型层

| ID | 问题 | 说明 |
|---|---|---|
| **E-1** | **提示词膨胀是设计选择**：对局路径**不裁剪历史**，第 N 天每个座位的输入是 O(天数 × 人数) 的完整逐字稿。源码注释明说"The current default model has enough context for complete game history, so do not trim old speeches here"（`prompt-utils.ts:450-453`）。对局路径唯一显式的长度预算是日总结的 15000 字符截断（`game-master.ts:655`） | 换更强模型的前提假设，模型降级时会崩 |
| **E-2** | **缓存脆弱**：可缓存头是一整条含 persona 的字符串，任何 i18n 文案改动都会击穿 1 小时缓存；断点上限 4（`prompt-utils.ts:1101`） | 所以才需要 `DEEPSEEK_STABLE_PROMPT_CACHE_PREFIX` 这种手工前缀缓存 |
| **E-3** | **模型语义错误仍在**：作者自己记录真实对局中仍出现"查验立场短暂反转"、"票型泛称全票"、"7 人存活时猜 4 狼"、"用了救药被说成没药了"（`docs/单人上下文约束.md:62,94`） | 上下文修复 ≠ 语义正确 |
| **E-4** | **兼容分支在累积**：`messages` 包装、会话封套、无外层数组、旧 `voteHistory`/`badge.history`、缺 `resultsAnnounced` 的旧存档——每一支都是新的泄露面 | `night-visibility.ts:9-12` 等 |
| **E-5** | **`bannedDiscussion` 类矛盾**曾导致系统提示压制合法证据，这类"提示词内部矛盾"没有静态检查 | `docs/单人上下文约束.md:35` |

---

## 十四、对同类项目的可迁移经验

按可迁移价值排序。

### 14.1 把"信息可见性"做成单一闸门 + 取值来源约束

不要试图给每个玩家做一份脱敏数据副本——那会在每个新字段加入时产生新的泄露点。Wolfcha 的做法是：

1. **唯一分发点**：所有提示词经 `resolvePhasePrompt(phase, state, player)`，无旁路
2. **唯一闸门**：`buildRolePrivateInfo` 按角色分支，不认识的角色 `return null`（不生成，而非过滤）
3. **取值来源约束**：公开块禁止读 `state.players` 这类全量状态，只能读"主持人会当众念出来的东西"
4. **双向断言**：同时测"应出现"和"不应出现"

第 4 点是这套方案能长期维持的原因。只有"不该泄露的没泄露"这一个方向的测试，会漏掉"该给的没给"，而后者会表现为"AI 变蠢"这种难以归因的症状。

### 14.2 异步身份用"请求 ID + 位置"，不要用内容

一旦异步链路变长（流式 → 解析 → 合成 → 播放 → 落库），基于文本内容做去重/缓存一定会错。Wolfcha 用三种身份分工：

- **位置身份** `${requestId}:${index}` 管字幕与历史的幂等（相同文字可以是两个有意的段落）
- **内容身份** `${provider}::${voiceId}::${text}` 管 TTS 合成缓存（同一句话只合成一次）
- **播放身份** `${requestId}:${index}` 管队列去重（同一句话可以播放两次）

### 14.3 把不可单测的东西变成可回归的三档审计

提示词质量无法写单元测试。但可以：

1. **离线档**：跑生产提示词路径 + 桩传输，断言零网络调用 + 全矩阵覆盖 + 无密钥泄漏 → 快、免费、可进 CI
2. **真实单点档**：拦截 LLM 调用转发真实网关，**拒绝任何其他网络调用** → 验证真实模型格式服从性
3. **端到端档**：真实部署 + 真实对局 → 记录调用次数、逐项比对页面与持久化历史

关键是**明确标注每一档证明了什么、没证明什么**。

### 14.4 用"刻意缺陷"替代"更长的提示词"

想要 AI 表现得像人，方向不是让模型更聪明，而是让它**有稳定且可复现的偏差**。`mistakePattern` / `PlayerMind` 这类结构化字段，比在提示词里写"请表现得像真人一样有缺点"有效得多——因为前者是**生成时随机、对局中稳定**的，后者是每次采样都重新掷骰子。

### 14.5 采样参数承担多样性，提示词承担正确性

温度阶梯（0.1 复盘 / 0.4 决策 / 0.7 结构 / 1.0 发言 / 1.2 角色生成）比"统一温度 + 超长提示词"更可控。特别是把**发言**与**决策**分成两个温度档：性格影响表达，不污染理性判断。

### 14.6 "失败即暂停"优于"降级兜底"

对于"AI 在说话"这类核心体验，屏幕上出现的每一句话都必须确实由模型生成且完整。遇到坏格式时重试一次，仍失败则停下来给用户重试入口，**绝不把错误信息或原始 JSON 写成角色台词**。这个取舍牺牲流畅性，换来的是一致性。

### 14.7 把故障档案写成文档

`docs/单人上下文约束.md` 的价值高于任何架构文档。它的结构（原因 → 约束 → 验证位置）可以直接复制到任何长期维护的项目里。

---

## 十五、附录：数据与规模

### 15.1 代码分布

| 目录 | 文件数 | 行数 |
|---|---|---|
| `src/components` | 84 | 23,347 |
| `src/lib` | 75 | 17,539 |
| `src/app` | 60 | 15,482 |
| `src/hooks` | 19 | 6,052 |
| `src/game` | 12 | 2,620 |
| `server` | 20 | 2,300 |
| `src/store` | 3 | 1,318 |
| `src/types` | 5 | 1,160 |
| `scripts` | 7 | 2,548 |
| **合计（TS/TSX）** | | **≈72,680** |

### 15.2 最大的文件（Top 12）

| 行数 | 文件 |
|---|---|
| 4499 | `src/app/globals.css` |
| 2420 / 2338 | `src/i18n/messages/zh.json` / `en.json` |
| 2357 | `src/hooks/useGameLogic.ts` |
| 2036 | `src/lib/game-master.ts` |
| 2036 | `src/components/DevTools/DevConsole.tsx` |
| 1912 | `src/components/game/DialogArea.tsx` |
| 1788 | `src/lib/game-analysis.ts` |
| 1758 | `src/app/page.tsx` |
| 1586 | `src/lib/SmartJumpManager.ts` |
| 1143 | `src/store/game-machine.ts` |
| 1132 | `src/lib/prompt-utils.ts` |
| 1115 | `src/lib/llm.ts` |

### 15.3 模型注册表

**`ALL_MODELS`**（24 条，自定义密钥时可选，`src/types/game.ts:364-386`）：

| Provider | 模型 |
|---|---|
| dashscope | deepseek-v3.2 |
| zenmux | gemini-3.1-flash-lite、deepseek-v3.2、deepseek-v4-flash、gemini-3-flash-preview、gpt-5.2-chat、claude-haiku-4.5、claude-sonnet-4.5、claude-opus-4.5、grok-4.5、glm-4.7、minimax-m2.1 |
| tokendance | minimax-m2.7、deepseek-v4-pro、deepseek-v4-flash-0731、deepseek-v4.1-flash、glm-5.3-flash、qwen3-max、glm-5、kimi-k2.5、deepseek-v3.2 |

**`DEFAULT_MODEL_CONFIG`**（`src/types/game.ts:325-335`）：

```
generator（角色生成/日总结）  = zenmux / google/gemini-3.1-flash-lite
summary（总结）              = tokendance / deepseek-v4.1-flash
review（赛后复盘）            = tokendance / deepseek-v4.1-flash
validation.zenmux           = google/gemini-3.1-flash-lite
validation.dashscope        = deepseek-v3.2
validation.tokendance       = minimax-m2.7
```

内置玩家模型池 `BUILTIN_PLAYER_MODELS` 仅 1 条（`deepseek-v4.1-flash`），且 `AVAILABLE_MODELS` 与它相同——即**不使用自带密钥时，所有 AI 座位都用同一个模型**。多个模型同台竞技需要用户自带密钥（这也解释了 SEO 里那一族模型对比页的商业动机）。

**一个已确认的不一致**：`generateGameAnalysis(state, model?)` 默认取 `getSummaryModel()`（`game-analysis.ts:1379`），但唯一调用方传的是 `getReviewModel()`（`src/hooks/useGameAnalysis.ts:50`）——实际是 REVIEW 模型驱动了总结与复盘两步，`SUMMARY_MODEL` 配置在复盘路径上不生效。

### 15.4 关键路径长度（一局 11 人）

- AI 调用：文档记录的真实对局为 **88 次**（11 人局，第 4 天结束）与 **47 次**（11 人局，第 2 天结束）——`docs/单人上下文约束.md:49,89`
- 其中发言调用：28 次 / 12 次
- 外部博客估算：整局 30–60 次模型调用，token 消耗 8k–15k（**未在源码中核实**）

### 15.5 未在本次分析中验证的事项

| 事项 | 原因 |
|---|---|
| 是否可构建 / 测试是否真的通过 | 未执行 `pnpm install` / `pnpm build` / `pnpm test:single-player-context`；文档声称 118 项通过（`docs/单人上下文约束.md:45`），本报告按文档引用并标注 |
| 线上 RLS 策略 | 仓库内无基础表 DDL（仅 5 个受版本控制的迁移 + 2 个 `scripts/sql/` 临时迁移） |
| `/en` 是否真的 404 | 只有源码推断，未实际抓取 |
| 29 条 slug 是"已下线"还是"尚未上线" | 快照无 `.git`，无构建产物 |
| 模型族 noindex 是有意（商标）还是疏漏 | 两种解释都与代码自洽 |
| 音频的实际听感 | 未做逐段听觉验收 |
| 真实支付/兑换流程 | 上游文档自述未用真实资金验证（`docs/watcha-pay-production.md:43`） |
| 第三方博客的部署成本估算 | 未核实 |
| `TOKENPAY_ENCRYPTION_KEY` 的轮换/回填方案 | 解密拒绝任何非 `v1:` 前缀的值，未见迁移路径 |

> 附带说明：本次分析过程中，一个子分析先后给出过"9 处站内死链"与"4 篇完整 Article"两个数字，经独立复验后分别更正为 **4 条**与 **6 篇**。报告中采用的是更正后的数字，相关更正过程保留在 `.research/` 下的子报告里。这类自我更正值得记录，因为它说明"看起来一致的多来源结论"仍需逐条复核。

---

## 结语

Wolfcha 是一个**黑客松出身、但工程标准远超黑客松**的项目。它最核心的贡献不在于"用 LLM 玩狼人杀"这个想法，而在于它认真回答了一个所有多人 LLM 应用都会遇到的问题：

> **当单一上下文天然全知时，如何为每个智能体构造一个可信的、有边界的、主观的世界？**

它的答案是一套完整的方法论：单一分发点 + 单一隐私闸门 + 取值来源约束 + 双向断言 + 请求身份而非内容身份 + 失败即暂停。这套方法论写在 `docs/单人上下文约束.md` 里，以故障档案的形式呈现，比任何架构图都更有说服力。

它的问题同样明显：客户端信任、文档漂移、巨型文件、规则逻辑重复实现、若干未完成的功能（多人模式停在草稿 PR #63、Dockerfile 已失效、29 条 SEO 页面无路由）。但从"718 star / 6 贡献者 / 9 个月活跃开发"的体量看，这是一个仍在健康演进的真实产品，而不是一个演示品。

如果你的项目涉及**多智能体 + 信息不对称**（剧本杀、推理游戏、多角色对话、模拟谈判、AI 陪练），`src/lib/prompt-utils.ts` 和 `docs/单人上下文约束.md` 是两个应该优先精读的文件。
