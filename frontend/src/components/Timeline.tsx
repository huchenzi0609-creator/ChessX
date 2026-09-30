import { asText, type EventRecord, type RecordData } from '../lib/types';
const eventNames: Record<string, string> = {
  WorldCreated: '世界创建',
  TurnStarted: '回合开始',
  TurnCompleted: '回合完成',
  PieceMoved: '角色移动',
  MovementCommitted: '移动落定',
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
export default function Timeline({
  events,
  receipts,
  highlightedEvent,
  onEvent,
}: {
  events?: EventRecord[];
  receipts?: RecordData[];
  highlightedEvent: string | null;
  onEvent: (id: string) => void;
}) {
  const entries = events ?? [];
  return (
    <div className="timeline" aria-live="polite">
      {receipts ? (
        receipts.length ? (
          receipts.map((receipt, index) => (
            <article className="receipt" key={String(receipt.receipt_id ?? index)}>
              <span className="eyebrow">收件 {index + 1}</span>
              <pre>{asText(receipt)}</pre>
            </article>
          ))
        ) : (
          <Empty>本人尚未收到消息。</Empty>
        )
      ) : entries.length ? (
        [...entries].reverse().map((event) => (
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
            {typeof (event.payload.speech ?? event.payload.text) === 'string' && (
              <p className="event-speech">{String(event.payload.speech ?? event.payload.text)}</p>
            )}
            <details>
              <summary>事件事实 · {event.event_id}</summary>
              <pre>{asText(event.payload)}</pre>
            </details>
          </article>
        ))
      ) : (
        <Empty>世界已准备好。运行一个回合，观察第一个事实如何发生。</Empty>
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
