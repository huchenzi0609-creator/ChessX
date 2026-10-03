import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import RuntimePanel, { BudgetEditor } from './RuntimePanel';
describe('runtime budget projection', () => {
  it('shows recorded tokens separately from requests without usage and unknown currency cost', () => {
    render(
      <RuntimePanel
        names={{ a: '信使' }}
        world={{
          world_id: 'w',
          mode: 'live',
          turn: 3,
          seq: 7,
          lifecycle: 'RUNNING',
          runtime: {
            budget: {
              limits: { max_calls: 50, max_total_tokens: 90000, max_wall_seconds: 180 },
              used: { calls: 12, tokens: 18000, wall_seconds: 18 },
              remaining: { calls: 38, tokens: 72000, wall_seconds: 162 },
              unknown_usage_requests: 2,
              cost: { known: false, amount: null, currency: null },
            },
            pending_requests: [{ stage: 'ordinary', actor_id: 'a', attempt: 1, status: 'running' }],
            waiting_reason: 'model_request',
          },
        }}
      />,
    );
    expect(screen.getByText('18,000 / 90,000')).toBeInTheDocument();
    expect(screen.getByText('金额未知')).toBeInTheDocument();
    expect(screen.getByText(/未返回用量的请求：2/)).toBeInTheDocument();
    expect(screen.getByText(/等待模型响应/)).toBeInTheDocument();
    expect(screen.getByText(/信使 · 普通交谈 · 进行中/)).toBeInTheDocument();
  });
  it('clamps editable limits to the server contract without mutating authoritative state', () => {
    const update = vi.fn();
    render(
      <BudgetEditor
        budget={{ max_calls: 20, max_total_tokens: 4096, max_wall_seconds: 120 }}
        onChange={update}
      />,
    );
    fireEvent.change(screen.getByLabelText('最大模型请求'), { target: { value: '12000' } });
    expect(update).toHaveBeenCalledWith({
      max_calls: 10000,
      max_total_tokens: 4096,
      max_wall_seconds: 120,
    });
  });
});
