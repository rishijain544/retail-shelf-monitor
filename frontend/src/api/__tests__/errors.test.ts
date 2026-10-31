import { describe, it, expect } from 'vitest';

import { ApiError } from '../client';
import { describeAuthError, describeError, isLockoutError, isNetworkError } from '../errors';

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

describe('isLockoutError', () => {
  it('is true only for the two 429 lockouts', () => {
    expect(isLockoutError(new ApiError(429, '…', 'http://x', 'account_locked'))).toBe(true);
    expect(isLockoutError(new ApiError(429, '…', 'http://x', 'mfa_locked'))).toBe(true);
    expect(isLockoutError(new ApiError(429, '…', 'http://x', 'resend_cooldown'))).toBe(false);
    expect(isLockoutError(new ApiError(401, '…', 'http://x', 'mfa_code_invalid'))).toBe(false);
    expect(isLockoutError(new TypeError('Failed to fetch'))).toBe(false);
  });
});

describe('describeAuthError', () => {
  it('states the wait time from a lockout response', () => {
    const message = describeAuthError(
      new ApiError(
        429,
        'Too many failed sign-in attempts. Try again in 12 minute(s).',
        'http://x',
        'account_locked',
      ),
    );
    expect(message).toBe('Too many attempts. Try again in 12 minutes.');
  });

  it('tells the user to sign in again when the challenge expired', () => {
    expect(
      describeAuthError(new ApiError(401, '…', 'http://x', 'mfa_challenge_invalid')),
    ).toMatch(/expired.*sign in again/i);
  });

  it('falls back to the server message for anything else', () => {
    expect(describeAuthError(new ApiError(401, 'That code is not valid.', 'http://x', 'mfa_code_invalid'))).toBe(
      'That code is not valid.',
    );
  });
});
