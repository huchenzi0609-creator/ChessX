import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './api';
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});
describe('observer archive request contract', () => {
  it('sends explicit JSON content type and observer scope for a downloadable archive', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['zip']) });
    const revoke = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn().mockReturnValue('blob:archive'),
      revokeObjectURL: revoke,
    });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await api.export('fixture-world', 'observer');
    expect(fetchMock).toHaveBeenCalledWith('/api/worlds/fixture-world/export', {
      method: 'POST',
      body: JSON.stringify({ view: 'observer' }),
      headers: { 'Content-Type': 'application/json' },
    });
    expect(click).toHaveBeenCalledOnce();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:archive');
  });
});
