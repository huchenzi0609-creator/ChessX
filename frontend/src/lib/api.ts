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
  PresetSummary,
  PresetDetail,
  CharacterSummary,
  CharacterDocument,
  CharacterInfo,
  ScenarioDocument,
  PresetScenario,
  RunBudget,
  BackupMetadata,
  UpgradePreview,
  ReceiptPage,
  MessagePage,
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
const put = (body: unknown): RequestInit => ({ method: 'PUT', body: JSON.stringify(body) });
export const api = {
  health: () => request<Health>('/health'),
  presets: () => request<PresetSummary[]>('/presets'),
  preset: (groupId: string) => request<PresetDetail>(`/presets/${id(groupId)}`),
  createPreset: (body: { id: string; title: string; description: string }) =>
    request<PresetSummary>('/presets', post(body)),
  characters: () => request<CharacterSummary[]>('/characters'),
  character: (groupId: string, characterId: string) =>
    request<CharacterDocument>(`/presets/${id(groupId)}/characters/${id(characterId)}`),
  saveCharacter: (
    groupId: string,
    character: CharacterInfo,
    designMarkdown: string,
    revision?: string,
  ) =>
    request<CharacterDocument>(
      `/presets/${id(groupId)}/characters/${id(character.id)}`,
      put({
        character,
        design_markdown: designMarkdown,
        ...(revision ? { expected_revision: revision } : {}),
      }),
    ),
  presetScenario: (groupId: string) =>
    request<ScenarioDocument>(`/presets/${id(groupId)}/scenario`),
  savePresetScenario: (groupId: string, scenario: PresetScenario, revision: string) =>
    request<ScenarioDocument>(
      `/presets/${id(groupId)}/scenario`,
      put({ scenario, expected_revision: revision }),
    ),
  validatePreset: (groupId: string) =>
    request<Validation>(`/presets/${id(groupId)}/validate`, post({})),
  createFromPreset: (presetId: string, mode: 'fixture' | 'live') =>
    request<World>('/worlds/from-preset', post({ preset_id: presetId, mode })),
  discipline: () => request<{ markdown: string; revision: string }>('/discipline'),
  scenarios: () => request<ScenarioSummary[]>('/scenarios'),
  scenario: (scenarioId: string) => request<RecordData>(`/scenarios/${id(scenarioId)}`),
  validate: (scenario: unknown) => request<Validation>('/scenarios/validate', post(scenario)),
  create: (scenario: unknown, mode: 'fixture' | 'live') =>
    request<World>('/worlds', post({ scenario, mode })),
  worlds: () => request<WorldSummary[]>('/worlds'),
  world: (worldId: string) => request<World>(`/worlds/${id(worldId)}?view=observer`),
  perspective: (worldId: string, pieceId: string) =>
    request<Perspective>(`/worlds/${id(worldId)}/perspectives/${id(pieceId)}`),
  receipts: (worldId: string, pieceId: string, afterReceipt: number) =>
    request<{
      world_id: string;
      piece_id: string;
      seq: number;
      receipts: RecordData[];
      receipt_page: ReceiptPage;
    }>(
      `/worlds/${id(worldId)}/perspectives/${id(pieceId)}/receipts?after_receipt=${afterReceipt}&limit=200`,
    ),
  conversationMessages: (worldId: string, conversationId: string, beforeIndex: number) =>
    request<{
      conversation_id: string;
      seq: number;
      messages: RecordData[];
      message_page: MessagePage;
    }>(
      `/worlds/${id(worldId)}/conversations/${id(conversationId)}/messages?before_index=${beforeIndex}&limit=50`,
    ),
  inspection: (worldId: string, pieceId: string) =>
    request<RecordData>(`/worlds/${id(worldId)}/inspection/${id(pieceId)}`),
  operations: (
    worldId: string,
    filters: { actor_id?: string; stage?: string; before_id?: number },
  ) => {
    const query = new URLSearchParams({
      limit: '25',
      ...(filters.actor_id ? { actor_id: filters.actor_id } : {}),
      ...(filters.stage ? { stage: filters.stage } : {}),
    });
    if (filters.before_id !== undefined) query.set('before_id', String(filters.before_id));
    return request<{ operations: RecordData[]; next_cursor: number | null; has_more: boolean }>(
      `/worlds/${id(worldId)}/inspection?${query}`,
    );
  },
  deleteWorld: (worldId: string) =>
    request<{ deleted: boolean; world_id: string }>(`/worlds/${id(worldId)}`, { method: 'DELETE' }),
  backup: () => request<BackupMetadata>('/backups', post({})),
  upgradePreview: (worldId: string) =>
    request<UpgradePreview>(`/worlds/${id(worldId)}/upgrade-policy`),
  upgrade: (worldId: string, expectedSeq: number) =>
    request<{ world: World; backup: BackupMetadata }>(
      `/worlds/${id(worldId)}/upgrade-policy`,
      post({ expected_seq: expectedSeq }),
    ),
  compactJournal: (worldId: string) =>
    request<RecordData>(`/worlds/${id(worldId)}/compact-journal`, post({})),
  events: (worldId: string, afterSeq: number) =>
    request<{ events: EventRecord[]; next_cursor: number; has_more: boolean }>(
      `/worlds/${id(worldId)}/events?after_seq=${afterSeq}&limit=200`,
    ),
  command: (worldId: string, kind: CommandKind, turns?: number, budget?: RunBudget) =>
    request<RecordData>(
      `/worlds/${id(worldId)}/commands`,
      post({
        command_id: crypto.randomUUID(),
        kind,
        ...(turns !== undefined ? { turns } : {}),
        ...(budget ? { budget } : {}),
      }),
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
    const archive = await request<{ download_url: string; filename: string }>(
      `/worlds/${id(worldId)}/export?download=prepare`,
      post({ view }),
    );
    if (!/^\/api\/exports\/[A-Za-z0-9_-]+$/.test(archive.download_url))
      throw new ApiError(500, '导出下载地址无效');
    const anchor = document.createElement('a');
    anchor.href = archive.download_url;
    anchor.download = archive.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  },
};
