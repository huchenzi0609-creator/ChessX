import { useEffect, useRef, useState } from 'react';
import {
  entityPosition,
  pieceSymbols,
  restOf,
  type Entity,
  type Piece,
  type Position,
} from '../lib/types';
type Props = {
  rows: number;
  cols: number;
  pieces: Piece[];
  objects: Entity[];
  tombstones: Entity[];
  visibleCells?: (Position | [number, number])[];
  selectedId: string | null;
  onSelect: (id: string) => void;
};
export default function Board({
  rows,
  cols,
  pieces,
  objects,
  tombstones,
  visibleCells,
  selectedId,
  onSelect,
}: Props) {
  const [zoom, setZoom] = useState(1);
  const [center, setCenter] = useState({ x: cols / 2, y: rows / 2 });
  const drag = useRef<{ x: number; y: number; cx: number; cy: number } | null>(null);
  useEffect(() => {
    setZoom(1);
    setCenter({ x: cols / 2, y: rows / 2 });
  }, [cols, rows]);
  const span = Math.max(rows, cols) / zoom;
  const startX = center.x - span / 2,
    startY = center.y - span / 2;
  const grid = Array.from(
    { length: Math.max(rows, cols) + 1 },
    (_, n) => `M${n} 0V${rows} M0 ${n}H${cols}`,
  ).join(' ');
  const mapped = visibleCells?.map((cell) =>
    Array.isArray(cell) ? { row: cell[0], col: cell[1] } : cell,
  );
  const shown = (p: Position) =>
    !mapped || mapped.some((cell) => cell.row === p.row && cell.col === p.col);
  const near = (p: Position) =>
    p.col > startX - 1 && p.col < startX + span && p.row > startY - 1 && p.row < startY + span;
  const palette = [
    '#344b5e',
    '#927251',
    '#536f64',
    '#895b5c',
    '#6c6785',
    '#aa7c40',
    '#4f7f84',
    '#78665b',
  ];
  return (
    <section className="board-panel" aria-label="实验棋盘">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">WORLD / 棋盘</span>
          <h2>一方世界</h2>
        </div>
        <span className="muted mono">
          {rows} × {cols}
        </span>
      </div>
      <div className="board-canvas">
        <svg
          aria-label={`${rows}行${cols}列棋盘${mapped ? '，角色视野外区域不可见' : ''}`}
          viewBox={`${startX} ${startY} ${span} ${span}`}
          onPointerDown={(event) => {
            if ((event.target as Element).closest('[data-piece]')) return;
            drag.current = { x: event.clientX, y: event.clientY, cx: center.x, cy: center.y };
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            const previous = drag.current;
            if (!previous) return;
            const unit = span / event.currentTarget.getBoundingClientRect().width;
            setCenter({
              x: Math.max(0, Math.min(cols, previous.cx - (event.clientX - previous.x) * unit)),
              y: Math.max(0, Math.min(rows, previous.cy - (event.clientY - previous.y) * unit)),
            });
          }}
          onPointerUp={() => {
            drag.current = null;
          }}
          onPointerCancel={() => {
            drag.current = null;
          }}
        >
          <defs>
            <pattern id="checker" width="2" height="2" patternUnits="userSpaceOnUse">
              <rect width="2" height="2" fill="#ede9dd" />
              <path d="M0 0h1v1H0z M1 1h1v1H1z" fill="#d9d2c2" />
            </pattern>
          </defs>
          <rect
            x="0"
            y="0"
            width={cols}
            height={rows}
            rx="0.06"
            fill={mapped ? '#52616b' : 'url(#checker)'}
          />
          {mapped?.filter(near).map((cell) => (
            <rect
              key={`${cell.row}-${cell.col}`}
              x={cell.col}
              y={cell.row}
              width="1"
              height="1"
              fill="url(#checker)"
            />
          ))}
          <path
            d={grid}
            stroke={mapped ? '#d9d3c315' : '#eee9dd'}
            strokeWidth="0.014"
            fill="none"
          />
          {objects
            .filter((object) => {
              const p = entityPosition(object);
              return p && shown(p) && near(p);
            })
            .map((object) => {
              const p = entityPosition(object)!;
              return (
                <g key={object.id} aria-label={object.name ?? object.id}>
                  <title>{object.name ?? object.id}</title>
                  <path
                    d={`M${p.col + 0.75} ${p.row + 0.12}l.13 .13-.13 .13-.13-.13z`}
                    fill="#aa8042"
                    stroke="#f8f3e7"
                    strokeWidth=".02"
                  />
                </g>
              );
            })}
          {tombstones
            .filter((entity) => {
              const p = entityPosition(entity);
              return p && shown(p) && near(p);
            })
            .map((entity) => {
              const p = entityPosition(entity)!;
              return (
                <g key={entity.id}>
                  <title>{entity.name ?? '墓碑'}</title>
                  <path
                    d={`M${p.col + 0.16} ${p.row + 0.8}v-.2a.14 .14 0 0 1 .28 0v.2z`}
                    fill="#7d817d"
                  />
                </g>
              );
            })}
          {pieces
            .filter(
              (piece) =>
                piece.position &&
                piece.alive !== false &&
                piece.status !== 'dead' &&
                shown(piece.position) &&
                near(piece.position),
            )
            .map((piece) => {
              const p = piece.position!;
              const colorKey = piece.color ?? piece.id;
              const color =
                palette[
                  Array.from(colorKey).reduce((hash, letter) => hash + letter.charCodeAt(0), 0) %
                    palette.length
                ];
              const occupants = pieces.filter(
                (other) =>
                  other.position?.row === p.row &&
                  other.position?.col === p.col &&
                  other.alive !== false,
              );
              const slot = occupants.findIndex((other) => other.id === piece.id);
              const columns = occupants.length <= 4 ? 2 : 3;
              const scale = occupants.length === 1 ? 1 : occupants.length <= 4 ? 0.58 : 0.38;
              const dx = occupants.length === 1 ? 0.5 : ((slot % columns) + 0.5) / columns;
              const dy =
                occupants.length === 1
                  ? 0.5
                  : (Math.floor(slot / columns) + 0.5) / Math.ceil(occupants.length / columns);
              const transform = `translate(${p.col + dx} ${p.row + dy}) scale(${scale}) translate(${-p.col - 0.5} ${-p.row - 0.5})`;

              return (
                <g
                  key={piece.id}
                  transform={transform}
                  data-piece={piece.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`${piece.name ?? piece.id}，${p.row}行${p.col}列，休息${restOf(piece)}回合`}
                  aria-pressed={piece.id === selectedId}
                  onClick={() => onSelect(piece.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onSelect(piece.id);
                    }
                  }}
                  className="board-piece"
                >
                  <title>
                    {piece.name ?? piece.id} · {piece.color} · ({p.row}, {p.col})
                  </title>
                  {selectedId === piece.id && (
                    <circle
                      cx={p.col + 0.5}
                      cy={p.row + 0.5}
                      r=".45"
                      fill="none"
                      stroke="#b1884b"
                      strokeWidth=".045"
                    />
                  )}
                  <circle
                    cx={p.col + 0.5}
                    cy={p.row + 0.5}
                    r=".34"
                    fill="#faf8ef"
                    stroke={color}
                    strokeWidth=".035"
                  />
                  <text
                    x={p.col + 0.5}
                    y={p.row + 0.65}
                    textAnchor="middle"
                    fontSize=".47"
                    fill={color}
                  >
                    {pieceSymbols[piece.type ?? 'pawn'] ?? '●'}
                  </text>
                  {occupants.length === 1 && zoom * 8 >= Math.max(rows, cols) && (
                    <text
                      x={p.col + 0.5}
                      y={p.row + 0.94}
                      textAnchor="middle"
                      fontSize=".12"
                      fill="#243c4b"
                    >
                      {piece.name ?? piece.id}
                    </text>
                  )}
                  {restOf(piece) > 0 && (
                    <g>
                      <circle cx={p.col + 0.8} cy={p.row + 0.2} r=".12" fill="#a67a40" />
                      <text
                        x={p.col + 0.8}
                        y={p.row + 0.245}
                        fontSize=".12"
                        textAnchor="middle"
                        fill="white"
                      >
                        {restOf(piece)}
                      </text>
                    </g>
                  )}
                </g>
              );
            })}
        </svg>
      </div>
      <div className="board-footer">
        <span className="muted">拖动平移 · 点击角色检查 {mapped ? '· 雾区不含角色信息' : ''}</span>
        <div className="zoom-controls">
          <button
            aria-label="缩小棋盘"
            onClick={() => setZoom((value) => Math.max(1, value / 1.5))}
          >
            −
          </button>
          <span className="mono">{zoom.toFixed(1)}×</span>
          <button
            aria-label="放大棋盘"
            onClick={() => setZoom((value) => Math.min(16, value * 1.5))}
          >
            ＋
          </button>
          <button
            onClick={() => {
              setZoom(1);
              setCenter({ x: cols / 2, y: rows / 2 });
            }}
          >
            复位
          </button>
        </div>
      </div>
    </section>
  );
}
