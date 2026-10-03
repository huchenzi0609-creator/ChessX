import { asText, values, type EventRecord, type RecordData } from '../lib/types';
import { Empty } from './Timeline';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Props = {
  conversations: RecordData[];
  currentTurn: number;
  names: Record<string, string>;
  events?: EventRecord[];
  worldId?: string;
  readonly?: boolean;
};
const messagesOf = (dialog: RecordData) =>
  values(dialog.messages as Record<string, RecordData> | RecordData[] | undefined);
const idsOf = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
const turnOf = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : undefined;

type CachedMessages = {
  entries: { index: number; message: RecordData }[];
  total: number;
  lastId: string;
};
const messagePage = (dialog: RecordData) =>
  dialog.message_page as
    { total?: number; start_index?: number; has_earlier?: boolean } | undefined;
const mergeMessages = (
  cached: CachedMessages | undefined,
  messages: RecordData[],
  start: number,
) => {
  const byIndex = new Map((cached?.entries ?? []).map((entry) => [entry.index, entry]));
  messages.forEach((message, offset) =>
    byIndex.set(start + offset, { index: start + offset, message }),
  );
  return [...byIndex.values()].sort((a, b) => a.index - b.index).slice(-500);
};
export default function Conversations({
  conversations,
  currentTurn,
  names,
  events = [],
  worldId,
  readonly = false,
}: Props) {
  const [limit, setLimit] = useState(20);
  const [messageLimits, setMessageLimits] = useState<Record<string, number>>({});
  const [cache, setCache] = useState<Record<string, CachedMessages>>({});
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setCache({});
    setMessageLimits({});
    setLimit(20);
    setError(null);
  }, [worldId]);
  useEffect(() => {
    setCache((current) => {
      let changed = false;
      const next = { ...current };
      conversations.forEach((dialog, index) => {
        const id = String(dialog.conversation_id ?? dialog.id ?? index);
        const messages = messagesOf(dialog);
        const page = messagePage(dialog);
        const total = page?.total ?? messages.length;
        const lastId = String(messages.at(-1)?.message_id ?? messages.at(-1)?.id ?? total);
        if (current[id]?.total === total && current[id]?.lastId === lastId) return;
        next[id] = {
          entries: mergeMessages(current[id], messages, page?.start_index ?? 0),
          total,
          lastId,
        };
        changed = true;
      });
      return changed ? next : current;
    });
  }, [conversations]);
  async function loadMessages(id: string, before: number) {
    if (!worldId || readonly || loading) return;
    setLoading(id);
    setError(null);
    try {
      const page = await api.conversationMessages(worldId, id, before);
      if (page.conversation_id !== id) return;
      setCache((current) => ({
        ...current,
        [id]: {
          ...current[id],
          entries: mergeMessages(current[id], page.messages, page.message_page.start_index),
          total: current[id]?.total ?? page.message_page.total,
          lastId: current[id]?.lastId ?? '',
        },
      }));
      setMessageLimits((current) => ({ ...current, [id]: (current[id] ?? 20) + 50 }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '会话历史读取失败');
    } finally {
      setLoading(null);
    }
  }
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
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <p className="conversation-sort-note">进行中的会话置顶，其余按最近活跃回合排列。</p>
      {ordered.slice(0, limit).map(({ dialog, id, active, lastTurn }) => {
        const supplied = messagesOf(dialog);
        const cached = mergeMessages(cache[id], supplied, messagePage(dialog)?.start_index ?? 0);
        const messages = cached.map((entry) => entry.message);
        const earliest = cached[0]?.index ?? messagePage(dialog)?.start_index ?? 0;
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
              messages.slice(-(messageLimits[id] ?? 20)).map((message, idx) => {
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
            {messages.length > (messageLimits[id] ?? 20) && (
              <button
                onClick={() =>
                  setMessageLimits((current) => ({ ...current, [id]: (current[id] ?? 20) + 30 }))
                }
              >
                显示本会话更早的 30 句
              </button>
            )}
            {worldId &&
              !readonly &&
              earliest > 0 &&
              messages.length <= (messageLimits[id] ?? 20) && (
                <button disabled={loading !== null} onClick={() => void loadMessages(id, earliest)}>
                  {loading === id ? '读取会话历史…' : '读取本会话更早的 50 句'}
                </button>
              )}
            <details>
              <summary>会话结构</summary>
              <pre>{asText(dialog)}</pre>
            </details>
          </article>
        );
      })}
      {ordered.length > limit && (
        <button onClick={() => setLimit((current) => current + 20)}>显示更早的 20 个会话</button>
      )}
    </div>
  );
}
