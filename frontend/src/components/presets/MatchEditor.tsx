import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import {
  asText,
  type CharacterReference,
  type CharacterSummary,
  type PresetScenario,
  type PresetSummary,
  type ScenarioDocument,
} from '../../lib/types';
import { errorMessage, Field } from './fields';

type Props = {
  group: PresetSummary;
  document: ScenarioDocument;
  library: CharacterSummary[];
  onSaved: (document: ScenarioDocument) => void;
  onDirty: (dirty: boolean) => void;
};
export default function MatchEditor({ group, document, library, onSaved, onDirty }: Props) {
  const [scenario, setScenario] = useState<PresetScenario>(document.scenario);
  const [objects, setObjects] = useState(JSON.stringify(document.scenario.objects, null, 2));
  const [limits, setLimits] = useState(JSON.stringify(document.scenario.limits ?? {}, null, 2));
  const [choice, setChoice] = useState('');
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  function update(value: Partial<PresetScenario>) {
    setScenario((current) => ({ ...current, ...value }));
    setDirty(true);
    setNotice(null);
  }
  function changeReference(index: number, value: Partial<CharacterReference>) {
    update({
      characters: scenario.characters.map((reference, i) =>
        i === index ? { ...reference, ...value } : reference,
      ),
    });
  }
  function add() {
    const character = library.find((item) => `${item.group_id}/${item.id}` === choice);
    if (!character) return;
    const taken = new Set(
      scenario.characters.map((reference) => reference.piece_id ?? reference.character_id),
    );
    let alias = character.id;
    for (let count = 2; taken.has(alias); count++) alias = `${character.id}_${count}`;
    update({
      characters: [
        ...scenario.characters,
        {
          group_id: character.group_id,
          character_id: character.id,
          piece_id: alias,
          position: { ...character.position },
        },
      ],
    });
  }
  function check(): PresetScenario {
    if (!scenario.title.trim() || !scenario.scenario_id.trim())
      throw new Error('请填写对局 ID 与标题。');
    if (
      ![scenario.board.rows, scenario.board.cols].every(
        (value) => Number.isInteger(value) && value >= 8 && value <= 64,
      )
    )
      throw new Error('棋盘行列必须为 8–64 的整数。');
    if (scenario.characters.length > 8) throw new Error('Demo 至多支持 8 个角色。');
    const ids = scenario.characters.map(
      (reference) => reference.piece_id || reference.character_id,
    );
    if (new Set(ids).size !== ids.length)
      throw new Error('对局角色 ID 重复；请为重复调用的人物设置不同的对局 ID。');
    if (ids.some((value) => !/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(value)))
      throw new Error('对局角色 ID 只能包含字母、数字、下划线和连字符。');
    const positions = scenario.characters.map(
      (reference) =>
        reference.position ??
        library.find(
          (item) => item.group_id === reference.group_id && item.id === reference.character_id,
        )?.position,
    );
    if (
      positions.some(
        (position) =>
          !position ||
          !Number.isInteger(position.row) ||
          !Number.isInteger(position.col) ||
          position.row < 0 ||
          position.col < 0 ||
          position.row >= scenario.board.rows ||
          position.col >= scenario.board.cols,
      )
    )
      throw new Error('每名角色的起点必须位于棋盘内。');
    if (
      new Set(positions.map((position) => `${position?.row},${position?.col}`)).size !==
      positions.length
    )
      throw new Error('角色起点不能重叠，请调整位置。');
    let parsedObjects: unknown;
    let parsedLimits: unknown;
    try {
      parsedObjects = JSON.parse(objects);
      parsedLimits = JSON.parse(limits);
    } catch {
      throw new Error('物件或运行限制 JSON 格式有误。');
    }
    if (!Array.isArray(parsedObjects)) throw new Error('物件配置应为 JSON 数组。');
    if (!parsedLimits || typeof parsedLimits !== 'object' || Array.isArray(parsedLimits))
      throw new Error('运行限制应为 JSON 对象。');
    return {
      ...scenario,
      schema_version: '2.1',
      rule_profile: 'demo-v2.1',
      objects: parsedObjects,
      limits: parsedLimits as Record<string, unknown>,
    };
  }
  async function save() {
    setBusy(true);
    setError(null);
    try {
      const saved = await api.savePresetScenario(group.id, check(), document.revision);
      setScenario(saved.scenario);
      setDirty(false);
      onSaved(saved);
      setNotice('对局信息已保存。跨组角色通过引用调用，原人物文件保持独立。');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  async function reload() {
    setBusy(true);
    setError(null);
    try {
      const saved = await api.presetScenario(group.id);
      setScenario(saved.scenario);
      setObjects(JSON.stringify(saved.scenario.objects, null, 2));
      setLimits(JSON.stringify(saved.scenario.limits ?? {}, null, 2));
      setDirty(false);
      onSaved(saved);
      setNotice('已重新载入保存版本。');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="preset-section" aria-label="对局编辑器">
      {group.readonly && (
        <p className="safe-note">内置对局只读。可直接创建实验，或在自己的预设组中引用这些人物。</p>
      )}
      <fieldset disabled={group.readonly || busy}>
        <legend>对局设置</legend>
        <div className="preset-form-grid">
          <Field label="对局 ID">
            <input
              value={scenario.scenario_id}
              onChange={(event) => update({ scenario_id: event.target.value })}
            />
          </Field>
          <Field label="对局标题">
            <input
              value={scenario.title}
              onChange={(event) => update({ title: event.target.value })}
            />
          </Field>
          <Field label="棋盘行数">
            <input
              type="number"
              min={8}
              max={64}
              value={scenario.board.rows}
              onChange={(event) =>
                update({ board: { ...scenario.board, rows: Number(event.target.value) } })
              }
            />
          </Field>
          <Field label="棋盘列数">
            <input
              type="number"
              min={8}
              max={64}
              value={scenario.board.cols}
              onChange={(event) =>
                update({ board: { ...scenario.board, cols: Number(event.target.value) } })
              }
            />
          </Field>
        </div>
        <legend>人物阵容 · {scenario.characters.length} / 8</legend>
        <p className="muted">
          从任意预设组单独调用人物。对局 ID 与起点只影响本次对局，不修改原角色资料。
        </p>
        <div className="preset-toolbar">
          <Field label="跨组人物选择">
            <select value={choice} onChange={(event) => setChoice(event.target.value)}>
              <option value="">请选择人物</option>
              {library.map((item) => (
                <option key={`${item.group_id}/${item.id}`} value={`${item.group_id}/${item.id}`}>
                  {item.name} · {item.group_id} / {item.id}
                </option>
              ))}
            </select>
          </Field>
          <button type="button" disabled={!choice || scenario.characters.length >= 8} onClick={add}>
            加入对局
          </button>
        </div>
        <div className="cast-editor">
          {scenario.characters.map((reference, index) => {
            const character = library.find(
              (item) => item.group_id === reference.group_id && item.id === reference.character_id,
            );
            const position = reference.position ?? character?.position ?? { row: 0, col: 0 };
            return (
              <article key={index} className="cast-editor-row">
                <strong>
                  {character?.name ?? reference.character_id}
                  <small>
                    {reference.group_id} / {reference.character_id}
                  </small>
                </strong>
                <Field label={`对局角色 ID ${index + 1}`}>
                  <input
                    value={reference.piece_id ?? reference.character_id}
                    onChange={(event) => changeReference(index, { piece_id: event.target.value })}
                  />
                </Field>
                <Field label={`起点行 ${index + 1}`}>
                  <input
                    type="number"
                    min={0}
                    max={scenario.board.rows - 1}
                    value={position.row}
                    onChange={(event) =>
                      changeReference(index, {
                        position: { ...position, row: Number(event.target.value) },
                      })
                    }
                  />
                </Field>
                <Field label={`起点列 ${index + 1}`}>
                  <input
                    type="number"
                    min={0}
                    max={scenario.board.cols - 1}
                    value={position.col}
                    onChange={(event) =>
                      changeReference(index, {
                        position: { ...position, col: Number(event.target.value) },
                      })
                    }
                  />
                </Field>
                <button
                  type="button"
                  onClick={() =>
                    update({ characters: scenario.characters.filter((_, i) => i !== index) })
                  }
                >
                  移除 {character?.name ?? reference.character_id}
                </button>
              </article>
            );
          })}
        </div>
        <details className="preset-details">
          <summary>物件与运行限制</summary>
          <Field label="物件配置 JSON">
            <textarea
              rows={8}
              value={objects}
              onChange={(event) => {
                setObjects(event.target.value);
                setDirty(true);
                setNotice(null);
              }}
            />
          </Field>
          <p className="muted">
            使用物件 ID、名称、描述、location（board / held）与对应 position /
            holder_id；保存时由后端校验。
          </p>
          <Field label="运行限制 JSON">
            <textarea
              rows={6}
              value={limits}
              onChange={(event) => {
                setLimits(event.target.value);
                setDirty(true);
                setNotice(null);
              }}
            />
          </Field>
        </details>
      </fieldset>
      <details className="preset-details">
        <summary>查看对局 JSON（规则信息与人物引用）</summary>
        <pre>{asText(scenario)}</pre>
      </details>
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
      <div className="editor-actions">
        <button disabled={busy} onClick={() => void reload()}>
          {dirty ? '放弃草稿并重新载入' : '重新载入对局'}
        </button>
        <button className="primary" disabled={group.readonly || busy} onClick={() => void save()}>
          {busy ? '正在保存…' : '保存对局'}
        </button>
      </div>
    </section>
  );
}
