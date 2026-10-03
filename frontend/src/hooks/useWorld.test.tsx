import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { useWorld } from './useWorld';
import type { EventRecord, World } from '../lib/types';
vi.mock('../lib/api', () => ({
  api: {
    world: vi.fn(),
    events: vi.fn(),
    perspective: vi.fn(),
    replay: vi.fn(),
    receipts: vi.fn(),
  },
}));
class Stream extends EventTarget {
  static instances: Stream[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    super();
    Stream.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: unknown) {
    this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
}
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
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
    Stream.instances = [];
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
    vi.stubGlobal('EventSource', Stream);
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
    expect(Stream.instances).toHaveLength(0);
  });
  it('deduplicates stream events and only presents facts covered by the board snapshot', async () => {
    vi.stubGlobal('EventSource', Stream);
    vi.mocked(api.events).mockResolvedValue({
      events: [event(1), event(2), event(3)],
      next_cursor: 3,
      has_more: false,
    });
    const { result } = renderHook(() => useWorld('w', null, null));
    await waitFor(() => expect(Stream.instances).toHaveLength(1));
    const stream = Stream.instances[0];
    expect(stream.url).toContain('after_seq=3');
    act(() => {
      stream.emit('world', event(4));
      stream.emit('world', event(4));
    });
    expect(result.current.events).toHaveLength(3);
    act(() => stream.emit('snapshot', { ...world, seq: 4 }));
    expect(result.current.events.map((item) => item.seq)).toEqual([1, 2, 3, 4]);
    act(() => stream.emit('snapshot', { ...world, seq: 2 }));
    expect(result.current.world?.seq).toBe(4);
    expect(api.world).toHaveBeenCalledTimes(1);
  });
  it('closes the public stream and rejects its late frames when switching to a role', async () => {
    vi.stubGlobal('EventSource', Stream);
    vi.mocked(api.events).mockResolvedValue({
      events: [event(1)],
      next_cursor: 3,
      has_more: false,
    });
    vi.mocked(api.perspective).mockResolvedValue({
      world_id: 'w',
      piece_id: 'a',
      turn: 1,
      seq: 3,
      observation: {},
      receipts: [],
    });
    const { result, rerender } = renderHook(({ actor }) => useWorld('w', actor, null), {
      initialProps: { actor: null as string | null },
    });
    await waitFor(() => expect(Stream.instances).toHaveLength(1));
    const stream = Stream.instances[0];
    rerender({ actor: 'a' });
    await waitFor(() => expect(result.current.perspective?.piece_id).toBe('a'));
    expect(stream.closed).toBe(true);
    act(() => stream.emit('snapshot', { ...world, seq: 9 }));
    expect(result.current.world).toBeNull();
    expect(result.current.events).toEqual([]);
  });
  it('starts a long ledger near the current snapshot and fetches an old story reference on demand', async () => {
    vi.mocked(api.world).mockResolvedValue({ ...world, seq: 1500 });
    vi.mocked(api.events).mockImplementation(async (_id, after) => ({
      events:
        after === 9
          ? [{ ...event(10), event_id: 'w:10' }]
          : [{ ...event(1500), event_id: 'w:1500' }],
      next_cursor: after === 9 ? 10 : 1500,
      has_more: false,
    }));
    const { result } = renderHook(() => useWorld('w', null, null));
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(api.events).toHaveBeenCalledWith('w', 1300);
    await act(() => result.current.loadEvent('w:10'));
    expect(api.events).toHaveBeenCalledWith('w', 9);
    expect(result.current.events.map((item) => item.seq)).toEqual([10, 1500]);
  });
  it('fetches earlier owner receipt pages without any public ledger or observer request', async () => {
    const receipt = (index: number) => ({
      receipt_id: `a:${index + 1}`,
      delivered_seq: index + 1,
      text: `own-${index}`,
    });
    vi.mocked(api.perspective).mockResolvedValue({
      world_id: 'w',
      piece_id: 'a',
      turn: 1,
      seq: 5,
      observation: {},
      receipts: [receipt(3), receipt(4)],
      receipt_page: {
        total: 5,
        start_index: 3,
        end_index: 5,
        next_cursor: 5,
        has_more: false,
        has_earlier: true,
      },
    });
    vi.mocked(api.receipts).mockResolvedValue({
      world_id: 'w',
      piece_id: 'a',
      seq: 5,
      receipts: [receipt(0), receipt(1), receipt(2)],
      receipt_page: {
        total: 5,
        start_index: 0,
        end_index: 3,
        next_cursor: 3,
        has_more: true,
        has_earlier: false,
      },
    });
    const { result } = renderHook(() => useWorld('w', 'a', null));
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(result.current.perspective?.receipts).toHaveLength(2);
    await act(() => result.current.loadEarlier());
    expect(api.receipts).toHaveBeenCalledWith('w', 'a', 0);
    expect(result.current.perspective?.receipts.map((item) => item.receipt_id)).toEqual([
      'a:1',
      'a:2',
      'a:3',
      'a:4',
      'a:5',
    ]);
    expect(result.current.perspective?.receipt_page?.has_earlier).toBe(false);
    expect(api.world).not.toHaveBeenCalled();
    expect(api.events).not.toHaveBeenCalled();
  });
  it('searches past a private-only journal page when loading older public records', async () => {
    vi.mocked(api.world).mockResolvedValue({ ...world, seq: 800 });
    vi.mocked(api.events).mockImplementation(async (_id, after) => ({
      events: after === 600 ? [event(800)] : after === 599 ? [] : [event(450)],
      next_cursor: after === 600 ? 800 : after + 200,
      has_more: false,
    }));
    const { result } = renderHook(() => useWorld('w', null, null));
    await waitFor(() => expect(result.current.connected).toBe(true));
    await act(() => result.current.loadEarlier());
    expect(api.events).toHaveBeenCalledWith('w', 599);
    expect(api.events).toHaveBeenCalledWith('w', 399);
    expect(result.current.events.map((item) => item.seq)).toEqual([450, 800]);
  });
});
