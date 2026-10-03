import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { label } from '../lib/language';
import { asText, type RecordData } from '../lib/types';
export default function Operations({ worldId }: { worldId: string }) {
  const [open, setOpen] = useState(false);
  const [actor, setActor] = useState('');
  const [stage, setStage] = useState('');
  const [records, setRecords] = useState<RecordData[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    setOpen(false);
    setRecords([]);
    setCursor(null);
    setError(null);
  }, [worldId]);
  useEffect(() => {
    setRecords([]);
    setCursor(null);
    setError(null);
    if (!open) return;
    let active = true;
    setBusy(true);
    api
      .operations(worldId, { ...(actor ? { actor_id: actor } : {}), ...(stage ? { stage } : {}) })
      .then((page) => {
        if (active) {
          setRecords(page.operations);
          setCursor(page.has_more ? page.next_cursor : null);
        }
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      })
      .finally(() => {
        if (active) setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [worldId, actor, stage, open, revision]);
  async function more() {
    if (!cursor || busy) return;
    setBusy(true);
    setError(null);
    try {
      const page = await api.operations(worldId, {
        ...(actor ? { actor_id: actor } : {}),
        ...(stage ? { stage } : {}),
        before_id: cursor,
      });
      setRecords((current) => [...current, ...page.operations]);
      setCursor(page.has_more ? page.next_cursor : null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '研究记录读取失败');
    } finally {
      setBusy(false);
    }
  }
  return (
    <details
      className="operations"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>所有 Agent 的分页研究记录</summary>
      <p className="muted small">
        包含角色、战斗裁判及吟游诗人；输入、输出、公开推理只在本地研究视角展开。
      </p>
      <button disabled={busy} onClick={() => setRevision((current) => current + 1)}>
        刷新研究记录
      </button>
      <label>
        角色 ID 筛选
        <input
          value={actor}
          onChange={(event) => setActor(event.target.value)}
          placeholder="留空包含系统 Agent"
        />
      </label>
      <label>
        阶段筛选
        <select value={stage} onChange={(event) => setStage(event.target.value)}>
          <option value="">全部阶段</option>
          {[
            'action',
            'ordinary',
            'negotiation',
            'settlement',
            'memory',
            'memory_compaction',
            'battle_narrator',
            'outcome',
            'surrender',
            'invite_response',
            'vote',
            'release_vote',
            'settlement_terminal',
            'accept_transfer',
            'bard',
            'capability_probe',
          ].map((value) => (
            <option key={value} value={value}>
              {label(value)}
            </option>
          ))}
        </select>
      </label>
      {records.map((record, index) => (
        <details key={String(record.id ?? record.operation_id ?? index)}>
          <summary>
            {label(record.stage)} · {asText(record.actor_id ?? '系统 Agent')} ·{' '}
            {label(record.status)}
          </summary>
          <pre>{asText(record)}</pre>
        </details>
      ))}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {busy && <p className="muted">读取研究记录…</p>}
      {cursor && (
        <button disabled={busy} onClick={() => void more()}>
          加载更早的 25 条
        </button>
      )}
      {!busy && !records.length && !error && <p className="muted">当前筛选没有研究记录。</p>}
    </details>
  );
}
