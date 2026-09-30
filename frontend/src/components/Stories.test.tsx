import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Stories from './Stories';
import type { World } from '../lib/types';
describe('story evidence navigation', () => {
  it('renders paragraph prose and connects each citation to its original event', () => {
    const navigate = vi.fn();
    const world: World = {
      world_id: 'w',
      mode: 'fixture',
      turn: 2,
      seq: 12,
      lifecycle: 'PAUSED_SEGMENT',
      stories: {
        s1: {
          segment_id: 's1',
          title: '桥上',
          paragraphs: [{ text: '信使在桥边停下。', event_refs: ['e4', 'e7'] }],
        },
      },
      segments: [{ id: 's1', status: 'paused' }],
    };
    render(<Stories world={world} onReference={navigate} onRefresh={vi.fn()} />);
    expect(screen.getByText('信使在桥边停下。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'e7' }));
    expect(navigate).toHaveBeenCalledWith('e7');
    expect(screen.queryByRole('button', { name: '编排故事' })).not.toBeInTheDocument();
  });
});
