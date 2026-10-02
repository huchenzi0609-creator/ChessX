import { asText, values, type EventRecord, type RecordData } from '../lib/types';
import { Empty } from './Timeline';

type Props = {
  conversations: RecordData[];
  currentTurn: number;
  names: Record<string, string>;
  events?: EventRecord[];
};
const messagesOf = (dialog: RecordData) =>
  values(dialog.messages as Record<string, RecordData> | RecordData[] | undefined);
const idsOf = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
const turnOf = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;

export default function Conversations({ conversations, currentTurn, names, events = [] }: Props) {
  const ordered = conversations
    .map((dialog, index) => {
      const id = String(dialog.conversation_id ?? dialog.id ?? index);
      const relatedEvents = events.filter(
        (event) => event.payload.conversation_id === id && event.turn <= currentTurn,
      );
      const suppliedTurns = [
        dialog.last_active_turn,
        dialog.closed_turn,
        dialog.last_activity_turn,
        dialog.created_turn,
      ]
        .map(turnOf)
        .filter((turn): turn is number => turn !== undefined && turn <= currentTurn);
      const suppliedTurn = suppliedTurns.length ? Math.max(...suppliedTurns) : undefined;
      const historicalTurn = relatedEvents.length
        ? Math.max(...relatedEvents.map((event) => event.turn))
        : undefined;
      const lastTurn =
        dialog.status === 'active'
          ? currentTurn
          : suppliedTurn === undefined
            ? historicalTurn
            : Math.max(suppliedTurn, historicalTurn ?? suppliedTurn);
      const active = dialog.status === 'active';
      return { dialog, id, index, active, lastTurn };
    })
    .sort(
      (a, b) =>
        Number(b.active) - Number(a.active) ||
        (b.lastTurn ?? -1) - (a.lastTurn ?? -1) ||
        a.index - b.index,
    );
  if (!ordered.length) return <Empty>角色接触后，会话将在这里留下逐句记录。</Empty>;
  return (
    <div className="conversations" aria-live="polite">
      <p className="conversation-sort-note">进行中的会话置顶，其余按最近活跃回合排列。</p>
      {ordered.map(({ dialog, id, active, lastTurn }) => {
        const messages = messagesOf(dialog);
        const members = idsOf(dialog.members);
        const archivedMembers = idsOf(
          dialog.participant_ids ?? dialog.member_ids ?? dialog.participants,
        );
        const speakers = messages
          .map((message) => String(message.speaker_id ?? message.actor_id ?? message.speaker ?? ''))
          .filter(Boolean);
        const participants = members.length
          ? members
          : [...new Set([...archivedMembers, ...speakers])];
        const label = (pid: string) =>
          names[pid] ??
          messages.find(
            (message) => message.speaker_id === pid && typeof message.speaker_name === 'string',
          )?.speaker_name ??
          pid;
        return (
          <article
            className={`dialog-card ${active ? 'dialog-active' : ''}`}
            key={id}
            data-conversation-id={id}
          >
            <div className="conversation-heading">
              <span className={`conversation-status ${active ? 'active' : ''}`}>
                {active ? '● 正在交谈' : '已结束'}
              </span>
              <span className="muted small">
                {lastTurn === undefined
                  ? '历史回合未知'
                  : `最近活跃 T${lastTurn} · ${Math.max(0, currentTurn - lastTurn)} 回合前`}
              </span>
            </div>
            <h3>{participants.length ? participants.map(label).join('、') : '会话'}</h3>
            <p className="conversation-id muted mono">{id}</p>
            {messages.length ? (
              messages.map((message, idx) => {
                const speaker = String(
                  message.speaker_id ?? message.actor_id ?? message.speaker ?? '未知发言者',
                );
                return (
                  <p className="dialog-line" key={String(message.message_id ?? message.id ?? idx)}>
                    <strong>{String(message.speaker_name ?? label(speaker))}</strong>
                    <span>{asText(message.speech ?? message.text ?? message.content)}</span>
                  </p>
                );
              })
            ) : (
              <p className="muted">尚无发言。</p>
            )}
            <details>
              <summary>会话结构</summary>
              <pre>{asText(dialog)}</pre>
            </details>
          </article>
        );
      })}
    </div>
  );
}
