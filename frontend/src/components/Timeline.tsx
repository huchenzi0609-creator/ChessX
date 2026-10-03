import { asText, type EventRecord, type Position, type RecordData } from '../lib/types';
import { useEffect, useState } from 'react';
const eventNames: Record<string, string> = {
  WorldCreated: '世界创建',
  TurnStarted: '回合开始',
  TurnCompleted: '回合完成',
  PieceMoved: '角色移动',
  MovementCommitted: '移动落定',
  MoveCommitted: '角色移动',
  ConversationStarted: '会话开始',
  ConversationMessage: '角色发言',
  NegotiationStarted: '路径交涉',
  BattleStarted: '战斗开始',
  BattleRoundResolved: '战斗裁定',
  BattleEnded: '战斗结束',
  PieceDied: '角色死亡',
  MemoryUpdated: '记忆更新',
  GoalUpdated: '目标更新',
  ObjectTransferred: '物件转交',
  StoryGenerated: '故事成稿',
  BardCreated: '故事成稿',
  BattleOutcome: '战斗裁定',
  ConversationCreated: '会话创建',
  TechnicalFailure: '技术暂停',
  RestApplied: '休息结算',
  PieceWaited: '等待',
  NegotiationMessage: '交涉发言',
  SettlementMessage: '战后发言',
};
const speechEvents = new Set(['ConversationMessage', 'NegotiationMessage', 'SettlementMessage']);
const movementEvents = new Set(['MoveCommitted', 'MovementCommitted', 'PieceMoved']);
const recordOf = (value: unknown): RecordData =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as RecordData) : {};
function positionOf(value: unknown): Position | undefined {
  const position = recordOf(value);
  return Number.isInteger(position.row) && Number.isInteger(position.col)
    ? (position as Position)
    : undefined;
}
function movementPath(payload: RecordData, pieceType?: string): Position[] {
  if (Array.isArray(payload.path))
    return payload.path.map(positionOf).filter((p): p is Position => !!p);
  const end = positionOf(payload.position ?? payload.actual_to ?? payload.to);
  // A projected receipt cannot imply the hidden origin or intermediate route.
  if (payload.path_visible_only) return end ? [end] : [];
  const origin = positionOf(payload.origin ?? payload.from);
  if (!origin || !end) return [origin, end].filter((p): p is Position => !!p);
  const dr = end.row - origin.row,
    dc = end.col - origin.col;
  const straight = dr === 0 || dc === 0;
  const diagonal = Math.abs(dr) === Math.abs(dc);
  const sliding =
    (pieceType === 'queen' && (straight || diagonal)) ||
    (pieceType === 'rook' && straight) ||
    (pieceType === 'bishop' && diagonal);
  const distance = Math.max(Math.abs(dr), Math.abs(dc));
  if (!sliding || distance > 1024) return [origin, end];
  return Array.from({ length: distance + 1 }, (_, n) => ({
    row: origin.row + Math.sign(dr) * n,
    col: origin.col + Math.sign(dc) * n,
  }));
}
function EventFacts({
  type,
  payload,
  actorId,
  names,
  pieceTypes,
}: {
  type: string;
  payload: RecordData;
  actorId?: string;
  names: Record<string, string>;
  pieceTypes: Record<string, string>;
}) {
  const actor = String(
    (speechEvents.has(type) ? payload.speaker_id : payload.piece_id) ??
      payload.actor_id ??
      actorId ??
      '未知角色',
  );
  const actorName = String(
    (speechEvents.has(type) ? payload.speaker_name : payload.piece_name) ?? names[actor] ?? actor,
  );
  const speech = payload.speech ?? payload.text;
  const path = movementEvents.has(type)
    ? movementPath(payload, String(payload.piece_type ?? pieceTypes[actor] ?? ''))
    : [];
  return (
    <>
      {(speechEvents.has(type) || movementEvents.has(type)) && (
        <p className="event-actor">
          {speechEvents.has(type) ? '说话人' : '移动者'} · <strong>{actorName}</strong>
        </p>
      )}
      {typeof speech === 'string' && <p className="event-speech">{speech}</p>}
      {movementEvents.has(type) && (
        <p className="event-path">
          <span>{payload.path_visible_only ? '可见路段' : '移动路径'}（行, 列；从 0 起）</span>
          <br />
          {path.length
            ? path.map((p) => `(${p.row}, ${p.col})`).join(' → ')
            : '此历史记录未保存路径'}
        </p>
      )}
    </>
  );
}
export default function Timeline({
  events,
  receipts,
  highlightedEvent,
  onEvent,
  names = {},
  pieceTypes = {},
  onLoadEarlier,
  hasEarlierReceipts = false,
}: {
  events?: EventRecord[];
  receipts?: RecordData[];
  highlightedEvent: string | null;
  onEvent: (id: string) => void;
  names?: Record<string, string>;
  pieceTypes?: Record<string, string>;
  onLoadEarlier?: () => Promise<void>;
  hasEarlierReceipts?: boolean;
}) {
  const entries = events ?? [];
  const [limit, setLimit] = useState(60);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    setLimit(60);
  }, [Boolean(receipts)]);
  const reversed = [...entries].reverse();
  const page = reversed.slice(0, limit);
  const selected = entries.find((event) => event.event_id === highlightedEvent);
  if (selected && !page.some((event) => event.event_id === selected.event_id)) page.push(selected);
  return (
    <div className="timeline" aria-live="polite">
      {receipts ? (
        receipts.length ? (
          [...receipts]
            .reverse()
            .slice(0, limit)
            .map((receipt, index) => {
              const type = String(receipt.type ?? receipt.kind ?? '');
              const payload = recordOf(receipt.payload ?? receipt);
              return (
                <article
                  className="receipt"
                  key={String(receipt.receipt_id ?? receipt.event_id ?? index)}
                >
                  <span className="eyebrow">
                    {eventNames[type] ?? (type || `收件 ${receipts.length - index}`)}
                    {receipt.turn !== undefined ? ` · T${receipt.turn}` : ''}
                  </span>
                  <EventFacts
                    type={type}
                    payload={payload}
                    actorId={typeof receipt.actor_id === 'string' ? receipt.actor_id : undefined}
                    names={names}
                    pieceTypes={pieceTypes}
                  />
                  <details>
                    <summary>收件事实</summary>
                    <pre>{asText(receipt)}</pre>
                  </details>
                </article>
              );
            })
        ) : (
          <Empty>本人尚未收到消息。</Empty>
        )
      ) : entries.length ? (
        page.map((event) => (
          <article
            id={`event-${event.event_id}`}
            key={event.event_id}
            className={`event-card ${highlightedEvent === event.event_id ? 'highlighted' : ''}`}
          >
            <button className="event-header" onClick={() => onEvent(event.event_id)}>
              <span className="event-dot" />
              <strong>{eventNames[event.type] ?? event.type}</strong>
              <span className="mono muted">
                T{event.turn} · #{event.seq}
              </span>
            </button>
            <EventFacts
              type={event.type}
              payload={event.payload}
              actorId={event.actor_id}
              names={names}
              pieceTypes={pieceTypes}
            />
            <details>
              <summary>事件事实 · {event.event_id}</summary>
              <pre>{asText(event.payload)}</pre>
            </details>
          </article>
        ))
      ) : (
        <Empty>世界已准备好。运行一个回合，观察第一个事实如何发生。</Empty>
      )}
      {(receipts ? receipts.length > limit : entries.length > limit) && (
        <button onClick={() => setLimit((current) => current + 60)}>显示更早的 60 条</button>
      )}
      {(receipts
        ? hasEarlierReceipts && receipts.length <= limit
        : entries.length <= limit &&
          entries.length > 0 &&
          Math.min(...entries.map((event) => event.seq)) > 1) &&
        onLoadEarlier && (
          <button
            disabled={loading}
            onClick={async () => {
              setLoading(true);
              setLoadError(null);
              try {
                await onLoadEarlier();
                setLimit((current) => current + 200);
              } catch {
                setLoadError('更早的事件暂时无法读取，请重试。');
              } finally {
                setLoading(false);
              }
            }}
          >
            {loading ? '读取历史…' : receipts ? '读取更早的本人收件' : '读取更早的存档页'}
          </button>
        )}
      {loadError && <p role="alert">{loadError}</p>}
      {(entries.length > 60 || (receipts?.length ?? 0) > 60) && (
        <p className="muted small">
          记录按窗口显示；故事来源可直接跳转到对应事件。更早内容保存在本地存档中。
        </p>
      )}
    </div>
  );
}
export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-symbol">◇</span>
      <p>{children}</p>
    </div>
  );
}
