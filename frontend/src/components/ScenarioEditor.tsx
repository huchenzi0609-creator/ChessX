import { useCallback, useEffect, useState } from 'react';
import { api } from '../lib/api';
import {
  asText,
  type CharacterSummary,
  type Health,
  type PresetSummary,
  type ScenarioDocument,
  type Validation,
  type World,
} from '../lib/types';
import CharacterEditor from './presets/CharacterEditor';
import MatchEditor from './presets/MatchEditor';
import LegacyScenarioImport from './presets/LegacyScenarioImport';
import { errorMessage, Field, MarkdownPreview } from './presets/fields';

type Props = { health: Health | null; onCreated: (world: World) => void; onClose?: () => void };
type Tab = 'preset' | 'characters' | 'match';
export default function ScenarioEditor({ health, onCreated, onClose }: Props) {
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [library, setLibrary] = useState<CharacterSummary[]>([]);
  const [groupId, setGroupId] = useState('');
  const [document, setDocument] = useState<ScenarioDocument | null>(null);
  const [discipline, setDiscipline] = useState('');
  const [tab, setTab] = useState<Tab>('preset');
  const [mode, setMode] = useState<'fixture' | 'live'>('fixture');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [newPreset, setNewPreset] = useState({ id: '', title: '', description: '' });
  const [characterDirty, setCharacterDirty] = useState(false);
  const [matchDirty, setMatchDirty] = useState(false);
  const dirty = characterDirty || matchDirty;
  const group = presets.find((preset) => preset.id === groupId);
  const liveReady = health?.model_configured === true && health.tokenizer_agreement === true;
  useEffect(() => {
    let active = true;
    Promise.all([api.presets(), api.characters(), api.discipline()])
      .then(([groups, characters, shared]) => {
        if (!active) return;
        setPresets(groups);
        setLibrary(characters);
        setDiscipline(shared.markdown);
        setGroupId(groups[0]?.id ?? '');
      })
      .catch((cause) => {
        if (active) setError(errorMessage(cause));
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!groupId) return;
    let active = true;
    setDocument(null);
    setValidation(null);
    setError(null);
    setNotice(null);
    setCharacterDirty(false);
    setMatchDirty(false);
    api
      .presetScenario(groupId)
      .then((value) => {
        if (active) setDocument(value);
      })
      .catch((cause) => {
        if (active) setError(errorMessage(cause));
      });
    return () => {
      active = false;
    };
  }, [groupId]);
  const characterChanged = useCallback((changed: boolean) => {
    setCharacterDirty(changed);
    if (changed) setValidation(null);
  }, []);
  const matchChanged = useCallback((changed: boolean) => {
    setMatchDirty(changed);
    if (changed) setValidation(null);
  }, []);
  const refreshLibrary = useCallback(async () => {
    setLibrary(await api.characters());
    setValidation(null);
  }, []);
  async function createPreset() {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(newPreset.id) || !newPreset.title.trim()) {
      setError('请填写预设组标题；ID 只能包含字母、数字、下划线和连字符。');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createPreset(newPreset);
      setPresets((current) => [...current, created]);
      setGroupId(created.id);
      setNewPreset({ id: '', title: '', description: '' });
      setNotice('预设组已创建，可在人物库和对局中继续配置。');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  async function validate(create = false) {
    if (!groupId || dirty || (create && mode === 'live' && !liveReady)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.validatePreset(groupId);
      setValidation(result);
      if (create && result.valid) onCreated(await api.createFromPreset(groupId, mode));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="scenario-editor">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">PRESETS / 故事起点</span>
          <h2>设计人物，安排相遇</h2>
        </div>
        {onClose && (
          <button className="quiet" disabled={dirty} onClick={onClose}>
            返回观测台
          </button>
        )}
      </div>
      <div className="scenario-layout">
        <aside className="scenario-guide">
          <Field label="当前预设组">
            <select
              value={groupId}
              disabled={busy || dirty}
              onChange={(event) => setGroupId(event.target.value)}
            >
              <option value="" disabled>
                请选择预设组
              </option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.title}
                  {preset.readonly ? ' · 内置只读' : ''}
                </option>
              ))}
            </select>
          </Field>
          <p className="scenario-description">
            {group?.description ?? '创建预设组，分别保存人物设计、人物信息与对局配置。'}
          </p>
          <span className="field-label">执行模式</span>
          <div className="mode-options">
            <label>
              <input
                type="radio"
                name="mode"
                checked={mode === 'fixture'}
                onChange={() => setMode('fixture')}
              />
              <span>
                工程固定响应<small>验证规则与运行流程，非 AI 叙事</small>
              </span>
            </label>
            <label>
              <input
                type="radio"
                name="mode"
                checked={mode === 'live'}
                onChange={() => setMode('live')}
              />
              <span>
                真实模型实验<small>服务端须配置密钥、分词器与公开推理能力</small>
              </span>
            </label>
          </div>
          <p className="safe-note">
            模型密钥只保存在服务端；浏览器不采集密钥。
            {!health?.model_configured
              ? '当前尚未配置模型。'
              : !liveReady
                ? '密钥配置已就绪，tokenizer 待核实。'
                : '模型与分词器已配置，运行前仍校验能力。'}
          </p>
          <div className="rule-card">
            <span className="eyebrow">DEMO–V2.2</span>
            <p>所有人物共用行为纪律，各自持有完整的私有 Markdown 记忆。</p>
            <ul>
              <li>0–8 个角色 · 8–64 行列棋盘</li>
              <li>公开目标也需通过人物言行展现</li>
              <li>跨组引用人物，按对局覆盖起点</li>
              <li>普通交谈每人每回合至多 10 句</li>
              <li>会话总句数至多历史最多参与人数 × 50</li>
              <li>所有棋子视野 3 × 3，移动休息按起终格距离</li>
              <li>战争消息不允许远程加入</li>
            </ul>
          </div>
        </aside>
        <div className="preset-main">
          <div className="tabs" role="tablist" aria-label="预设生成器">
            {(['preset', 'characters', 'match'] as Tab[]).map((value) => (
              <button
                key={value}
                role="tab"
                aria-selected={tab === value}
                aria-controls={`preset-panel-${value}`}
                id={`preset-tab-${value}`}
                onClick={() => setTab(value)}
              >
                {{ preset: '预设组', characters: '人物库', match: '对局' }[value]}
              </button>
            ))}
          </div>
          <div
            hidden={tab !== 'preset'}
            role="tabpanel"
            id="preset-panel-preset"
            aria-labelledby="preset-tab-preset"
            className="preset-section"
          >
            <h3>创建预设组</h3>
            <p className="muted">
              一个预设组包含独立的人物文件夹和对局配置。人物资料可被其他预设组引用。
            </p>
            <fieldset disabled={busy || dirty}>
              <div className="preset-form-grid">
                <Field label="预设组 ID">
                  <input
                    value={newPreset.id}
                    onChange={(event) =>
                      setNewPreset((current) => ({ ...current, id: event.target.value }))
                    }
                    placeholder="例如 river-crossing"
                  />
                </Field>
                <Field label="预设组标题">
                  <input
                    value={newPreset.title}
                    onChange={(event) =>
                      setNewPreset((current) => ({ ...current, title: event.target.value }))
                    }
                  />
                </Field>
              </div>
              <Field label="预设组说明">
                <textarea
                  rows={3}
                  value={newPreset.description}
                  onChange={(event) =>
                    setNewPreset((current) => ({ ...current, description: event.target.value }))
                  }
                />
              </Field>
              <div className="editor-actions">
                <button className="primary" onClick={() => void createPreset()}>
                  创建预设组
                </button>
              </div>
            </fieldset>
            {group && (
              <div className="preset-summary">
                <strong>{group.title}</strong>
                <p>
                  {group.readonly ? '内置示例 · 只读' : '个人预设 · 可编辑'} ·{' '}
                  {library.filter((item) => item.group_id === groupId).length} 名人物 · 当前对局{' '}
                  {document?.scenario.characters.length ?? 0} 名人物
                </p>
                <p className="muted">
                  先保存人物设计，再在「对局」中选择人物和起点；保存对局后即可创建实验。
                </p>
              </div>
            )}
            <details className="preset-details">
              <summary>查看所有棋子共用的行为纪律</summary>
              <MarkdownPreview source={discipline || '正在加载…'} />
            </details>
          </div>
          {group && (
            <div
              hidden={tab !== 'characters'}
              role="tabpanel"
              id="preset-panel-characters"
              aria-labelledby="preset-tab-characters"
            >
              <CharacterEditor
                key={groupId}
                group={group}
                library={library}
                onSaved={refreshLibrary}
                onDirty={characterChanged}
              />
            </div>
          )}
          {group && document && (
            <div
              hidden={tab !== 'match'}
              role="tabpanel"
              id="preset-panel-match"
              aria-labelledby="preset-tab-match"
            >
              <MatchEditor
                key={groupId}
                group={group}
                document={document}
                library={library}
                onSaved={(saved) => {
                  setDocument(saved);
                  setValidation(null);
                }}
                onDirty={matchChanged}
              />
            </div>
          )}
          <div className="preset-run-actions">
            {dirty && (
              <p className="safe-note">
                存在尚未保存的草稿。请先保存，或在对应编辑器中明确放弃，再切换预设组或创建实验。
              </p>
            )}
            {error && (
              <div className="error-banner" role="alert">
                {error}
              </div>
            )}
            {notice && (
              <p className="validation-success" role="status">
                {notice}
              </p>
            )}
            {validation && (
              <div
                role="status"
                className={validation.valid ? 'validation-success' : 'error-banner'}
              >
                {validation.valid ? (
                  '预设组校验通过，可创建实验。'
                ) : (
                  <>
                    <strong>预设组校验未通过</strong>
                    <ul>
                      {validation.errors.map((issue, index) => (
                        <li key={index}>{asText(issue)}</li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}
            <div className="editor-actions">
              <button disabled={busy || dirty || !document} onClick={() => void validate()}>
                校验预设组
              </button>
              <button
                className="primary"
                disabled={busy || dirty || !document || (mode === 'live' && !liveReady)}
                onClick={() => void validate(true)}
              >
                {busy ? '正在处理…' : '创建实验 →'}
              </button>
            </div>
          </div>
          <details className="preset-details legacy-import">
            <summary>高级 · 兼容导入旧版场景 JSON</summary>
            <p className="safe-note">
              兼容导入用于一次性创建旧场景。新设计请使用上方预设生成器，人物设计保存在 Markdown 中。
            </p>
            <LegacyScenarioImport mode={mode} liveReady={liveReady} onCreated={onCreated} />
          </details>
        </div>
      </div>
    </section>
  );
}
