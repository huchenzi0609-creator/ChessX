import { useState } from 'react';
import { api } from '../../lib/api';
import { asText, type Validation, type World } from '../../lib/types';
import { errorMessage, Field } from './fields';

export default function LegacyScenarioImport({
  mode,
  liveReady,
  onCreated,
}: {
  mode: 'fixture' | 'live';
  liveReady: boolean;
  onCreated: (world: World) => void;
}) {
  const [source, setSource] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [busy, setBusy] = useState(false);
  async function importFile(file?: File) {
    if (!file) return;
    if (file.size > 1024 * 1024) {
      setError('场景 JSON 文件应小于 1 MB。');
      return;
    }
    try {
      const text = await file.text();
      JSON.parse(text);
      setSource(text);
      setValidation(null);
      setError(null);
    } catch {
      setError('无法导入，请选择有效的 UTF-8 JSON 场景文件。');
    }
  }
  async function validate(create = false) {
    if (create && mode === 'live' && !liveReady) return;
    setBusy(true);
    setError(null);
    try {
      let value: unknown;
      try {
        value = JSON.parse(source);
      } catch {
        throw new Error('JSON 格式有误，请检查引号、逗号与括号。');
      }
      const result = await api.validate(value);
      setValidation(result);
      if (create && result.valid) onCreated(await api.create(value, mode));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="preset-section">
      <Field label="导入本地场景 JSON">
        <input
          type="file"
          accept="application/json,.json"
          onChange={(event) => {
            void importFile(event.target.files?.[0]);
            event.target.value = '';
          }}
        />
      </Field>
      <Field label="兼容场景配置 JSON">
        <textarea
          rows={14}
          spellCheck={false}
          value={source}
          onChange={(event) => {
            setSource(event.target.value);
            setValidation(null);
          }}
        />
      </Field>
      {error && (
        <div role="alert" className="error-banner">
          {error}
        </div>
      )}
      {validation && (
        <div role="status" className={validation.valid ? 'validation-success' : 'error-banner'}>
          {validation.valid ? (
            '场景校验通过。'
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
          校验导入
        </button>
        <button
          disabled={busy || !source || (mode === 'live' && !liveReady)}
          onClick={() => void validate(true)}
        >
          从兼容 JSON 创建实验
        </button>
      </div>
    </div>
  );
}
