import { cloneElement, useId, type ReactElement } from 'react';
import { ApiError } from '../../lib/api';

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactElement<{ id?: string }>;
}) {
  const generatedId = useId();
  const controlId = children.props.id ?? generatedId;
  return (
    <div className="preset-field">
      <label htmlFor={controlId}>{label}</label>
      {cloneElement(children, { id: controlId })}
    </div>
  );
}

export function errorMessage(cause: unknown): string {
  if (cause instanceof ApiError && cause.status === 409)
    return '文件已被其他操作更新。你的编辑仍保留；请复制草稿后重新载入，再合并修改。';
  return cause instanceof Error ? cause.message : '操作失败，请稍后重试。';
}

/** React text nodes only: Markdown and embedded HTML are never executed. */
export function MarkdownPreview({ source }: { source: string }) {
  return (
    <div className="markdown-preview" aria-label="Markdown 预览">
      {source.split('\n').map((line, index) => {
        if (line.startsWith('# ')) return <h3 key={index}>{line.slice(2)}</h3>;
        if (line.startsWith('## ')) return <h4 key={index}>{line.slice(3)}</h4>;
        if (line.startsWith('### ')) return <h5 key={index}>{line.slice(4)}</h5>;
        return <p key={index}>{line || '\u00a0'}</p>;
      })}
    </div>
  );
}
