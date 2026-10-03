import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';
import type { EventRecord, Perspective, RecordData, World } from '../lib/types';
const RETAINED_EVENTS = 1000;
const FALLBACK_MS = 5000;
/** A view owns its cache. SSE carries only observer DTOs; a role never opens that stream. */
export function useWorld(
  worldId: string | null,
  perspectiveId: string | null,
  replaySeq: number | null,
) {
  const [world, setWorld] = useState<World | null>(null);
  const [perspective, setPerspective] = useState<Perspective | null>(null);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const loadReference = useRef<(eventId: string) => Promise<void>>(async () => {});
  const loadHistory = useRef<() => Promise<void>>(async () => {});
  const loadEvent = useCallback((eventId: string) => loadReference.current(eventId), []);
  const loadEarlier = useCallback(() => loadHistory.current(), []);
  useEffect(() => {
    setPerspective(null);
    setEvents([]);
    setWorld(null);
    setConnected(false);
    setError(null);
  }, [worldId, perspectiveId, replaySeq]);
  useEffect(() => {
    if (!worldId) return;
    let active = true;
    let fetching = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let source: EventSource | undefined;
    let cursor = 0;
    let snapshotSeq = -1;
    let pinnedReference: string | null = null;
    let archiveWindow = new Set<string>();
    const ledger = new Map<string, EventRecord>();
    const receiptLedger = new Map<string, { index: number; receipt: RecordData }>();
    let receiptArchiveWindow = new Set<string>();
    let latestPerspective: Perspective | null = null;
    const putReceipts = (receipts: RecordData[], start: number) => {
      receipts.forEach((receipt, offset) =>
        receiptLedger.set(
          String(receipt.receipt_id ?? `${perspectiveId}:index:${start + offset}`),
          { index: start + offset, receipt },
        ),
      );
    };
    const publishReceipts = () => {
      if (!active || !latestPerspective) return;
      const total = latestPerspective.receipt_page?.total ?? latestPerspective.receipts.length;
      for (const [id, entry] of receiptLedger)
        if (entry.index < total - 400 && !receiptArchiveWindow.has(id)) receiptLedger.delete(id);
      const entries = [...receiptLedger.values()].sort((a, b) => a.index - b.index);
      setPerspective({
        ...latestPerspective,
        receipts: entries.map((entry) => entry.receipt),
        ...(latestPerspective.receipt_page
          ? {
              receipt_page: {
                ...latestPerspective.receipt_page,
                has_earlier: (entries[0]?.index ?? 0) > 0,
              },
            }
          : {}),
      });
    };
    const publish = () => {
      const ordered = [...ledger.values()]
        .filter((event) => event.seq <= snapshotSeq)
        .sort((a, b) => a.seq - b.seq);
      const visible = ordered.slice(-RETAINED_EVENTS);
      for (const event of ordered)
        if (
          archiveWindow.has(event.event_id) &&
          !visible.some((item) => item.event_id === event.event_id)
        )
          visible.unshift(event);
      visible.sort((a, b) => a.seq - b.seq);
      const pinned = pinnedReference ? ledger.get(pinnedReference) : undefined;
      if (pinned && !visible.some((event) => event.event_id === pinned.event_id))
        visible.unshift(pinned);
      if (active) setEvents(visible);
      const keep = new Set(visible.map((event) => event.event_id));
      for (const [id, event] of ledger)
        if (event.seq <= snapshotSeq && !keep.has(id)) ledger.delete(id);
    };
    const put = (event: EventRecord) => {
      if (
        typeof event.event_id === 'string' &&
        Number.isInteger(event.seq) &&
        event.seq >= 0 &&
        (replaySeq === null || event.seq <= replaySeq)
      )
        ledger.set(event.event_id, event);
    };
    async function catchUp(until: number, initial: boolean) {
      if (initial) cursor = Math.max(0, until - 200);
      while (active && cursor < until) {
        const page = await api.events(worldId!, cursor);
        if (!active) return;
        page.events.filter((event) => event.seq <= until).forEach(put);
        const nextCursor = Math.min(Number(page.next_cursor), until);
        if (!Number.isFinite(nextCursor) || nextCursor <= cursor) break;
        cursor = nextCursor;
        if (!page.has_more) break;
      }
      cursor = Math.max(cursor, until);
    }
    async function fetchSnapshot(initial = false) {
      if (fetching || !active) return;
      fetching = true;
      try {
        if (perspectiveId) {
          const next = await api.perspective(worldId!, perspectiveId);
          if (active && next.world_id === worldId && next.piece_id === perspectiveId) {
            latestPerspective = next;
            putReceipts(next.receipts, next.receipt_page?.start_index ?? 0);
            publishReceipts();
            setWorld(null);
            setEvents([]);
          }
        } else {
          const next =
            replaySeq === null ? await api.world(worldId!) : await api.replay(worldId!, replaySeq);
          await catchUp(next.seq, initial);
          if (active && next.world_id === worldId && next.seq >= snapshotSeq) {
            snapshotSeq = next.seq;
            setWorld(next);
            setPerspective(null);
            publish();
          }
        }
        if (active) {
          setConnected(true);
          setError(null);
        }
      } catch (cause) {
        if (active) {
          setConnected(false);
          setError(cause instanceof Error ? cause.message : '读取世界失败');
        }
      } finally {
        fetching = false;
      }
    }
    const scheduleFallback = () => {
      clearTimeout(timer);
      if (active && replaySeq === null)
        timer = setTimeout(async () => {
          await fetchSnapshot();
          scheduleFallback();
        }, FALLBACK_MS);
    };
    loadReference.current = async (eventId: string) => {
      if (!active || perspectiveId) return;
      pinnedReference = eventId;
      if (!ledger.has(eventId)) {
        const seq = Number(eventId.split(':').at(-1));
        if (!Number.isInteger(seq) || seq < 0 || seq > snapshotSeq) return;
        const page = await api.events(worldId!, Math.max(0, seq - 1));
        if (!active) return;
        page.events.filter((event) => event.seq <= snapshotSeq).forEach(put);
      }
      publish();
    };
    loadHistory.current = async () => {
      if (!active) return;
      if (perspectiveId) {
        if (!receiptLedger.size || !latestPerspective) return;
        const earliest = Math.min(...[...receiptLedger.values()].map((entry) => entry.index));
        if (earliest <= 0) return;
        const page = await api.receipts(worldId!, perspectiveId, Math.max(0, earliest - 200));
        if (!active || page.world_id !== worldId || page.piece_id !== perspectiveId) return;
        const earlier = page.receipts.filter(
          (_receipt, offset) => page.receipt_page.start_index + offset < earliest,
        );
        receiptArchiveWindow = new Set(
          earlier.map((receipt, offset) =>
            String(
              receipt.receipt_id ??
                `${perspectiveId}:index:${page.receipt_page.start_index + offset}`,
            ),
          ),
        );
        putReceipts(earlier, page.receipt_page.start_index);
        publishReceipts();
        return;
      }
      if (!ledger.size) return;
      const earliest = Math.min(...[...ledger.values()].map((event) => event.seq));
      if (earliest <= 1) return;
      // Pages can contain only private journal entries. Search backward through
      // those holes instead of getting stuck on the same empty public page.
      let before = earliest;
      let earlier: EventRecord[] = [];
      while (active && before > 1 && earlier.length === 0) {
        const after = Math.max(0, before - 201);
        const page = await api.events(worldId!, after);
        if (!active) return;
        earlier = page.events.filter((event) => event.seq < before && event.seq <= snapshotSeq);
        before = after + 1;
      }
      archiveWindow = new Set(earlier.map((event) => event.event_id));
      earlier.forEach(put);
      publish();
    };
    async function start() {
      await fetchSnapshot(true);
      if (!active || replaySeq !== null) return;
      if (perspectiveId || typeof EventSource === 'undefined') {
        scheduleFallback();
        return;
      }
      source = new EventSource(
        `/api/worlds/${encodeURIComponent(worldId!)}/stream?after_seq=${cursor}`,
      );
      source.onopen = () => {
        if (active) {
          clearTimeout(timer);
          setConnected(true);
          setError(null);
        }
      };
      source.addEventListener('world', (message) => {
        if (!active) return;
        try {
          put(JSON.parse((message as MessageEvent).data) as EventRecord);
          publish();
        } catch {
          /* A malformed frame does not change an acknowledged snapshot. */
        }
      });
      source.addEventListener('cursor', (message) => {
        const next = Number((message as MessageEvent).data);
        if (Number.isInteger(next) && next >= cursor) cursor = next;
      });
      source.addEventListener('snapshot', (message) => {
        if (!active) return;
        try {
          const next = JSON.parse((message as MessageEvent).data) as World;
          if (next.world_id !== worldId || !Number.isInteger(next.seq) || next.seq < snapshotSeq)
            return;
          snapshotSeq = next.seq;
          cursor = Math.max(cursor, next.seq);
          setWorld(next);
          setPerspective(null);
          setConnected(true);
          setError(null);
          publish();
        } catch {
          /* Keep the last complete DTO; fallback can recover an invalid frame. */
        }
      });
      source.onerror = () => {
        if (active) {
          setConnected(false);
          scheduleFallback();
        }
      };
      source.addEventListener('deleted', () => {
        if (!active) return;
        clearTimeout(timer);
        source?.close();
        setConnected(false);
        setWorld(null);
        setEvents([]);
        setError('此存档已被删除，请选择其他实验。');
      });
    }
    void start();
    return () => {
      active = false;
      clearTimeout(timer);
      source?.close();
      loadReference.current = async () => {};
      loadHistory.current = async () => {};
    };
  }, [worldId, perspectiveId, replaySeq, revision]);
  return { world, perspective, events, error, connected, refresh, loadEvent, loadEarlier };
}
