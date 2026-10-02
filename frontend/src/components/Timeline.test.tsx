import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Timeline from './Timeline';
import type { EventRecord } from '../lib/types';
const event = (type: string, payload: Record<string, unknown>, seq = 1): EventRecord => ({
  event_id: `e${seq}`,
  seq,
  type,
  turn: 2,
  actor_id: 'a',
  payload,
});
const props = {
  highlightedEvent: null,
  onEvent: vi.fn(),
  names: { a: '信使', b: '王后' },
  pieceTypes: { a: 'rook', b: 'knight' },
};
describe('event readability', () => {
  it('identifies each dialogue speaker including negotiation and settlement', () => {
    render(
      <Timeline
        {...props}
        events={[
          event('ConversationMessage', { speaker_id: 'b', speech: '请进。' }),
          event('NegotiationMessage', { speaker_id: 'a', speech: '借过。' }, 2),
          event(
            'SettlementMessage',
            { speaker_id: 'c', speaker_name: '牧人', speech: '我认输。' },
            3,
          ),
        ]}
      />,
    );
    expect(screen.getByText('王后', { selector: '.event-actor strong' })).toBeInTheDocument();
    expect(screen.getByText('信使', { selector: '.event-actor strong' })).toBeInTheDocument();
    expect(screen.getByText('牧人', { selector: '.event-actor strong' })).toBeInTheDocument();
  });
  it('uses the actual committed partial path instead of reconstructing a requested destination', () => {
    render(
      <Timeline
        {...props}
        events={[
          event('MoveCommitted', {
            piece_id: 'a',
            path: [
              { row: 1, col: 0 },
              { row: 1, col: 1 },
            ],
            origin: { row: 1, col: 0 },
            position: { row: 1, col: 1 },
            to: { row: 1, col: 5 },
          }),
        ]}
      />,
    );
    expect(
      screen.getByText(/\(1, 0\) → \(1, 1\)/, { selector: '.event-path' }),
    ).toBeInTheDocument();
    expect(screen.getByText('信使', { selector: '.event-actor strong' })).toBeInTheDocument();
    expect(document.querySelector('.event-path')).not.toHaveTextContent('(1, 5)');
  });
  it('reconstructs sliding history from complete endpoints while horses only have two cells', () => {
    render(
      <Timeline
        {...props}
        events={[
          event('PieceMoved', { piece_id: 'a', from: { row: 1, col: 0 }, to: { row: 1, col: 3 } }),
          event(
            'MoveCommitted',
            { piece_id: 'b', origin: { row: 0, col: 0 }, position: { row: 2, col: 1 } },
            2,
          ),
        ]}
      />,
    );
    expect(
      screen.getByText(/\(1, 0\) → \(1, 1\) → \(1, 2\) → \(1, 3\)/, { selector: '.event-path' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/\(0, 0\) → \(2, 1\)/, { selector: '.event-path' }),
    ).toBeInTheDocument();
  });
  it('never fills a projected visible-only route with hidden intermediate cells', () => {
    render(
      <Timeline
        {...props}
        receipts={[
          {
            ...event('MoveCommitted', {
              piece_id: 'a',
              path_visible_only: true,
              path: [{ row: 1, col: 2 }],
              origin: { row: 1, col: 0 },
              position: { row: 1, col: 3 },
            }),
            receipt_id: 'r1',
          },
        ]}
      />,
    );
    const route = document.querySelector('.event-path');
    expect(route).toHaveTextContent('可见路段');
    expect(route).toHaveTextContent('(1, 2)');
    expect(route).not.toHaveTextContent('(1, 0)');
    expect(route).not.toHaveTextContent('(1, 1)');
    expect(route).not.toHaveTextContent('(1, 3)');
  });
});
