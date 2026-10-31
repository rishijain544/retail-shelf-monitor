import { useEffect, useRef } from 'react';

/**
 * A one-time-code field.
 *
 * One input rather than six boxes: it works with paste from an email or a password
 * manager, with SMS autofill (`autoComplete="one-time-code"`), and with a keyboard
 * on desktop and mobile alike. Digits are filtered on the way in, because a stray
 * space from a copied code is the most common reason a correct code is rejected.
 */
export default function NumericCodeInput({
  id,
  label,
  value,
  onChange,
  maxLength = 6,
  hint,
  disabled = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** 6 for TOTP and email codes; recovery codes are longer, so pass 40 there. */
  maxLength?: number;
  hint?: string;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // The code is the only thing this screen asks for, so focus it immediately.
    ref.current?.focus();
  }, []);

  return (
    <div>
      <label htmlFor={id} className="block text-sm font-semibold text-gray-700 mb-1.5">
        {label}
      </label>
      <input
        ref={ref}
        id={id}
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        value={value}
        onChange={(event) => onChange(event.target.value.replace(/[^0-9]/g, ''))}
        maxLength={maxLength}
        disabled={disabled}
        placeholder="123456"
        aria-describedby={hint ? `${id}-hint` : undefined}
        className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2.5 text-center text-2xl font-semibold tracking-[0.4em] text-gray-900 placeholder-gray-300 focus:border-sky-500 focus:outline-none focus:ring-2 focus:ring-sky-500/20 disabled:bg-gray-50"
      />
      {hint && (
        <p id={`${id}-hint`} className="mt-1.5 text-xs text-gray-400">
          {hint}
        </p>
      )}
    </div>
  );
}
