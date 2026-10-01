import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import {
  pieceLabels,
  type CharacterDocument,
  type CharacterInfo,
  type CharacterSummary,
  type PresetSummary,
} from '../../lib/types';
import { errorMessage, Field, MarkdownPreview } from './fields';

const sections = [
  '性格与价值观',
  '过往故事',
  '人物关系',
  '对他人的初始看法',
  '公开目标',
  '隐藏目标',
  '表达与行动风格',
  '初始知识与已知人物',
] as const;
const emptyCharacter = (): CharacterInfo => ({
  id: '',
  name: '',
  type: 'pawn',
  color: '白',
  position: { row: 0, col: 0 },
});
type Props = {
  group: PresetSummary;
  library: CharacterSummary[];
  onSaved: () => Promise<void>;
  onDirty: (dirty: boolean) => void;
};

export default function CharacterEditor({ group, library, onSaved, onDirty }: Props) {
  const [selected, setSelected] = useState('');
  const [character, setCharacter] = useState<CharacterInfo>(emptyCharacter);
  const [markdown, setMarkdown] = useState('');
  const [revision, setRevision] = useState<string>();
  const [origin, setOrigin] = useState(group.id);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [baseMarkdown, setBaseMarkdown] = useState('');
  const [authoring, setAuthoring] = useState<'fields' | 'markdown'>('fields');
  const writable = !group.readonly && origin === group.id;
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);

  function install(document: CharacterDocument) {
    setCharacter(document.character);
    setMarkdown(document.design_markdown);
    setRevision(document.revision);
    setOrigin(document.group_id);
    setDirty(false);
    setAuthoring('markdown');
    setAnswers({});
    setBaseMarkdown('');
  }
  function newCharacter() {
    setSelected('');
    setCharacter(emptyCharacter());
    setMarkdown('');
    setRevision(undefined);
    setOrigin(group.id);
    setDirty(false);
    setAnswers({});
    setBaseMarkdown('');
    setAuthoring('fields');
    setError(null);
    setNotice(null);
  }
  async function load(key: string) {
    if (!key) {
      newCharacter();
      return;
    }
    const item = library.find((value) => `${value.group_id}/${value.id}` === key);
    if (!item) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      install(await api.character(item.group_id, item.id));
      setSelected(key);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  function update(value: Partial<CharacterInfo>) {
    setCharacter((current) => ({ ...current, ...value }));
    setDirty(true);
    setNotice(null);
  }
  function generatedMarkdown(values = answers, name = character.name) {
    if (baseMarkdown)
      return `${baseMarkdown}\n\n${sections
        .filter((section) => values[section]?.trim())
        .map((section) => `## ${section}（补充）\n${values[section].trim()}`)
        .join('\n\n')}`;
    return `# ${name || '未命名人物'}\n\n${sections.map((section) => `## ${section}\n${values[section]?.trim() || (section === '初始知识与已知人物' ? '默认不知道其他人物的存在；仅知晓本资料明确记载的内容。' : '待补充。')}`).join('\n\n')}\n`;
  }
  const design = authoring === 'fields' ? generatedMarkdown() : markdown;
  async function save() {
    if (!writable) return;
    if (!character.id.trim() || !character.name.trim() || !character.color.trim()) {
      setError('请填写人物 ID、姓名和颜色。');
      return;
    }
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(character.id)) {
      setError('人物 ID 只能包含字母、数字、下划线和连字符。');
      return;
    }
    if (
      !revision &&
      library.some((item) => item.group_id === group.id && item.id === character.id)
    ) {
      setError('此预设组已存在同名 ID，请选择该人物后编辑。');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      install(await api.saveCharacter(group.id, character, design, revision));
      setSelected(`${group.id}/${character.id}`);
      await onSaved();
      setNotice('人物已保存：人物信息 JSON 与私有设计 Markdown 已分别落盘。');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="preset-section" aria-label="人物编辑器">
      <div className="preset-toolbar">
        <Field label="人物库 · 可浏览所有预设组">
          <select
            value={selected}
            disabled={busy || dirty}
            onChange={(event) => void load(event.target.value)}
          >
            <option value="">新建人物</option>
            {library.map((item) => (
              <option key={`${item.group_id}/${item.id}`} value={`${item.group_id}/${item.id}`}>
                {item.name} · {item.group_id} / {item.id}
              </option>
            ))}
          </select>
        </Field>
        <button disabled={busy} onClick={newCharacter}>
          {dirty ? '放弃草稿并新建人物' : '新建人物'}
        </button>
        {selected && (
          <button disabled={busy} onClick={() => void load(selected)}>
            重新载入人物
          </button>
        )}
      </div>
      <p className="muted">
        新人物保存到当前预设组「{group.title}
        」。其他组的人物可独立加入本组对局；修改其原文件需先切换到所属预设组。
      </p>
      {!writable && (
        <p className="safe-note">
          {group.readonly
            ? '内置示例只读。请创建自己的预设组以编写人物。'
            : `正在浏览 ${origin} 的人物，当前只读。`}
        </p>
      )}
      <fieldset disabled={!writable || busy}>
        <legend>规则域 · 人物信息</legend>
        <div className="preset-form-grid">
          <Field label="人物 ID">
            <input
              value={character.id}
              disabled={Boolean(revision)}
              onChange={(event) => update({ id: event.target.value })}
              placeholder="例如 keeper"
            />
          </Field>
          <Field label="人物姓名">
            <input
              value={character.name}
              onChange={(event) => update({ name: event.target.value })}
            />
          </Field>
          <Field label="棋子类型">
            <select
              value={character.type}
              onChange={(event) => update({ type: event.target.value })}
            >
              {Object.entries(pieceLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="人物颜色">
            <input
              value={character.color}
              onChange={(event) => update({ color: event.target.value })}
            />
          </Field>
          <Field label="默认行（从 0 开始）">
            <input
              type="number"
              min={0}
              max={63}
              value={character.position.row}
              onChange={(event) =>
                update({ position: { ...character.position, row: Number(event.target.value) } })
              }
            />
          </Field>
          <Field label="默认列（从 0 开始）">
            <input
              type="number"
              min={0}
              max={63}
              value={character.position.col}
              onChange={(event) =>
                update({ position: { ...character.position, col: Number(event.target.value) } })
              }
            />
          </Field>
        </div>
        <legend>叙事域 · 私有角色资料</legend>
        <p className="muted">
          公开目标与隐藏目标均属于私有记忆。其他角色只能从言行获知；本编辑器属于本地研究视角。
        </p>
        <div className="preset-toolbar">
          <button
            type="button"
            disabled={authoring === 'fields'}
            onClick={() => {
              setBaseMarkdown(markdown);
              setAnswers({});
              setAuthoring('fields');
            }}
          >
            分项填写
          </button>
          <button
            type="button"
            disabled={authoring === 'markdown'}
            onClick={() => {
              setMarkdown(generatedMarkdown());
              setAuthoring('markdown');
            }}
          >
            自由编辑 Markdown
          </button>
        </div>
        {authoring === 'fields' && baseMarkdown && (
          <p className="safe-note">原 Markdown 完整保留；以下新增内容将作为补充段落追加。</p>
        )}
        {authoring === 'fields' ? (
          <div className="private-fields">
            {sections.map((section) => (
              <Field key={section} label={section}>
                <textarea
                  rows={section === '过往故事' ? 6 : 3}
                  value={answers[section] ?? ''}
                  onChange={(event) => {
                    setAnswers((current) => ({ ...current, [section]: event.target.value }));
                    setDirty(true);
                    setNotice(null);
                  }}
                />
              </Field>
            ))}
          </div>
        ) : (
          <Field label="人物设计 Markdown">
            <textarea
              className="design-markdown"
              rows={24}
              value={markdown}
              onChange={(event) => {
                setMarkdown(event.target.value);
                setDirty(true);
                setNotice(null);
              }}
            />
          </Field>
        )}
      </fieldset>
      <details className="preset-details">
        <summary>预览私有 Markdown</summary>
        <MarkdownPreview source={design} />
      </details>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <p role="status" className="validation-success">
          {notice}
        </p>
      )}
      <div className="editor-actions">
        <button className="primary" disabled={!writable || busy} onClick={() => void save()}>
          {busy ? '正在保存…' : '保存人物'}
        </button>
      </div>
    </section>
  );
}
