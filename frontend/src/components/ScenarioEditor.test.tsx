import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../lib/api';
import type { CharacterSummary, Health, PresetScenario, World } from '../lib/types';
import ScenarioEditor from './ScenarioEditor';
vi.mock('../lib/api', async (original) => ({
  ...(await original<typeof import('../lib/api')>()),
  api: {
    presets: vi.fn(),
    characters: vi.fn(),
    discipline: vi.fn(),
    presetScenario: vi.fn(),
    createPreset: vi.fn(),
    character: vi.fn(),
    saveCharacter: vi.fn(),
    savePresetScenario: vi.fn(),
    validatePreset: vi.fn(),
    createFromPreset: vi.fn(),
    validate: vi.fn(),
    create: vi.fn(),
  },
}));
const health: Health = { status: 'ok', model_configured: false, rule_version: 'demo-v2.1' };
const scenario: PresetScenario = {
  schema_version: '2.1',
  scenario_id: 'river',
  title: '渡口',
  board: { rows: 8, cols: 8 },
  rule_profile: 'demo-v2.1',
  characters: [],
  objects: [],
  limits: {},
};
const keeper: CharacterSummary = {
  group_id: 'sample',
  id: 'keeper',
  name: '守桥人',
  type: 'rook',
  color: '青',
  position: { row: 3, col: 1 },
};
const world: World = { world_id: 'w1', mode: 'fixture', turn: 0, seq: 0, lifecycle: 'READY' };

