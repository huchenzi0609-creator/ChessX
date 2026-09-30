import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import {
  asText,
  type Health,
  type ScenarioSummary,
  type Validation,
  type World,
} from '../lib/types';
type Props = { health: Health | null; onCreated: (world: World) => void; onClose?: () => void };
export default function ScenarioEditor({ health, onCreated, onClose }: Props) {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [scenarioId, setScenarioId] = useState('');
  const [source, setSource] = useState('');
  const [mode, setMode] = useState<'fixture' | 'live'>('fixture');
  const [validation, setValidation] = useState<Validation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    api
      .scenarios()
      .then((items) => {
        if (!active) return;
        setScenarios(items);
        setScenarioId(items[0]?.id ?? '');
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!scenarioId) return;
    let active = true;
    setSource('');
    setValidation(null);
    api
      .scenario(scenarioId)
      .then((value) => {
        if (active) {
          setSource(JSON.stringify(value, null, 2));
          setError(null);
        }
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      });
    return () => {
      active = false;
    };
  }, [scenarioId]);
  async function validate(create = false) {
    setBusy(true);
    setError(null);
    try {
      let scenario: unknown;
      try {
        scenario = JSON.parse(source);
      } catch {
        throw new Error('JSON 格式有误，请检查引号、逗号与括号。');
      }
      const result = await api.validate(scenario);
      setValidation(result);
      if (create && result.valid) onCreated(await api.create(scenario, mode));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '场景处理失败');
    } finally {
      setBusy(false);
    }
  }
  async function importFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    if (file.size > 1024 * 1024) {
      setError('场景 JSON 文件应小于 1 MB。');
      return;
    }
    try {
      const text = await file.text();
      JSON.parse(text);
      setSource(text);
      setValidation(null);
    } catch {
      setError('无法导入，请选择有效的 UTF-8 JSON 场景文件。');
    }
  }
  const summary = scenarios.find((item) => item.id === scenarioId);
  return (
    <section className="scenario-editor">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">EXPERIMENT / 场景</span>
          <h2>为故事安排一个起点</h2>
        </div>
        {onClose && (
          <button className="quiet" onClick={onClose}>
            返回观测台
          </button>
        )}
      </div>
      <div className="scenario-layout">
        <aside className="scenario-guide">
          <label className="field-label" htmlFor="scenario-choice">
            实验场景
          </label>
          <select
            id="scenario-choice"
            value={scenarioId}
            onChange={(event) => setScenarioId(event.target.value)}
          >
            {scenarios.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
          <p className="scenario-description">
            {summary?.description ?? '加载场景后，可编辑棋盘、角色、物件和私人资料。'}
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
            模型密钥仅配置在后端的本地环境文件中。浏览器不采集密钥。当前模型：
            {health?.model_configured
              ? health.tokenizer_agreement === false
                ? '密钥配置已就绪，tokenizer 待核实'
                : '已配置，运行前校验能力'
              : '未配置'}
          </p>
          <div className="rule-card">
            <span className="eyebrow">DEMO–V2.1</span>
            <p>颜色是身份，走法是几何。角色凭自身所见、所闻和记忆行动。</p>
            <ul>
              <li>0–8 个角色 · 8–64 格棋盘</li>
              <li>兵正四向一步；斜向仅在格内有人时移动</li>
              <li>马直接跳变 · 无升变</li>
              <li>会话与交涉至多 50 句</li>
              <li>战争消息不允许远程加入</li>
            </ul>
          </div>
        </aside>
        <div className="scenario-code">
          <label className="field-label" htmlFor="scenario-json">
            场景配置 JSON · 私人资料仅供本地研究
          </label>
          <div className="import-field">
            <label htmlFor="scenario-file">导入本地场景 JSON</label>
            <input
              id="scenario-file"
              type="file"
              accept="application/json,.json"
              onChange={(event) => {
                void importFile(event.target.files?.[0]);
                event.target.value = '';
              }}
            />
          </div>
          <textarea
            id="scenario-json"
            spellCheck={false}
            value={source}
            onChange={(event) => {
              setSource(event.target.value);
              setValidation(null);
            }}
            aria-describedby="json-help"
          />
          <p id="json-help" className="muted">
            按角色 ID 绑定私人文本；校验会报告字段路径。创建后冻结场景。
          </p>
          {error && (
            <div className="error-banner" role="alert">
              {error}
            </div>
          )}
          {validation && (
            <div className={validation.valid ? 'validation-success' : 'error-banner'} role="status">
              {validation.valid ? (
                '场景校验通过，可创建实验。'
              ) : (
                <>
                  <strong>场景校验未通过</strong>
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
            <button disabled={busy || !source} onClick={() => void validate()}>
              校验场景
            </button>
            <button
              className="primary"
              disabled={
                busy ||
                !source ||
                (mode === 'live' &&
                  (!health?.model_configured || health.tokenizer_agreement === false))
              }
              onClick={() => void validate(true)}
            >
              {busy ? '正在校验…' : '创建实验 →'}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
