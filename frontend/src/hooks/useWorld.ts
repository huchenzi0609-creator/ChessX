import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { EventRecord, Perspective, World } from '../lib/types';
/** Poll the persistent ledger in pages, preserving every event across disconnects. */
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
    let timer: ReturnType<typeof setTimeout>;
    let cursor = 0;
    const eventsById = new Map<string, EventRecord>();
    async function poll() {
      try {
        if (perspectiveId) {
          const next = await api.perspective(worldId!, perspectiveId);
          if (active) {
            setPerspective(next);
            setWorld(null);
            setEvents([]);
          }
        } else {
          const next =
            replaySeq === null ? await api.world(worldId!) : await api.replay(worldId!, replaySeq);
          let more = true;
          while (more && active) {
            const page = await api.events(worldId!, cursor);
            for (const event of page.events)
              if (replaySeq === null || event.seq <= replaySeq)
                eventsById.set(event.event_id, event);
            const nextCursor = Number(page.next_cursor);
            if (!Number.isFinite(nextCursor) || nextCursor <= cursor) more = false;
            else {
              cursor = nextCursor;
              more = page.has_more && (replaySeq === null || cursor < replaySeq);
            }
          }
          if (active) {
            setWorld(next);
            setPerspective(null);
            setEvents([...eventsById.values()].sort((a, b) => a.seq - b.seq));
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
      }
      if (active && replaySeq === null) timer = setTimeout(poll, 1200);
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [worldId, perspectiveId, replaySeq, revision]);
  return { world, perspective, events, error, connected, refresh };
}
