import { describe, it, expect } from 'vitest';

import { ApiError } from '../client';
import { describeError, isNetworkError } from '../errors';

describe('isNetworkError', () => {
  it('recognises the browser-specific wording for a request that never completed', () => {
    expect(isNetworkError(new TypeError('Failed to fetch'))).toBe(true);
    expect(isNetworkError(new TypeError('NetworkError when attempting to fetch resource.'))).toBe(true);
    expect(isNetworkError(new TypeError('Load failed'))).toBe(true);
  });

  it('ignores non-fetch TypeErrors and HTTP errors that do have a status', () => {
    expect(isNetworkError(new TypeError('x.map is not a function'))).toBe(false);
    expect(isNetworkError(new Error('Failed to fetch'))).toBe(false);
    expect(isNetworkError(new ApiError(500, 'boom', 'http://example.test'))).toBe(false);
    expect(isNetworkError('Failed to fetch')).toBe(false);
  });
});

describe('describeError', () => {
  it('prefers the backend detail for a non-2xx response', () => {
    expect(describeError(new ApiError(403, 'Not your camera.', 'http://example.test'))).toBe(
      'Not your camera.',
    );
  });

  it('turns an opaque network failure into an actionable message', () => {
    const message = describeError(new TypeError('Failed to fetch'));
    expect(message).toMatch(/server could not be reached/i);
    expect(message).not.toMatch(/failed to fetch/i);
  });
});
