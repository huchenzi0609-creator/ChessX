import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import ScenarioEditor from './ScenarioEditor';
vi.mock('../lib/api', () => ({
  api: { scenarios: vi.fn(), scenario: vi.fn(), validate: vi.fn(), create: vi.fn() },
}));
describe('scenario creation validation', () => {
  beforeEach(() => {
    vi.mocked(api.scenarios).mockResolvedValue([
      { id: 'seal', title: '印信', description: '两人相遇' },
    ]);
    vi.mocked(api.scenario).mockResolvedValue({ scenario_id: 'seal', pieces: [] });
    vi.mocked(api.create).mockClear();
  });
  it('keeps invalid configuration reviewable and never creates a world', async () => {
    vi.mocked(api.validate).mockResolvedValue({
      valid: false,
      errors: [{ path: 'board.rows', message: 'must be >= 8' }],
    });
    render(
      <ScenarioEditor
        health={{ status: 'ok', model_configured: false, rule_version: 'demo-v2.1' }}
        onCreated={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect((screen.getByLabelText(/场景配置 JSON/) as HTMLTextAreaElement).value).toContain(
        'seal',
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: '创建实验 →' }));
    await screen.findByText('场景校验未通过');
    expect(api.create).not.toHaveBeenCalled();
    expect(screen.getByText(/board.rows/)).toBeInTheDocument();
  });
  it('clearly marks fixture responses and prevents live creation without backend model configuration', async () => {
    render(
      <ScenarioEditor
        health={{ status: 'ok', model_configured: false, rule_version: 'demo-v2.1' }}
        onCreated={vi.fn()}
      />,
    );
    expect(screen.getByText('验证规则与运行流程，非 AI 叙事')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /真实模型实验/ }));
    expect(screen.getByRole('button', { name: '创建实验 →' })).toBeDisabled();
    expect(document.querySelector('input[type="password"]')).toBeNull();
  });
  it('distinguishes configured credentials from unverified tokenizer readiness', async () => {
    render(
      <ScenarioEditor
        health={{
          status: 'ok',
          model_configured: true,
          tokenizer_agreement: false,
          rule_version: 'demo-v2.1',
        }}
        onCreated={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect((screen.getByLabelText(/场景配置 JSON/) as HTMLTextAreaElement).value).toContain(
        'seal',
      ),
    );
    expect(screen.getByText(/密钥配置已就绪，tokenizer 待核实/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /真实模型实验/ }));
    expect(screen.getByRole('button', { name: '创建实验 →' })).toBeDisabled();
    expect(api.create).not.toHaveBeenCalled();
  });
});
