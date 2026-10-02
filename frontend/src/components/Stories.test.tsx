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
      phase: 'BARD',
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
    expect(screen.getByRole('button', { name: '重编本段' })).toBeInTheDocument();
  });
  it('shows complete ordered chapters with original citations and headings', () => {
    const navigate = vi.fn();
    const world: World = {
      world_id: 'w',
      mode: 'live',
      turn: 16,
      seq: 710,
      lifecycle: 'PAUSED_SEGMENT',
      stories: {
        s1: {
          segment_id: 's1',
          title: '城中的一天',
          composition: 'ordered_chapters',
          chapter_count: 2,
          chapters: [
            { title: '桥边', paragraph_start: 0, paragraph_count: 1 },
            { title: '归来', paragraph_start: 1, paragraph_count: 1 },
          ],
          paragraphs: [
            { text: '完整的第一章。', event_refs: ['w:2'] },
            { text: '完整的第二章。', event_refs: ['w:3'] },
          ],
        },
      },
      segments: [{ id: 's1', status: 'paused' }],
    };
    render(<Stories world={world} onReference={navigate} onRefresh={vi.fn()} />);
    expect(screen.getByText('分章成稿 · 2章')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '桥边' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '归来' })).toBeInTheDocument();
    expect(screen.getByText('完整的第一章。')).toBeInTheDocument();
    expect(screen.getByText('完整的第二章。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'w:3' }));
    expect(navigate).toHaveBeenCalledWith('w:3');
  });
  it('labels pending story work as continuation and prevents replacing an unfinished action', () => {
    const world: World = {
      world_id: 'w',
      mode: 'live',
      turn: 16,
      seq: 710,
      phase: 'BARD',
      lifecycle: 'PAUSED_ERROR',
      segments: [{ id: 's1', status: 'running', source_end_seq: 700 }],
    };
    const { rerender } = render(
      <Stories world={world} onReference={vi.fn()} onRefresh={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '继续编排' })).toBeEnabled();
    rerender(
      <Stories world={{ ...world, phase: 'PRIMARY' }} onReference={vi.fn()} onRefresh={vi.fn()} />,
    );
    expect(screen.getByRole('button', { name: '继续编排' })).toBeDisabled();
    rerender(
      <Stories
        world={{ ...world, lifecycle: 'RUNNING' }}
        onReference={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: '正在编排…' })).toBeDisabled();
  });
});
