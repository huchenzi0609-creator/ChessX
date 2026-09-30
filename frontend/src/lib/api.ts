import type {
  CommandKind,
  EventRecord,
  Health,
  Perspective,
  RecordData,
  ScenarioSummary,
  Validation,
  World,
  WorldSummary,
} from './types';
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new ApiError(0, '无法连接本地服务。请确认后端已启动，稍后会自动重新连接。');
  }
  if (!response.ok) {
    let detail = `请求失败（HTTP ${response.status}）`;
    try {
      const body = await response.json();
      const value =
        body.detail ??
        body.error ??
        (body.code ? `${body.code}${body.message ? ` · ${body.message}` : ''}` : body.message);
      if (value) detail = typeof value === 'string' ? value : JSON.stringify(value);
    } catch {
      /* Non-JSON errors contain no useful contract data. */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}
const id = encodeURIComponent;
const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });
export const api = {
  health: () => request<Health>('/health'),
  scenarios: () => request<ScenarioSummary[]>('/scenarios'),
  scenario: (scenarioId: string) => request<RecordData>(`/scenarios/${id(scenarioId)}`),
  validate: (scenario: unknown) => request<Validation>('/scenarios/validate', post(scenario)),
  create: (scenario: unknown, mode: 'fixture' | 'live') =>
    request<World>('/worlds', post({ scenario, mode })),
  worlds: () => request<WorldSummary[]>('/worlds'),
  world: (worldId: string) => request<World>(`/worlds/${id(worldId)}?view=observer`),
  perspective: (worldId: string, pieceId: string) =>
    request<Perspective>(`/worlds/${id(worldId)}/perspectives/${id(pieceId)}`),
  inspection: (worldId: string, pieceId: string) =>
    request<RecordData>(`/worlds/${id(worldId)}/inspection/${id(pieceId)}`),
  events: (worldId: string, afterSeq: number) =>
    request<{ events: EventRecord[]; next_cursor: number; has_more: boolean }>(
      `/worlds/${id(worldId)}/events?after_seq=${afterSeq}&limit=200`,
    ),
  command: (worldId: string, kind: CommandKind, turns?: number) =>
    request<RecordData>(
      `/worlds/${id(worldId)}/commands`,
      post({ command_id: crypto.randomUUID(), kind, ...(turns !== undefined ? { turns } : {}) }),
    ),
  commandStatus: (worldId: string, commandId: string) =>
    request<RecordData>(`/worlds/${id(worldId)}/commands/${id(commandId)}`),
  replay: (worldId: string, seq: number) =>
    request<World>(`/worlds/${id(worldId)}/replay?at_seq=${seq}`),
  story: (worldId: string, segmentId: string) =>
    request<RecordData>(`/worlds/${id(worldId)}/segments/${id(segmentId)}/story`),
  generateStory: (worldId: string, segmentId: string) =>
    request<RecordData>(`/worlds/${id(worldId)}/segments/${id(segmentId)}/story`, post({})),
  async export(worldId: string, view: 'observer' | 'research') {
    const response = await fetch(`/api/worlds/${id(worldId)}/export`, {
      ...post({ view }),
      headers: { 'Content-Type': 'application/json' },
    });
    if (!response.ok) throw new ApiError(response.status, `导出失败（HTTP ${response.status}）`);
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `chessx-${worldId}-${view}.zip`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
