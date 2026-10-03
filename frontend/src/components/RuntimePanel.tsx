import { label, number } from '../lib/language';
import { asText, type RunBudget, type World } from '../lib/types';
export const DEFAULT_BUDGET: RunBudget = {
  max_calls: 1000,
  max_total_tokens: 2_000_000,
  max_wall_seconds: 1800,
};
export function BudgetEditor({
  budget,
  onChange,
  disabled = false,
}: {
  budget: RunBudget;
  onChange: (budget: RunBudget) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="budget-editor" disabled={disabled}>
      <legend>本次执行预算</legend>
      {(
        [
          ['max_calls', '最大模型请求', 1, 10000],
          ['max_total_tokens', '最大 Token', 4096, 100_000_000],
          ['max_wall_seconds', '最长运行秒数', 1, 86400],
        ] as const
      ).map(([key, text, min, max]) => (
        <label key={key}>
          {text}
          <input
            type="number"
            min={min}
            max={max}
            value={budget[key]}
            onChange={(event) =>
              onChange({
                ...budget,
                [key]: Math.min(max, Math.max(min, Number(event.target.value) || min)),
              })
            }
          />
        </label>
      ))}
      <p className="muted small">
        预算含探测、重试和被打断的请求。已知 Token 不等于实际金额；未返回用量的请求另行标记。
      </p>
    </fieldset>
  );
}
export default function RuntimePanel({
  world,
  names,
}: {
  world: World;
  names: Record<string, string>;
}) {
  const runtime = world.runtime;
  if (!runtime) return null;
  const budget = runtime.budget;
  const pending = runtime.pending_requests ?? [];
  return (
    <details className="runtime-panel" open>
      <summary>预算与待处理状态</summary>
      <div className="runtime-metrics">
        {(['calls', 'tokens', 'wall_seconds'] as const).map((key, index) => (
          <div key={key}>
            <span>{['请求数', 'Token', '运行秒数'][index]}</span>
            <strong>
              {number(budget?.used?.[key])} /{' '}
              {number(
                budget?.limits?.[
                  ['max_calls', 'max_total_tokens', 'max_wall_seconds'][index] as keyof RunBudget
                ],
              )}
            </strong>
            <small>剩余 {number(budget?.remaining?.[key])}</small>
          </div>
        ))}
        <div>
          <span>已知费用</span>
          <strong>
            {budget?.cost?.known
              ? `${number(budget.cost.amount)} ${budget.cost.currency ?? ''}`
              : '金额未知'}
          </strong>
          <small>供应商未提供费用时不推算</small>
        </div>
      </div>
      <p className="muted small">
        Token 为本次执行已记录用量；本存档累计未返回用量的请求：
        {number(budget?.unknown_usage_requests)}
        。此部分不记为 0。
      </p>
      <p className="small">
        当前阶段：{label(runtime.active_stage)} · 当前角色：
        {runtime.active_actor_id
          ? (names[runtime.active_actor_id] ?? runtime.active_actor_id)
          : '系统 / 裁判 / 吟游诗人'}
      </p>
      {runtime.waiting_reason && (
        <p className="small">
          等待原因：
          {typeof runtime.waiting_reason === 'string'
            ? label(runtime.waiting_reason)
            : asText(runtime.waiting_reason)}
        </p>
      )}
      {pending.length ? (
        <ul className="pending-requests">
          {pending.map((request, index) => (
            <li key={String(request.slot_id ?? index)}>
              {request.actor_id
                ? (names[String(request.actor_id)] ?? String(request.actor_id))
                : '系统 Agent'}{' '}
              · {label(request.stage)} · {label(request.status)} · 尝试 {asText(request.attempt)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">没有等待中的模型请求。</p>
      )}
      <details>
        <summary>执行策略与存档版本</summary>
        <pre>
          {asText({
            execution_policy: runtime.execution_policy,
            save_schema_version: runtime.save_schema_version,
          })}
        </pre>
      </details>
    </details>
  );
}
