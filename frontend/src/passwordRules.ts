/**
 * The one password rule the UI enforces, shared by signup and password reset.
 *
 * These mirror `PasswordStr` in backend/app/schemas/auth.py exactly: at least 8
 * characters, at most 72 characters, and at most 72 **bytes** when UTF-8 encoded
 * (bcrypt reads no further). The byte limit is the one that matters for non-ASCII
 * input: a 40-character password of 3-byte characters is 120 bytes, and the server
 * rejects it with a 422 rather than hashing a truncated version of it.
 *
 * Keeping one copy means the reset form cannot quietly accept something signup would
 * refuse, and the two screens give the same answer as the API.
 */
export const MIN_PASSWORD_LENGTH = 8;
export const MAX_PASSWORD_CHARS = 72;
export const MAX_PASSWORD_BYTES = 72;

/** Shown under the field, and as the message when the rule is broken. */
export const PASSWORD_HINT = `Must be at least ${MIN_PASSWORD_LENGTH} characters.`;

/** UTF-8 size, the same measure bcrypt applies. */
export function passwordByteLength(password: string): number {
  return new TextEncoder().encode(password).length;
}

/**
 * Why this password is unacceptable, or null when it is fine.
 *
 * Only the client-side part of the rule lives here: the server has the final say.
 */
export function passwordError(password: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) return PASSWORD_HINT;
  if (password.length > MAX_PASSWORD_CHARS) {
    return `Must be at most ${MAX_PASSWORD_CHARS} characters.`;
  }
  const bytes = passwordByteLength(password);
  if (bytes > MAX_PASSWORD_BYTES) {
    return (
      `Too long: ${bytes} bytes when UTF-8 encoded, but the limit is ` +
      `${MAX_PASSWORD_BYTES}. Accented letters, emoji and many non-Latin characters ` +
      'count as several bytes each — use a shorter password.'
    );
  }
  return null;
}
