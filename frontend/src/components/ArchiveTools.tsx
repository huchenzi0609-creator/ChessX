import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { asText, type UpgradePreview } from '../lib/types';
export default function ArchiveTools({
  worldId,
  title,
  running,
  readonly,
  onDeleted,
  onChanged,
}: {
  worldId: string;
  title: string;
  running: boolean;
  readonly: boolean;
  onDeleted: () => void;
  onChanged: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  const [confirmation, setConfirmation] = useState('');
  const [preview, setPreview] = useState<UpgradePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setDeleting(false);
    setConfirmation('');
    setPreview(null);
    setNotice(null);
    setError(null);
  }, [worldId]);
  async function operation(action: () => Promise<string>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await action());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '存档操作失败');
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="archive-tools">
      <summary>存档维护</summary>
      <p className="muted small">
        备份保存在本机 data/backups。运行中的存档不能删除、压缩或升级；旧策略只在明确升级后改变。
      </p>
      <div className="editor-actions">
        <button
          disabled={busy || readonly || running}
          onClick={() =>
            void operation(async () => {
              const backup = await api.backup();
              return `本地备份已保存：${backup.filename}（${backup.size_bytes} 字节）`;
            })
          }
        >
          创建本地备份
        </button>
        <button
          disabled={busy || readonly || running}
          onClick={() =>
            void operation(async () => {
              setPreview(await api.upgradePreview(worldId));
              return '已读取升级预览。';
            })
          }
        >
          预览策略升级
        </button>
        <button
          disabled={busy || readonly || running}
          onClick={() =>
            void operation(async () => {
              const result = await api.compactJournal(worldId);
              onChanged();
              return `存档整理完成：${asText(result.compacted ?? result.retained_checkpoints ?? '已完成')}。原始数据已先备份。`;
            })
          }
        >
          整理长局存储
        </button>
        <button
          className="danger"
          disabled={busy || readonly || running}
          onClick={() => setDeleting(true)}
        >
          删除此存档
        </button>
      </div>
      {preview && (
        <div className="upgrade-preview">
          <p>
            当前策略：{asText(preview.current_policy)} → {asText(preview.target_policy)}
          </p>
          <ul>
            {preview.changes.map((change) => (
              <li key={change}>{change}</li>
            ))}
          </ul>
          {preview.blockers.map((blocker) => (
            <p key={blocker.code} className="error-text">
              {blocker.message}（{blocker.code}）
            </p>
          ))}
          <button
            disabled={busy || running || readonly || !preview.can_upgrade}
            onClick={() =>
              void operation(async () => {
                const result = await api.upgrade(worldId, preview.expected_seq);
                setPreview(null);
                onChanged();
                return `策略已升级。升级前备份：${result.backup.filename}`;
              })
            }
          >
            备份并应用此升级
          </button>
        </div>
      )}
      {deleting && (
        <div className="delete-confirmation" role="group" aria-label="确认删除存档">
          <p>将删除「{title}」及其事件、私人记忆和模型研究记录。已有本地备份保留。</p>
          <label>
            输入存档名称确认
            <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
          </label>
          <button
            className="danger"
            disabled={busy || running || readonly || confirmation !== title}
            onClick={() =>
              void operation(async () => {
                await api.deleteWorld(worldId);
                onDeleted();
                return '存档已删除。';
              })
            }
          >
            永久删除「{title}」
          </button>
          <button disabled={busy} onClick={() => setDeleting(false)}>
            取消
          </button>
        </div>
      )}
      {notice && (
        <p role="status" className="validation-success">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
    </details>
  );
}
