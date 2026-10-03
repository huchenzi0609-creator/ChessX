import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe('observer archive request contract', () => {
  it('prepares an explicit observer archive and delegates streaming download to the browser', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        download_url: '/api/exports/archive-token',
        filename: 'observer.zip',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await api.export('fixture-world', 'observer');
    expect(fetchMock).toHaveBeenCalledWith('/api/worlds/fixture-world/export?download=prepare', {
      method: 'POST',
      body: JSON.stringify({ view: 'observer' }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(click).toHaveBeenCalledOnce();
    expect(click.mock.instances[0]).toHaveAttribute('href', '/api/exports/archive-token');
    expect(click.mock.instances[0]).toHaveAttribute('download', 'observer.zip');
  });
  it('does not follow an unexpected external download URL', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          download_url: 'https://other.invalid/private.zip',
          filename: 'research.zip',
        }),
      }),
    );
    await expect(api.export('w', 'research')).rejects.toThrow('导出下载地址无效');
  });
  it('sends the user budget with a run command', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ status: 'accepted' }) });
    vi.stubGlobal('fetch', fetchMock);
    const budget = { max_calls: 60, max_total_tokens: 20000, max_wall_seconds: 240 };
    await api.command('w', 'run', 3, budget);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toMatchObject({ kind: 'run', turns: 3, budget });
  });
});
