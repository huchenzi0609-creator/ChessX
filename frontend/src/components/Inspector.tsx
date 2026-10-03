import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import Operations from './Operations';
import {
  asText,
  pieceLabels,
  restOf,
  type Perspective,
  type Piece,
  type RecordData,
} from '../lib/types';
export default function Inspector({
  worldId,
  piece,
  research,
  perspective,
}: {
  worldId: string;
  piece: Piece | undefined;
  research: boolean;
  perspective: Perspective | null;
}) {
  const [inspection, setInspection] = useState<RecordData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    setInspection(null);
    setError(null);
    setOpen(false);
  }, [piece?.id, worldId, research]);
  useEffect(() => {
    if (!research || !piece || !open) return;
    let active = true;
    api
      .inspection(worldId, piece.id)
      .then((result) => {
        if (active) setInspection(result);
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      });
    return () => {
      active = false;
    };
  }, [worldId, piece?.id, research, open]);
  const privateData = piece?.id === perspective?.piece_id ? perspective?.private : undefined;
  return (
    <aside className="inspector">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">CHARACTER / 角色</span>
          <h2>{piece?.name ?? '选择一个角色'}</h2>
        </div>
        <span className="inspector-glyph">{pieceLabels[piece?.type ?? ''] ?? '◇'}</span>
      </div>
      {piece ? (
        <>
          <dl className="piece-facts">
            <div>
              <dt>身份</dt>
              <dd>
                {piece.color ?? '—'} · {pieceLabels[piece.type ?? ''] ?? piece.type}
              </dd>
            </div>
            <div>
              <dt>位置</dt>
              <dd className="mono">
                {piece.position ? `(${piece.position.row}, ${piece.position.col})` : '未知'}
              </dd>
            </div>
            <div>
              <dt>休息</dt>
              <dd>{restOf(piece)} 回合</dd>
            </div>
            <div>
              <dt>状态</dt>
              <dd>
                {piece.alive === false
                  ? '已死亡'
                  : asText(
                      piece.battle_id
                        ? '参战'
                        : piece.conversation_id
                          ? '会话中'
                          : (piece.status ?? '自由行动'),
                    )}
              </dd>
            </div>
          </dl>
          <p className="muted small">颜色不代表固定阵营。关系由角色经历建立。</p>
          {privateData && (
            <div className="private-section">
              <span className="eyebrow">本人资料 · 仅角色视角</span>
              <details>
                <summary>个人资料</summary>
                <pre>{asText(privateData.persona)}</pre>
              </details>
              <details>
                <summary>记忆 · v{privateData.memory_version ?? '—'}</summary>
                <pre>{asText(privateData.memory)}</pre>
              </details>
              <details>
                <summary>目标 · v{privateData.goal_version ?? '—'}</summary>
                <pre>{asText(privateData.goals)}</pre>
              </details>
              <details>
                <summary>跨回合私人行动计划</summary>
                <pre>{asText(privateData.travel_plan)}</pre>
              </details>
            </div>
          )}
          {research && (
            <div className="private-section">
              <span className="research-label">全知研究 · 私密调试</span>
              <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
                <summary>检查输入、输出与 API 公开推理</summary>
                <p className="muted small">
                  仅展示提供方实际返回的推理文本，默认折叠，不进入其他角色的事件或输入。
                </p>
                {error ? (
                  <p role="alert" className="error-text">
                    {error}
                  </p>
                ) : inspection ? (
                  <pre>{asText(inspection)}</pre>
                ) : (
                  open && <p className="muted">读取研究记录…</p>
                )}
              </details>
            </div>
          )}
        </>
      ) : (
        <p className="muted">在棋盘或角色列表中选择。位置、休息状态及可见记录将在此显示。</p>
      )}
      {research && <Operations worldId={worldId} />}
    </aside>
  );
}
