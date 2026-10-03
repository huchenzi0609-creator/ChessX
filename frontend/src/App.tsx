import { useCallback, useEffect, useState } from 'react';
import Board from './components/Board';
import Conversations from './components/Conversations';
import Inspector from './components/Inspector';
import ScenarioEditor from './components/ScenarioEditor';
import Stories from './components/Stories';
import Timeline, { Empty } from './components/Timeline';
import ArchiveTools from './components/ArchiveTools';
import RuntimePanel, { BudgetEditor, DEFAULT_BUDGET } from './components/RuntimePanel';
import { label } from './lib/language';
import { useWorld } from './hooks/useWorld';
import { api } from './lib/api';
import {
  asText,
  pieceLabels,
  pieceSymbols,
  restOf,
  values,
  type CommandKind,
  type Health,
  type World,
  type WorldSummary,
} from './lib/types';
type View = 'observer' | 'research' | `piece:${string}`;
type Tab = 'events' | 'conversations' | 'battles' | 'stories';
export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [worlds, setWorlds] = useState<WorldSummary[]>([]);
  const [worldId, setWorldId] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [view, setView] = useState<View>('observer');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [turns, setTurns] = useState(5);
  const [budget, setBudget] = useState(DEFAULT_BUDGET);
  const [tab, setTab] = useState<Tab>('events');
  const [commandBusy, setCommandBusy] = useState(false);
  const [commandMessage, setCommandMessage] = useState<string | null>(null);
  const [pendingCommand, setPendingCommand] = useState<{
    worldId: string;
    commandId: string;
  } | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [replaySeq, setReplaySeq] = useState<number | null>(null);
  const [replayInput, setReplayInput] = useState('0');
  const [highlightedEvent, setHighlightedEvent] = useState<string | null>(null);
  const perspectiveId = view.startsWith('piece:') ? view.slice(6) : null;
  const {
    world: fetchedWorld,
    perspective: fetchedPerspective,
    events,
    error,
    connected,
    refresh,
    loadEvent,
    loadEarlier,
  } = useWorld(worldId, perspectiveId, replaySeq);
  const world = fetchedWorld?.world_id === worldId && !perspectiveId ? fetchedWorld : null;
  const perspective =
    fetchedPerspective?.world_id === worldId && fetchedPerspective.piece_id === perspectiveId
      ? fetchedPerspective
      : null;
  const metadata = world ?? worlds.find((item) => item.world_id === worldId);
  const observerPieces = values(world?.pieces);
  const observation = perspective?.observation;
  const pieces = perspectiveId
    ? values(observation?.pieces)
        .concat(observation?.self ? [observation.self] : [])
        .filter((piece, index, items) => items.findIndex((item) => item.id === piece.id) === index)
    : observerPieces;
  const selectedPiece =
    pieces.find((piece) => piece.id === selectedId) ??
    (perspectiveId ? observation?.self : undefined);
  const board = perspectiveId
    ? observation?.board
    : (world?.board ??
      (world?.scenario as { board?: { rows: number; cols: number } } | undefined)?.board);
  const availableRoles = values(
    (world ?? worlds.find((item) => item.world_id === worldId))?.pieces,
  );
  const roleChoices = perspectiveId
    ? availableRoles.filter((piece) => piece.id === perspectiveId)
    : availableRoles;
  const syncWorlds = useCallback(async () => {
    setWorlds(await api.worlds());
  }, []);
  useEffect(() => {
    let active = true;
    Promise.allSettled([api.health(), api.worlds()]).then((results) => {
      if (!active) return;
      if (results[0].status === 'fulfilled') setHealth(results[0].value);
      else setLocalError(results[0].reason.message);
      if (results[1].status === 'fulfilled') {
        setWorlds(results[1].value);
        if (results[1].value.length) setWorldId(results[1].value[0].world_id);
      }
    });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (world)
      setWorlds((current) => [
        world,
        ...current.filter((item) => item.world_id !== world.world_id),
      ]);
  }, [world]);
  useEffect(() => {
    const initialSelection = perspectiveId ? observation?.self : pieces[0];
    if (!selectedId && initialSelection) setSelectedId(initialSelection.id);
  }, [pieces, selectedId, perspectiveId, observation?.self]);
  useEffect(() => {
    if (!pendingCommand || pendingCommand.worldId !== worldId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function pollCommand() {
      try {
        const result = await api.commandStatus(pendingCommand!.worldId, pendingCommand!.commandId);
        if (!active) return;
        const status = String(result.status ?? 'accepted');
        setCommandMessage(
          `命令状态 · ${status}${result.from_turn !== undefined ? ` · 起点 ${result.from_turn}` : ''}${result.target_turn !== undefined ? ` · 目标 ${result.target_turn}` : ''}`,
        );
        if (['completed', 'failed', 'interrupted', 'cancelled', 'rejected'].includes(status)) {
          setPendingCommand(null);
          refresh();
          return;
        }
      } catch {
        if (active) setCommandMessage('命令状态连接中断，正在重连；执行器仍由后端管理。');
      }
      if (active) timer = setTimeout(pollCommand, 1000);
    }
    void pollCommand();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [pendingCommand, worldId, refresh]);
  function chooseWorld(next: string) {
    setWorldId(next);
    setView('observer');
    setReplaySeq(null);
    setSelectedId(null);
    setCommandMessage(null);
    setPendingCommand(null);
    setLocalError(null);
  }
  async function command(kind: CommandKind) {
    if (!worldId) return;
    setCommandBusy(true);
    setLocalError(null);
    setCommandMessage(null);
    try {
      const result = await api.command(
        worldId,
        kind,
        kind === 'run' || kind === 'resume' ? turns : undefined,
        ['run', 'resume', 'step_event', 'step_turn', 'retry'].includes(kind) ? budget : undefined,
      );
      if (kind === 'reset' && typeof result.world_id === 'string' && result.world_id !== worldId) {
        chooseWorld(result.world_id);
        await syncWorlds();
        setCommandMessage('已从同一冻结场景创建新实验；原实验事实仍保留。');
        return;
      }
      setCommandMessage(
        `命令已接收 · ${asText(result.status ?? 'accepted')}${result.target_turn !== undefined ? ` · 目标回合 ${result.target_turn}` : ''}`,
      );
      if (
        result.command_id &&
        !['completed', 'failed', 'interrupted', 'cancelled', 'rejected'].includes(
          String(result.status),
        )
      ) {
        setPendingCommand({ worldId, commandId: String(result.command_id) });
      }
      refresh();
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : '命令失败');
    } finally {
      setCommandBusy(false);
    }
  }
  async function exportWorld() {
    if (!worldId) return;
    setLocalError(null);
    try {
      await api.export(worldId, view === 'research' ? 'research' : 'observer');
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : '导出失败');
    }
  }
  function created(next: World) {
    setWorlds((current) => [next, ...current]);
    chooseWorld(next.world_id);
    setEditorOpen(false);
    void syncWorlds().catch(() => setLocalError('实验已创建，但存档列表暂时无法刷新。'));
  }
  function reference(eventId: string) {
    setTab('events');
    setHighlightedEvent(eventId);
    void loadEvent(eventId)
      .then(() =>
        setTimeout(
          () =>
            document
              .getElementById(`event-${eventId}`)
              ?.scrollIntoView({ behavior: 'smooth', block: 'center' }),
          50,
        ),
      )
      .catch((cause) =>
        setLocalError(cause instanceof Error ? cause.message : '引用事件暂时无法读取'),
      );
  }
  const busy = commandBusy || replaySeq !== null;
  const runState = metadata?.lifecycle ?? 'READY';
  const visibleEvents = perspectiveId ? undefined : events;
  const dialogs = values(world?.conversations);
  const visibleNames = Object.fromEntries(
    pieces.map((piece) => [piece.id, piece.name ?? piece.id]),
  );
  const visibleTypes = Object.fromEntries(pieces.map((piece) => [piece.id, piece.type ?? '']));
  const battles = values(world?.battles);
  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="/" aria-label="ChessX 首页">
          <span className="brand-mark">♞</span>
          <span>
            Chess<span className="brand-x">X</span>
            <small>涌现叙事实验</small>
          </span>
        </a>
        <div className="header-center">
          <span className="eyebrow">THE NARRATIVE OBSERVATORY</span>
          <span>从一个行动，观察故事如何出现。</span>
        </div>
        <div className="header-actions">
          <span className={`connection ${connected || (!worldId && health) ? 'online' : ''}`}>
            <i />
            {worldId
              ? connected
                ? '本地服务已连接'
                : '正在连接'
              : health
                ? '本地服务就绪'
                : '服务未连接'}
          </span>
          <button onClick={() => setEditorOpen(true)}>＋ 新实验</button>
        </div>
      </header>
      <main>
        {!worldId || editorOpen ? (
          <>
            <div className="intro">
              <span className="eyebrow">CHESSX / DEMO V2.2</span>
              <h1>
                规则安排相遇。
                <br />
                <em>故事来自角色。</em>
              </h1>
              <p>移动、交涉、记忆与选择，在同一个世界留下可追溯的事实。</p>
            </div>
            <ScenarioEditor
              health={health}
              onCreated={created}
              onClose={worldId ? () => setEditorOpen(false) : undefined}
            />
            {worlds.length > 0 && !worldId && (
              <button onClick={() => chooseWorld(worlds[0].world_id)}>恢复最近实验</button>
            )}
          </>
        ) : (
          <>
            <section className="experiment-heading">
              <div>
                <span className="eyebrow">EXPERIMENT / 运行中的世界</span>
                <h1>{metadata?.title ?? '叙事实验'}</h1>
                <div className="experiment-meta">
                  <span className={`mode-badge ${metadata?.mode === 'live' ? 'live' : ''}`}>
                    {metadata?.mode === 'live' ? '真实模型实验' : '工程固定响应 · 非 AI'}
                  </span>
                  <span className="muted mono">
                    {asText(
                      metadata?.rule_profile ??
                        (health?.rule_version
                          ? `demo-v${health.rule_version.replace(/^demo-v/, '')}`
                          : 'demo-v2.2'),
                    )}
                  </span>
                  <span className="muted mono">{worldId.slice(0, 12)}</span>
                </div>
              </div>
              <div className="experiment-picker">
                <label className="field-label" htmlFor="world-choice">
                  实验存档
                </label>
                <select
                  id="world-choice"
                  value={worldId}
                  onChange={(event) => chooseWorld(event.target.value)}
                >
                  {worlds.map((item) => (
                    <option key={item.world_id} value={item.world_id}>
                      {item.title ?? item.world_id} · T{item.completed_turn ?? item.turn}
                    </option>
                  ))}
                </select>
              </div>
            </section>
            <details className="budget-settings">
              <summary>
                本次预算：{budget.max_calls} 次请求 ·{' '}
                {budget.max_total_tokens.toLocaleString('zh-CN')} Token · {budget.max_wall_seconds}{' '}
                秒（可调整）
              </summary>
              <BudgetEditor
                budget={budget}
                onChange={setBudget}
                disabled={busy || ['RUNNING', 'STEPPING', 'PAUSING'].includes(runState)}
              />
            </details>
            {!perspectiveId && world && <RuntimePanel world={world} names={visibleNames} />}
            <ArchiveTools
              worldId={worldId}
              title={String(metadata?.title ?? worldId)}
              running={commandBusy || ['RUNNING', 'STEPPING', 'PAUSING'].includes(runState)}
              readonly={Boolean(perspectiveId) || replaySeq !== null}
              onChanged={() => {
                refresh();
                void syncWorlds();
              }}
              onDeleted={() => {
                const remaining = worlds.filter((item) => item.world_id !== worldId);
                setWorlds(remaining);
                if (remaining.length) chooseWorld(remaining[0].world_id);
                else {
                  setWorldId(null);
                  setView('observer');
                  setReplaySeq(null);
                  setSelectedId(null);
                }
              }}
            />
            <section className="controls" aria-label="实验运行控制">
              <div className="run-group">
                <label htmlFor="turn-count">运行</label>
                <input
                  id="turn-count"
                  type="number"
                  min="1"
                  max="500"
                  value={turns}
                  onChange={(event) =>
                    setTurns(Math.max(1, Math.min(500, Number(event.target.value) || 1)))
                  }
                />
                <span>回合</span>
                <button className="primary" disabled={busy} onClick={() => void command('run')}>
                  ▶ 开始
                </button>
                <button disabled={busy} onClick={() => void command('pause')}>
                  暂停
                </button>
                <button disabled={busy} onClick={() => void command('resume')}>
                  继续
                </button>
              </div>
              <div className="control-secondary">
                <button disabled={busy} onClick={() => void command('step_event')}>
                  单步提交
                </button>
                <button disabled={busy} onClick={() => void command('step_turn')}>
                  单步回合
                </button>
                <button disabled={busy} onClick={() => void command('retry')}>
                  重试
                </button>
                <button disabled={busy} onClick={() => void command('reset')}>
                  重置运行
                </button>
                <button disabled={Boolean(perspectiveId)} onClick={() => void exportWorld()}>
                  导出 ↗
                </button>
              </div>
            </section>
            {(localError || error) && (
              <div role="alert" className="error-banner">
                <span>{localError ?? error}</span>
                <button
                  onClick={() => {
                    setLocalError(null);
                    refresh();
                    void api
                      .health()
                      .then(setHealth)
                      .catch(() => {});
                  }}
                >
                  重新连接
                </button>
              </div>
            )}
            {commandMessage && (
              <p role="status" className="command-status">
                {commandMessage}
              </p>
            )}
            <section className="status-strip" aria-label="运行状态">
              <div>
                <span>世界回合</span>
                <strong className="mono">
                  {perspective?.turn ?? metadata?.turn ?? 0}
                  <small> / 已完成 {metadata?.completed_turn ?? 0}</small>
                </strong>
              </div>
              <div>
                <span>运行阶段</span>
                <strong>
                  {world?.phase ? label(world.phase) : perspectiveId ? '角色观测' : '—'}
                </strong>
              </div>
              <div>
                <span>执行状态</span>
                <strong className="mono">
                  {replaySeq !== null
                    ? world
                      ? `REPLAY · #${world.seq}`
                      : 'REPLAY · 正在载入'
                    : label(runState)}
                </strong>
              </div>
              <div>
                <span>累计调用 / Token</span>
                <strong className="mono">
                  {perspectiveId
                    ? '研究者统计已隐藏'
                    : `${asText(world?.usage?.calls ?? world?.usage?.llm_calls ?? 0)} / ${asText(world?.usage?.total_tokens ?? Number(world?.usage?.input_tokens ?? 0) + Number(world?.usage?.output_tokens ?? 0) + Number(world?.usage?.reasoning_tokens ?? 0))}`}
                </strong>
              </div>
            </section>
            <div className="viewbar">
              <div>
                <label htmlFor="perspective">观测视角</label>
                <select
                  id="perspective"
                  value={view}
                  onChange={(event) => {
                    setView(event.target.value as View);
                    setReplaySeq(null);
                    setTab('events');
                    setSelectedId(null);
                  }}
                >
                  <option value="observer">普通观众 · 公开事件</option>
                  <option value="research">全知研究 · 私密记录</option>
                  {roleChoices.map((piece) => (
                    <option key={piece.id} value={`piece:${piece.id}`}>
                      角色 · {piece.name ?? piece.id}
                    </option>
                  ))}
                </select>
                {perspectiveId && <span className="view-note">仅使用本人投影与收件箱</span>}
                {view === 'research' && (
                  <span className="research-label">本地研究权限 · 不回灌角色</span>
                )}
              </div>
              {!perspectiveId && (
                <div className="replay-controls">
                  <label htmlFor="replay-seq">历史事件</label>
                  <input
                    id="replay-seq"
                    type="number"
                    min="0"
                    max={metadata?.seq ?? 1}
                    value={replayInput}
                    onChange={(event) => setReplayInput(event.target.value)}
                  />
                  <button
                    onClick={() =>
                      setReplaySeq(
                        Math.max(0, Math.min(metadata?.seq ?? 0, Number(replayInput) || 0)),
                      )
                    }
                  >
                    回放
                  </button>
                  {replaySeq !== null && (
                    <button onClick={() => setReplaySeq(null)}>返回当前</button>
                  )}
                </div>
              )}
            </div>
            <div className="observatory-grid">
              <div className="world-column">
                <Board
                  rows={board?.rows ?? 8}
                  cols={board?.cols ?? 8}
                  pieces={pieces}
                  objects={perspectiveId ? values(observation?.objects) : values(world?.objects)}
                  tombstones={
                    perspectiveId ? values(observation?.tombstones) : values(world?.tombstones)
                  }
                  visibleCells={perspectiveId ? (observation?.visible_cells ?? []) : undefined}
                  selectedId={selectedPiece?.id ?? null}
                  onSelect={setSelectedId}
                />
                <div className="cast-list" aria-label="当前可见角色">
                  {pieces.map((piece) => (
                    <button
                      className={selectedPiece?.id === piece.id ? 'selected' : ''}
                      key={piece.id}
                      onClick={() => setSelectedId(piece.id)}
                    >
                      <span className="cast-symbol">{pieceSymbols[piece.type ?? ''] ?? '●'}</span>
                      <span>
                        {piece.name ?? piece.id}
                        <small>
                          {pieceLabels[piece.type ?? ''] ?? piece.type} · {piece.color}
                        </small>
                      </span>
                      <span className="mono muted">
                        {restOf(piece) ? `休 ${restOf(piece)}` : '·'}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
              <section className="activity-panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">CHRONICLE / 留下的事实</span>
                    <h2>{perspectiveId ? '所见与所闻' : '世界编年'}</h2>
                  </div>
                  <span className="muted mono">#{perspective?.seq ?? world?.seq ?? '—'}</span>
                </div>
                <div className="tabs" role="tablist" aria-label="观察记录">
                  {(perspectiveId
                    ? ['events']
                    : (['events', 'conversations', 'battles', 'stories'] as Tab[])
                  ).map((item) => (
                    <button
                      key={item}
                      role="tab"
                      id={`tab-${item}`}
                      aria-controls={`panel-${item}`}
                      aria-selected={tab === item}
                      onClick={() => setTab(item as Tab)}
                    >
                      {
                        {
                          events: perspectiveId ? '本人收件' : '事件',
                          conversations: '会话',
                          battles: '战斗',
                          stories: '故事',
                        }[item]
                      }
                    </button>
                  ))}
                </div>
                <div
                  role="tabpanel"
                  id={`panel-${tab}`}
                  aria-labelledby={`tab-${tab}`}
                  className="activity-body"
                >
                  {tab === 'events' && (
                    <Timeline
                      names={visibleNames}
                      pieceTypes={visibleTypes}
                      events={visibleEvents}
                      receipts={perspectiveId ? (perspective?.receipts ?? []) : undefined}
                      highlightedEvent={highlightedEvent}
                      onEvent={setHighlightedEvent}
                      onLoadEarlier={loadEarlier}
                      hasEarlierReceipts={perspective?.receipt_page?.has_earlier}
                    />
                  )}
                  {!perspectiveId && tab === 'conversations' && (
                    <Conversations
                      key={`${worldId}:${replaySeq ?? 'live'}`}
                      conversations={dialogs}
                      currentTurn={world?.turn ?? 0}
                      names={visibleNames}
                      events={visibleEvents}
                      worldId={worldId}
                      readonly={replaySeq !== null}
                    />
                  )}
                  {!perspectiveId &&
                    tab === 'battles' &&
                    (battles.length ? (
                      battles.map((battle, index) => (
                        <article
                          className="dialog-card"
                          key={String(battle.battle_id ?? battle.id ?? index)}
                        >
                          <span className="eyebrow">
                            战斗 · {asText(battle.status ?? 'active')}
                          </span>
                          <h3>
                            第 {asText(battle.round ?? 0)} / {asText(battle.max_rounds ?? 4)} 轮
                          </h3>
                          <p>战场 {asText(battle.anchor)}</p>
                          <p className="muted">
                            参战者必须实际到达战场。战争消息不授予远程加入权限。
                          </p>
                          <details>
                            <summary>参战与裁定记录</summary>
                            <pre>{asText(battle)}</pre>
                          </details>
                        </article>
                      ))
                    ) : (
                      <Empty>还没有战斗。冲突由角色的行动与选择产生。</Empty>
                    ))}
                  {!perspectiveId && tab === 'stories' && world && (
                    <Stories world={world} onReference={reference} onRefresh={refresh} />
                  )}
                </div>
              </section>
              <Inspector
                worldId={worldId}
                piece={selectedPiece}
                research={view === 'research' && replaySeq === null}
                perspective={perspective}
              />
            </div>
            {(world?.error || (world?.errors && world.errors.length > 0)) && (
              <div className="error-banner">
                <strong>运行需要处理</strong>
                <pre>{asText(world.error ?? world.errors)}</pre>
              </div>
            )}
          </>
        )}
      </main>
      <footer className="app-footer">
        <span>
          CHESSX <span className="mono">/ 0.2</span>
        </span>
        <span>事实可回放。记忆各自持有。故事从此生长。</span>
        <span className="mono">LOCAL EXPERIMENT</span>
      </footer>
    </div>
  );
}