async function ready(config = health) {
  const onCreated = vi.fn();
  render(<ScenarioEditor health={config} onCreated={onCreated} />);
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '创建实验 →' })).not.toBeDisabled(),
  );
  return onCreated;
}
function input(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
function tab(name: string) {
  fireEvent.click(screen.getByRole('tab', { name }));
}

describe('structured preset authoring', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.presets).mockResolvedValue([
      { id: 'river', title: '渡口人物', description: '个人设定', readonly: false },
      { id: 'sample', title: '印信示例', description: '示例只读', readonly: true },
    ]);
    vi.mocked(api.characters).mockResolvedValue([keeper]);
    vi.mocked(api.discipline).mockResolvedValue({
      markdown: '# 纪律\n只使用自身知识。',
      revision: 'd1',
    });
    vi.mocked(api.presetScenario).mockResolvedValue({ scenario, revision: 's1' });
    vi.mocked(api.validatePreset).mockResolvedValue({ valid: true, errors: [] });
    vi.mocked(api.createFromPreset).mockResolvedValue(world);
    vi.mocked(api.saveCharacter).mockImplementation(
      async (groupId, character, design_markdown) => ({
        group_id: groupId,
        character,
        design_markdown,
        revision: 'c2',
      }),
    );
    vi.mocked(api.savePresetScenario).mockImplementation(async (_groupId, value) => ({
      scenario: value,
      revision: 's2',
    }));
    vi.mocked(api.character).mockResolvedValue({
      group_id: 'sample',
      character: keeper,
      design_markdown: '# 守桥人\n## 公开目标\n询问去处。\n## 自定义细节\n<script>bad()</script>',
      revision: 'c1',
    });
  });
  it('creates a preset group with an explicit destination and never starts an AI call', async () => {
    vi.mocked(api.createPreset).mockResolvedValue({
      id: 'new-group',
      title: '山间',
      description: '长故事',
      readonly: false,
    });
    await ready();
    input('预设组 ID', 'new-group');
    input('预设组标题', '山间');
    input('预设组说明', '长故事');
    fireEvent.click(screen.getByRole('button', { name: '创建预设组' }));
    await waitFor(() =>
      expect(api.createPreset).toHaveBeenCalledWith({
        id: 'new-group',
        title: '山间',
        description: '长故事',
      }),
    );
    await waitFor(() => expect(screen.getByLabelText('当前预设组')).toHaveValue('new-group'));
    expect(api.createFromPreset).not.toHaveBeenCalled();
    expect(api.create).not.toHaveBeenCalled();
  });
  it('saves private goals, history and relationships exclusively in Markdown, with system metadata in JSON', async () => {
    await ready();
    tab('人物库');
    input('人物 ID', 'traveler');
    input('人物姓名', '旅人');
    input('过往故事', '我曾看见桥的另一端燃起火光。');
    input('人物关系', '守桥人曾照料我。');
    input('公开目标', '重访旧桥。');
    input('隐藏目标', '不让朋友认出我。');
    expect(screen.getByRole('button', { name: '创建实验 →' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '保存人物' }));
    await screen.findByText(/人物已保存/);
    expect(screen.getByRole('textbox', { name: '人物设计 Markdown' })).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '棋子类型' })).toBeInTheDocument();
    const [groupId, character, markdown, revision] = vi.mocked(api.saveCharacter).mock.calls[0];
    expect(groupId).toBe('river');
    expect(revision).toBeUndefined();
    expect(Object.keys(character).sort()).toEqual(['color', 'id', 'name', 'position', 'type']);
    expect(JSON.stringify(character)).not.toContain('重访旧桥');
    expect(markdown).toContain('## 公开目标\n重访旧桥。');
    expect(markdown).toContain('## 隐藏目标\n不让朋友认出我。');
    expect(markdown).toContain('## 过往故事');
    expect(markdown).toContain('## 人物关系');
    expect(api.createFromPreset).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: '创建实验 →' })).not.toBeDisabled(),
    );
  });
  it('uses cross-group references with local position overrides and no persona fields in match JSON', async () => {
    await ready();
    tab('对局');
    input('跨组人物选择', 'sample/keeper');
    fireEvent.click(screen.getByRole('button', { name: '加入对局' }));
    input('起点行 1', '2');
    input('起点列 1', '4');
    fireEvent.click(screen.getByRole('button', { name: '保存对局' }));
    await screen.findByText(/对局信息已保存/);
    expect(api.savePresetScenario).toHaveBeenCalledWith(
      'river',
      expect.objectContaining({
        characters: [
          {
            group_id: 'sample',
            character_id: 'keeper',
            piece_id: 'keeper',
            position: { row: 2, col: 4 },
          },
        ],
      }),
      's1',
    );
    expect(api.saveCharacter).not.toHaveBeenCalled();
    expect(JSON.stringify(vi.mocked(api.savePresetScenario).mock.calls[0][1])).not.toMatch(
      /private_text|goal|design_markdown/,
    );
  });
  it('blocks duplicate runtime IDs and overlapping positions before sending a save', async () => {
    await ready();
    tab('对局');
    input('跨组人物选择', 'sample/keeper');
    fireEvent.click(screen.getByRole('button', { name: '加入对局' }));
    fireEvent.click(screen.getByRole('button', { name: '加入对局' }));
    expect(screen.getByLabelText('对局角色 ID 2')).toHaveValue('keeper_2');
    input('对局角色 ID 2', 'keeper');
    fireEvent.click(screen.getByRole('button', { name: '保存对局' }));
    await screen.findByText(/对局角色 ID 重复/);
    expect(api.savePresetScenario).not.toHaveBeenCalled();
    input('对局角色 ID 2', 'keeper_2');
    fireEvent.click(screen.getByRole('button', { name: '保存对局' }));
    await screen.findByText(/角色起点不能重叠/);
    expect(api.savePresetScenario).not.toHaveBeenCalled();
  });
  it('keeps bundled groups and foreign characters read-only', async () => {
    await ready();
    tab('人物库');
    input('人物库 · 可浏览所有预设组', 'sample/keeper');
    await waitFor(() =>
      expect((screen.getByLabelText('人物设计 Markdown') as HTMLTextAreaElement).value).toContain(
        '守桥人',
      ),
    );
    expect(screen.getByRole('button', { name: '保存人物' })).toBeDisabled();
    expect(screen.getByLabelText('人物姓名')).toBeDisabled();
    input('当前预设组', 'sample');
    tab('对局');
    await waitFor(() => expect(screen.getByRole('button', { name: '保存对局' })).toBeDisabled());
    expect(api.saveCharacter).not.toHaveBeenCalled();
    expect(api.savePresetScenario).not.toHaveBeenCalled();
  });
  it('preserves free Markdown when returning to structured fields and renders embedded HTML as text', async () => {
    await ready();
    tab('人物库');
    input('人物 ID', 'author');
    input('人物姓名', '作者');
    fireEvent.click(screen.getByRole('button', { name: '自由编辑 Markdown' }));
    input('人物设计 Markdown', '# 作者\n## 独有记忆\n保留这段很长的故事。\n<script>bad()</script>');
    fireEvent.click(screen.getByRole('button', { name: '分项填写' }));
    input('过往故事', '新的补充。');
    fireEvent.click(screen.getByText('预览私有 Markdown'));
    expect(document.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '保存人物' }));
    await screen.findByText(/人物已保存/);
    const markdown = vi.mocked(api.saveCharacter).mock.calls[0][2];
    expect(markdown).toContain('## 独有记忆\n保留这段很长的故事。');
    expect(markdown).toContain('<script>bad()</script>');
    expect(markdown).toContain('## 过往故事（补充）\n新的补充。');
  });
  it('retains an edited character on a revision conflict and shows a recovery action', async () => {
    vi.mocked(api.character).mockResolvedValue({
      group_id: 'river',
      character: { ...keeper, id: 'mine' },
      design_markdown: '# 私有草稿',
      revision: 'c1',
    });
    vi.mocked(api.characters).mockResolvedValue([{ ...keeper, group_id: 'river', id: 'mine' }]);
    vi.mocked(api.saveCharacter).mockRejectedValue(new ApiError(409, 'revision_conflict'));
    await ready();
    tab('人物库');
    input('人物库 · 可浏览所有预设组', 'river/mine');
    await waitFor(() =>
      expect(screen.getByLabelText('人物设计 Markdown')).toHaveValue('# 私有草稿'),
    );
    input('人物设计 Markdown', '# 私有草稿\n仍应保留。');
    fireEvent.click(screen.getByRole('button', { name: '保存人物' }));
    await screen.findByText(/文件已被其他操作更新/);
    expect(screen.getByLabelText('人物设计 Markdown')).toHaveValue('# 私有草稿\n仍应保留。');
    expect(api.saveCharacter).toHaveBeenCalledWith(
      'river',
      expect.any(Object),
      '# 私有草稿\n仍应保留。',
      'c1',
    );
    expect(screen.getByRole('button', { name: '重新载入人物' })).toBeEnabled();
  });
  it('retains an edited match and its original revision on conflict', async () => {
    vi.mocked(api.savePresetScenario).mockRejectedValue(new ApiError(409, 'revision_conflict'));
    await ready();
    tab('对局');
    input('对局标题', '未保存标题');
    fireEvent.click(screen.getByRole('button', { name: '保存对局' }));
    await screen.findByText(/文件已被其他操作更新/);
    expect(screen.getByLabelText('对局标题')).toHaveValue('未保存标题');
    expect(api.savePresetScenario).toHaveBeenCalledWith(
      'river',
      expect.objectContaining({ title: '未保存标题' }),
      's1',
    );
    expect(screen.getByRole('button', { name: '创建实验 →' })).toBeDisabled();
  });
  it('creates a fixture only after validating the saved preset', async () => {
    const onCreated = await ready();
    expect(screen.getByText('验证规则与运行流程，非 AI 叙事')).toBeInTheDocument();
    expect(api.createFromPreset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '创建实验 →' }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(world));
    expect(api.validatePreset).toHaveBeenCalledWith('river');
    expect(api.createFromPreset).toHaveBeenCalledWith('river', 'fixture');
  });
  it('keeps invalid saved presets reviewable and does not create a world', async () => {
    vi.mocked(api.validatePreset).mockResolvedValue({
      valid: false,
      errors: [{ path: 'board.rows', message: 'must be >= 8' }],
    });
    await ready();
    fireEvent.click(screen.getByRole('button', { name: '创建实验 →' }));
    await screen.findByText('预设组校验未通过');
    expect(api.createFromPreset).not.toHaveBeenCalled();
    expect(screen.getByText(/board.rows/)).toBeInTheDocument();
  });
  it.each([
    health,
    { ...health, model_configured: true },
    { ...health, model_configured: true, tokenizer_agreement: false },
  ])('prevents live creation when readiness is incomplete: %j', async (config) => {
    await ready(config);
    fireEvent.click(screen.getByRole('radio', { name: /真实模型实验/ }));
    expect(screen.getByRole('button', { name: '创建实验 →' })).toBeDisabled();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(api.createFromPreset).not.toHaveBeenCalled();
  });
  it('preserves a labeled legacy JSON import and reports server validation errors', async () => {
    vi.mocked(api.validate).mockResolvedValue({ valid: false, errors: ['旧场景缺少棋盘'] });
    await ready();
    fireEvent.click(screen.getByText('高级 · 兼容导入旧版场景 JSON'));
    input('兼容场景配置 JSON', '{"pieces":[]}');
    fireEvent.click(screen.getByRole('button', { name: '从兼容 JSON 创建实验' }));
    await screen.findByText('场景校验未通过');
    expect(api.create).not.toHaveBeenCalled();
    expect(screen.getByText('旧场景缺少棋盘')).toBeInTheDocument();
  });
});
