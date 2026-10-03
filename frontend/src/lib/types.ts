export type RecordData = Record<string, unknown>;
export type Position = { row: number; col: number };
export type Piece = RecordData & {
  id: string;
  name?: string;
  type?: string;
  color?: string;
  position?: Position;
  rest?: number;
  rest_remaining?: number;
  alive?: boolean;
  status?: string;
};
export type Entity = RecordData & {
  id: string;
  name?: string;
  position?: Position;
  location?: { kind?: string; position?: Position; holder_id?: string };
};
export type EventRecord = {
  event_id: string;
  seq: number;
  type: string;
  turn: number;
  phase?: string;
  actor_id?: string;
  payload: RecordData;
};
export type World = RecordData & {
  world_id: string;
  title?: string;
  mode: 'fixture' | 'live';
  turn: number;
  completed_turn?: number;
  seq: number;
  lifecycle: string;
  phase?: string;
  board?: { rows: number; cols: number };
  pieces?: Record<string, Piece> | Piece[];
  objects?: Record<string, Entity> | Entity[];
  tombstones?: Record<string, Entity> | Entity[];
  conversations?: Record<string, RecordData>;
  battles?: Record<string, RecordData>;
  stories?: Record<string, RecordData>;
  segments?: RecordData[] | Record<string, RecordData>;
  usage?: RecordData;
  errors?: unknown[];
  runtime?: RuntimeStatus;
};
export type RunBudget = {
  max_calls: number;
  max_total_tokens: number;
  max_wall_seconds: number;
};
export type RuntimeStatus = {
  budget?: {
    limits?: Partial<RunBudget>;
    used?: RecordData;
    remaining?: RecordData;
    cost?: { known: boolean; amount: number | null; currency: string | null };
    usage_completeness?: string;
    unknown_usage_requests?: number;
  };
  pending_requests?: RecordData[];
  waiting_reason?: string | RecordData | null;
  active_actor_id?: string | null;
  active_stage?: string | null;
  execution_policy?: string | RecordData;
  save_schema_version?: string;
};
export type BackupMetadata = {
  backup_id: string;
  filename: string;
  created_at: string;
  size_bytes: number;
  scope: string;
  schema_version: string;
};
export type UpgradePreview = {
  current_policy: string | RecordData;
  target_policy: string | RecordData;
  can_upgrade: boolean;
  blockers: { code: string; message: string }[];
  changes: string[];
  backup_required: boolean;
  expected_seq: number;
};
export type Perspective = {
  world_id: string;
  turn: number;
  seq: number;
  piece_id: string;
  observation: {
    board?: { rows: number; cols: number };
    self?: Piece;
    pieces?: Piece[] | Record<string, Piece>;
    objects?: Entity[] | Record<string, Entity>;
    tombstones?: Entity[] | Record<string, Entity>;
    visible_cells?: (Position | [number, number])[];
  };
  receipts: RecordData[];
  receipt_page?: ReceiptPage;
  private?: {
    persona?: unknown;
    memory?: unknown;
    goals?: unknown;
    memory_version?: number;
    goal_version?: number;
    travel_plan?: RecordData;
  };
};
export type ReceiptPage = {
  total: number;
  start_index: number;
  end_index: number;
  next_cursor: number | null;
  has_more: boolean;
  has_earlier: boolean;
};
export type MessagePage = {
  total: number;
  start_index: number;
  end_index: number;
  next_before_cursor: number | null;
  has_earlier: boolean;
};
export type Health = {
  status: string;
  mode?: string;
  model_configured: boolean;
  tokenizer_agreement?: boolean;
  rule_version: string;
};
export type ScenarioSummary = { id: string; title: string; description: string };
export type Validation = { valid: boolean; errors: (string | RecordData)[] };
export type PresetSummary = { id: string; title: string; description: string; readonly: boolean };
export type CharacterInfo = {
  id: string;
  name: string;
  type: string;
  color: string;
  position: Position;
};
export type CharacterSummary = CharacterInfo & { group_id: string; revision?: string };
export type CharacterDocument = {
  group_id: string;
  character: CharacterInfo;
  design_markdown: string;
  revision: string;
};
export type CharacterReference = {
  group_id: string;
  character_id: string;
  piece_id?: string;
  position?: Position;
};
export type PresetScenario = RecordData & {
  schema_version: string;
  scenario_id: string;
  title: string;
  board: { rows: number; cols: number };
  rule_profile: string;
  characters: CharacterReference[];
  objects: unknown[];
  limits?: RecordData;
};
export type PresetDetail = PresetSummary & {
  characters: CharacterSummary[];
  scenario: PresetScenario;
};
export type ScenarioDocument = { scenario: PresetScenario; revision: string };
export type CommandKind =
  'run' | 'pause' | 'resume' | 'step_event' | 'step_turn' | 'retry' | 'reset';
export const values = <T>(value: Record<string, T> | T[] | undefined): T[] =>
  value ? (Array.isArray(value) ? value : Object.values(value)) : [];
export function asText(value: unknown): string {
  return value == null ? '—' : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}
export const pieceLabels: Record<string, string> = {
  king: '王',
  queen: '后',
  rook: '车',
  bishop: '象',
  knight: '马',
  pawn: '兵',
};
export const pieceSymbols: Record<string, string> = {
  king: '♚',
  queen: '♛',
  rook: '♜',
  bishop: '♝',
  knight: '♞',
  pawn: '♟',
};
export function entityPosition(entity: Entity): Position | undefined {
  return entity.position ?? entity.location?.position;
}

export const restOf = (piece: Piece): number => piece.rest_remaining ?? piece.rest ?? 0;

export type WorldSummary = Pick<
  World,
  'world_id' | 'title' | 'mode' | 'turn' | 'completed_turn' | 'lifecycle'
> &
  Partial<World>;
