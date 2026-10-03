import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import ArchiveTools from './ArchiveTools';
vi.mock('../lib/api', () => ({
  api: {
    deleteWorld: vi.fn(),
    backup: vi.fn(),
    upgradePreview: vi.fn(),
    upgrade: vi.fn(),
    compactJournal: vi.fn(),
  },
}));
describe('archive maintenance controls', () => {
  beforeEach(() => vi.clearAllMocks());
  it('requires the exact selected name and deletes only that archive', async () => {
    vi.mocked(api.deleteWorld).mockResolvedValue({ deleted: true, world_id: 'w' });
    const deleted = vi.fn();
    render(
      <ArchiveTools
        worldId="w"
        title="忒拜城"
        running={false}
        readonly={false}
        onDeleted={deleted}
        onChanged={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('存档维护'));
    fireEvent.click(screen.getByText('删除此存档'));
    expect(screen.getByRole('button', { name: '永久删除「忒拜城」' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('输入存档名称确认'), { target: { value: '忒拜城' } });
    fireEvent.click(screen.getByRole('button', { name: '永久删除「忒拜城」' }));
    await screen.findByText('存档已删除。');
    expect(api.deleteWorld).toHaveBeenCalledWith('w');
    expect(deleted).toHaveBeenCalledOnce();
  });
  it('disables mutation controls during execution and role/replay views', () => {
    const { rerender } = render(
      <ArchiveTools
        worldId="w"
        title="实验"
        running
        readonly={false}
        onDeleted={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('存档维护'));
    expect(screen.getByText('删除此存档')).toBeDisabled();
    expect(screen.getByText('整理长局存储')).toBeDisabled();
    rerender(
      <ArchiveTools
        worldId="w"
        title="实验"
        running={false}
        readonly
        onDeleted={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.queryByText('备份并应用此升级')).not.toBeInTheDocument();
    expect(screen.getByText('预览策略升级')).toBeDisabled();
    expect(api.deleteWorld).not.toHaveBeenCalled();
  });
  it('uses preview sequence and a backend backup for an explicit policy upgrade', async () => {
    vi.mocked(api.upgradePreview).mockResolvedValue({
      current_policy: 'v1',
      target_policy: 'v2',
      can_upgrade: true,
      changes: ['新会话规则'],
      blockers: [],
      backup_required: true,
      expected_seq: 27,
    });
    vi.mocked(api.upgrade).mockResolvedValue({
      world: { world_id: 'w', mode: 'fixture', seq: 28, turn: 1, lifecycle: 'PAUSED' },
      backup: {
        backup_id: 'b',
        filename: 'before.sqlite3',
        created_at: '2026-10-03',
        size_bytes: 12,
        scope: 'database',
        schema_version: '2',
      },
    });
    render(
      <ArchiveTools
        worldId="w"
        title="实验"
        running={false}
        readonly={false}
        onDeleted={vi.fn()}
        onChanged={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('存档维护'));
    fireEvent.click(screen.getByText('预览策略升级'));
    const apply = await screen.findByText('备份并应用此升级');
    fireEvent.click(apply);
    await screen.findByText(/策略已升级/);
    expect(api.upgrade).toHaveBeenCalledWith('w', 27);
  });
});
