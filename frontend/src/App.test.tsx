import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { api } from './lib/api';
import type { World } from './lib/types';
vi.mock('./lib/api', () => ({
  api: {
    health: vi.fn(),
    worlds: vi.fn(),
    world: vi.fn(),
    events: vi.fn(),
    perspective: vi.fn(),
    inspection: vi.fn(),
    scenarios: vi.fn(),
    scenario: vi.fn(),
    presets: vi.fn(),
    characters: vi.fn(),
    discipline: vi.fn(),
    replay: vi.fn(),
  },
}));
const world: World = {
  world_id: 'w1',
  title: '隔离实验',
  mode: 'fixture',
  turn: 1,
  seq: 1,
  lifecycle: 'PAUSED',
  board: { rows: 8, cols: 8 },
  pieces: {
    a: { id: 'a', name: '信使', type: 'pawn', position: { row: 1, col: 1 } },
    b: { id: 'b', name: '盲区人物哨兵', position: { row: 7, col: 7 } },
  },
  objects: {},
  conversations: {},
  battles: {},
  tombstones: {},
  stories: {},
};
describe('observatory information isolation', () => {
  beforeEach(() => {
    vi.mocked(api.scenarios).mockResolvedValue([]);
    vi.mocked(api.presets).mockResolvedValue([]);
    vi.mocked(api.characters).mockResolvedValue([]);
    vi.mocked(api.discipline).mockResolvedValue({ markdown: '# 纪律', revision: 'r1' });
    vi.mocked(api.health).mockResolvedValue({
      status: 'ok',
      model_configured: false,
      rule_version: 'demo-v2.1',
    });
    vi.mocked(api.worlds).mockResolvedValue([world]);
    vi.mocked(api.world).mockResolvedValue(world);
    vi.mocked(api.events).mockResolvedValue({
      events: [
        {
          event_id: 'e1',
          seq: 1,
          type: 'ConversationMessage',
          turn: 1,
          payload: { text: '全局发言哨兵' },
        },
      ],
      next_cursor: 1,
      has_more: false,
    });
    vi.mocked(api.perspective).mockResolvedValue({
      world_id: 'w1',
      piece_id: 'a',
      turn: 1,
      seq: 1,
      observation: {
        board: { rows: 8, cols: 8 },
        self: world.pieces && !Array.isArray(world.pieces) ? world.pieces.a : undefined,
        pieces: [],
        visible_cells: [{ row: 1, col: 1 }],
      },
      receipts: [{ text: '本人可见收件' }],
      private: { memory: '本人记忆' },
    });
    vi.mocked(api.inspection).mockClear();
  });
  it('removes observer events immediately on switching to a role and uses only its receipts', async () => {
    render(<App />);
    await screen.findByText('全局发言哨兵');
    fireEvent.change(screen.getByLabelText('观测视角'), { target: { value: 'piece:a' } });
    expect(screen.queryByText('全局发言哨兵')).not.toBeInTheDocument();
    await screen.findByText('本人可见收件', { selector: '.event-speech' });
    expect(screen.queryByRole('tab', { name: '战斗' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /盲区人物哨兵/ })).not.toBeInTheDocument();
    expect(api.inspection).not.toHaveBeenCalled();
    expect(screen.getByText('工程固定响应 · 非 AI')).toBeInTheDocument();
  });
  it('does not resolve a receipt speaker through the observer roster outside role visibility', async () => {
    const perspective = await api.perspective('w1', 'a');
    vi.mocked(api.perspective).mockResolvedValue({
      ...perspective,
      receipts: [
        {
          event_id: 'r1',
          type: 'ConversationMessage',
          turn: 1,
          payload: { speaker_id: 'b', speech: '投影台词' },
        },
      ],
    });
    render(<App />);
    await screen.findByText('全局发言哨兵');
    fireEvent.change(screen.getByLabelText('观测视角'), { target: { value: 'piece:a' } });
    await screen.findByText('投影台词');
    expect(document.querySelector('.event-actor strong')).toHaveTextContent('b');
    expect(document.querySelector('.event-actor')).not.toHaveTextContent('盲区人物哨兵');
  });
  it('loads research records only after the research view and explicit expansion', async () => {
    vi.mocked(api.inspection).mockResolvedValue({ reasoning: '授权私密推理' });
    render(<App />);
    await screen.findByText('全局发言哨兵');
    fireEvent.change(screen.getByLabelText('观测视角'), { target: { value: 'research' } });
    await screen.findByText('检查输入、输出与 API 公开推理');
    expect(api.inspection).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('检查输入、输出与 API 公开推理'));
    await waitFor(() => expect(api.inspection).toHaveBeenCalledWith('w1', 'a'));
    await screen.findByText(/授权私密推理/);
  });
  it('keeps the role itself selected when a neighboring character is visible', async () => {
    vi.mocked(api.perspective).mockResolvedValue({
      world_id: 'w1',
      piece_id: 'a',
      turn: 1,
      seq: 1,
      observation: {
        self: { id: 'a', name: '信使', type: 'pawn', position: { row: 1, col: 1 } },
        pieces: [{ id: 'b', name: '邻近人物', type: 'rook', position: { row: 1, col: 2 } }],
        visible_cells: [
          { row: 1, col: 1 },
          { row: 1, col: 2 },
        ],
      },
      receipts: [],
      private: { persona: '# 自己的人物设计\n公开目标私有哨兵。', memory: '自身经历' },
    });
    render(<App />);
    await screen.findByText('全局发言哨兵');
    fireEvent.change(screen.getByLabelText('观测视角'), { target: { value: 'piece:a' } });
    await screen.findByText('本人资料 · 仅角色视角');
    fireEvent.click(screen.getByText('个人资料'));
    await waitFor(() => expect(screen.getByText(/公开目标私有哨兵/)).toBeInTheDocument());
    expect(document.querySelector('.inspector h2')).toHaveTextContent('信使');
    expect(screen.getAllByRole('button', { name: /邻近人物/ })).toHaveLength(2);
  });
  it('supports the initial zero checkpoint and keeps simulation controls read-only', async () => {
    vi.mocked(api.replay).mockResolvedValue({ ...world, turn: 0, seq: 0 });
    render(<App />);
    await screen.findByText('全局发言哨兵');
    expect(screen.getByLabelText('历史事件')).toHaveValue(0);
    expect(screen.getByLabelText('历史事件')).toHaveAttribute('min', '0');
    fireEvent.click(screen.getByRole('button', { name: '回放' }));
    expect(screen.getByRole('button', { name: '▶ 开始' })).toBeDisabled();
    await screen.findByText('REPLAY · #0');
    expect(api.replay).toHaveBeenCalledWith('w1', 0);
    fireEvent.click(screen.getByRole('button', { name: '返回当前' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '▶ 开始' })).not.toBeDisabled());
  });
});
