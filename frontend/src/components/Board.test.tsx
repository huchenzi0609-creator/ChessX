import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import Board from './Board';
describe('role board boundaries', () => {
  it('never renders pieces outside the role visible cells, including accessible labels', () => {
    render(
      <Board
        rows={64}
        cols={64}
        pieces={[
          { id: 'self', name: '本人', type: 'pawn', position: { row: 2, col: 2 } },
          { id: 'secret', name: '不可见姓名哨兵', type: 'rook', position: { row: 60, col: 60 } },
        ]}
        objects={[]}
        tombstones={[]}
        visibleCells={[{ row: 2, col: 2 }]}
        selectedId={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /本人/ })).toBeInTheDocument();
    expect(screen.queryByText(/不可见姓名哨兵/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /不可见姓名哨兵/ })).not.toBeInTheDocument();
  });
  it('supports keyboard selection and zooming without changing world state', () => {
    const select = vi.fn();
    render(
      <Board
        rows={8}
        cols={8}
        pieces={[{ id: 'a', name: '信使', position: { row: 2, col: 2 } }]}
        objects={[]}
        tombstones={[]}
        selectedId={null}
        onSelect={select}
      />,
    );
    fireEvent.keyDown(screen.getByRole('button', { name: /信使/ }), { key: 'Enter' });
    expect(select).toHaveBeenCalledWith('a');
    fireEvent.click(screen.getByRole('button', { name: '放大棋盘' }));
    expect(screen.getByText('1.5×')).toBeInTheDocument();
  });
});
