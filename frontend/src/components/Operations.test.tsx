import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import Operations from './Operations';
vi.mock('../lib/api', () => ({ api: { operations: vi.fn() } }));
describe('research-only operation paging', () => {
  beforeEach(() => vi.clearAllMocks());
  it('does not load hidden research until expanded, and includes system Agents without a role filter', async () => {
    vi.mocked(api.operations).mockResolvedValue({
      operations: [
        {
          id: 'op1',
          stage: 'battle',
          actor_id: null,
          status: 'succeeded',
          source: 'source-sentinel',
        },
      ],
      next_cursor: 17,
      has_more: true,
    });
    render(<Operations worldId="w" />);
    expect(api.operations).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('所有 Agent 的分页研究记录'));
    await screen.findByText(/战斗裁定 · 系统 Agent/);
    expect(api.operations).toHaveBeenCalledWith('w', {});
    expect(screen.getByText(/source-sentinel/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('加载更早的 25 条'));
    expect(api.operations).toHaveBeenLastCalledWith('w', { before_id: 17 });
  });
});
