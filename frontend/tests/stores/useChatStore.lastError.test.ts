import { describe, it, expect, beforeEach } from 'vitest';
import { useChatStore } from '@/stores/useChatStore';

describe('useChatStore — lastError', () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it('starts empty', () => {
    expect(useChatStore.getState().lastError).toBeNull();
  });

  it('records the failure message', () => {
    useChatStore.getState().setLastError('Session model is invalid. Please select a model.');
    expect(useChatStore.getState().lastError).toBe(
      'Session model is invalid. Please select a model.',
    );
  });

  it('clears the failure message', () => {
    useChatStore.getState().setLastError('boom');
    useChatStore.getState().setLastError(null);
    expect(useChatStore.getState().lastError).toBeNull();
  });

  it('reset() clears the failure message', () => {
    useChatStore.getState().setLastError('boom');
    useChatStore.getState().reset();
    expect(useChatStore.getState().lastError).toBeNull();
  });
});
