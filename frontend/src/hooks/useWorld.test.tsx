import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { useWorld } from './useWorld';
import type { EventRecord, World } from '../lib/types';
vi.mock('../lib/api', () => ({ api: { world: vi.fn(), events: vi.fn(), perspective: vi.fn() } }));
const world: World = { world_id: 'w', mode: 'fixture', turn: 1, seq: 3, lifecycle: 'PAUSED' };
const event = (seq: number): EventRecord => ({
  event_id: `e${seq}`,
  seq,
  turn: 1,
  type: 'PieceMoved',
  payload: {},
});
describe('persistent event catch-up', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.world).mockResolvedValue(world);
  });
  it('drains all ledger pages, deduplicates IDs and keeps sequence order', async () => {
    vi.mocked(api.events).mockImplementation(async (_worldId, after) =>
      after === 0
        ? { events: [event(2), event(1)], next_cursor: 2, has_more: true }
        : { events: [event(2), event(3)], next_cursor: 3, has_more: false },
    );
    const { result } = renderHook(() => useWorld('w', null, null));
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(result.current.events.map((record) => record.seq)).toEqual([1, 2, 3]);
    expect(api.events).toHaveBeenCalledWith('w', 0);
    expect(api.events).toHaveBeenCalledWith('w', 2);
  });
  it('never requests observer state or the global ledger for a role view', async () => {
    vi.mocked(api.perspective).mockResolvedValue({
      world_id: 'w',
      piece_id: 'a',
      turn: 1,
      seq: 3,
      observation: {},
      receipts: [],
    });
    const { result } = renderHook(() => useWorld('w', 'a', null));
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(result.current.world).toBeNull();
    expect(result.current.events).toEqual([]);
    expect(api.world).not.toHaveBeenCalled();
    expect(api.events).not.toHaveBeenCalled();
  });
});
