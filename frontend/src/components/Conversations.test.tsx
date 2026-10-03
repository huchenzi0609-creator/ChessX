import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Conversations from './Conversations';
import type { EventRecord } from '../lib/types';
import { api } from '../lib/api';
vi.mock('../lib/api', () => ({ api: { conversationMessages: vi.fn() } }));
const names = { a: '王后', b: '信使', c: '牧人' };
describe('conversation continuity and ordering', () => {
  it('pins active conversations and sorts archives by recent activation, retaining silent participants', () => {
    const { container } = render(
      <Conversations
        currentTurn={10}
        names={names}
        conversations={[
          {
            id: 'old',
            status: 'closed',
            members: [],
            participant_ids: ['a', 'c'],
            closed_turn: 3,
            messages: [{ speaker_id: 'a', speech: '那时我还不知道。' }],
          },
          {
            id: 'recent',
            status: 'closed',
            members: [],
            participant_ids: ['b'],
            last_active_turn: 8,
            messages: [],
          },
          {
            id: 'active',
            status: 'active',
            members: ['a', 'b'],
            participant_ids: ['c'],
            created_turn: 1,
            messages: [{ speaker_id: 'b', speech: '我刚回来。' }],
          },
        ]}
      />,
    );
    const cards = [...container.querySelectorAll('.dialog-card')];
    expect(cards.map((card) => card.getAttribute('data-conversation-id'))).toEqual([
      'active',
      'recent',
      'old',
    ]);
    expect(within(cards[0] as HTMLElement).getByRole('heading')).toHaveTextContent('王后、信使');
    expect(within(cards[0] as HTMLElement).getByText('● 正在交谈')).toBeInTheDocument();
    expect(within(cards[1] as HTMLElement).getByText('最近活跃 T8 · 2 回合前')).toBeInTheDocument();
    expect(within(cards[2] as HTMLElement).getByRole('heading')).toHaveTextContent('王后、牧人');
    expect(screen.getByText('我刚回来。')).toBeInTheDocument();
  });
  it('recovers activity from events for legacy archives and participants from messages', () => {
    const events: EventRecord[] = [
      {
        event_id: 'e1',
        seq: 1,
        type: 'ConversationClosed',
        turn: 9,
        payload: { conversation_id: 'legacy' },
      },
    ];
    const { container } = render(
      <Conversations
        currentTurn={10}
        names={names}
        events={events}
        conversations={[
          { id: 'older', status: 'closed', created_turn: 5 },
          {
            id: 'legacy',
            status: 'closed',
            members: [],
            created_turn: 1,
            messages: [{ speaker_id: 'c', speech: '我在这里。' }],
          },
        ]}
      />,
    );
    expect(container.querySelector('.dialog-card')).toHaveAttribute(
      'data-conversation-id',
      'legacy',
    );
    expect(screen.getByRole('heading', { name: '牧人' })).toBeInTheDocument();
    expect(screen.getByText('最近活跃 T9 · 1 回合前')).toBeInTheDocument();
  });
  it('moves a conversation to the front on reactivation without changing the input collection', () => {
    const conversations = [
      { id: 'first', status: 'closed', members: ['a'], closed_turn: 5 },
      { id: 'second', status: 'closed', members: ['b'], closed_turn: 3 },
    ];
    const { container, rerender } = render(
      <Conversations currentTurn={7} names={names} conversations={conversations} />,
    );
    rerender(
      <Conversations
        currentTurn={7}
        names={names}
        conversations={[
          conversations[0],
          { ...conversations[1], status: 'active', last_active_turn: 7 },
        ]}
      />,
    );
    expect(container.querySelector('.dialog-card')).toHaveAttribute(
      'data-conversation-id',
      'second',
    );
    expect(conversations.map((conversation) => conversation.id)).toEqual(['first', 'second']);
  });
  it('loads older message pages and keeps an already shown line when the live tail advances', async () => {
    const messages = (start: number, count: number) =>
      Array.from({ length: count }, (_, offset) => ({
        id: `m${start + offset}`,
        speaker_id: 'a',
        speech: `line-${start + offset}`,
      }));
    const dialog = {
      id: 'c',
      status: 'active',
      members: ['a'],
      messages: messages(50, 50),
      message_page: { total: 100, start_index: 50, has_earlier: true },
    };
    vi.mocked(api.conversationMessages).mockResolvedValue({
      conversation_id: 'c',
      seq: 100,
      messages: messages(0, 50),
      message_page: {
        total: 100,
        start_index: 0,
        end_index: 50,
        next_before_cursor: null,
        has_earlier: false,
      },
    });
    const { rerender } = render(
      <Conversations worldId="w" currentTurn={10} names={names} conversations={[dialog]} />,
    );
    fireEvent.click(screen.getByText('显示本会话更早的 30 句'));
    fireEvent.click(screen.getByText('读取本会话更早的 50 句'));
    await screen.findByText('line-0');
    expect(api.conversationMessages).toHaveBeenCalledWith('w', 'c', 50);
    rerender(
      <Conversations
        worldId="w"
        currentTurn={11}
        names={names}
        conversations={[
          {
            ...dialog,
            messages: messages(51, 50),
            message_page: { total: 101, start_index: 51, has_earlier: true },
          },
        ]}
      />,
    );
    expect(screen.getByText('line-50')).toBeInTheDocument();
    expect(screen.getByText('line-100')).toBeInTheDocument();
  });
});
