import { useState } from 'react';
import { AlertTriangle, Check, Copy, Download, KeyRound } from 'lucide-react';

/**
 * The one and only display of an account's recovery codes.
 *
 * The backend returns them exactly once and keeps only bcrypt digests, so there is
 * no "show them again" call to fall back on. That is why Continue stays disabled
 * until the checkbox is ticked: losing this screen without saving the codes means
 * a lost phone locks the account until an operator runs `scripts/reset_mfa.py`.
 */
export default function RecoveryCodesPanel({
  codes,
  onContinue,
  continueLabel = 'Continue',
}: {
  codes: string[];
  onContinue: () => void;
  continueLabel?: string;
}) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);

  const asText = () =>
    [
      'ShelfMonitor recovery codes',
      '',
      'Each code works once, as a replacement for your authenticator app.',
      'Store them somewhere safe and offline.',
      '',
      ...codes,
    ].join('\n');

  const handleCopy = async () => {
    try {
      await navigator.clipboard?.writeText(codes.join('\n'));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be blocked (insecure origin, denied permission). The
      // codes are on screen and downloadable, so this is not worth an error banner.
    }
  };

  const handleDownload = () => {
    const blob = new Blob([asText()], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'shelfmonitor-recovery-codes.txt';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <span>
          These codes are shown <strong>once</strong>. Save them now — they are the only way
          back in if you lose your phone.
        </span>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-gray-700">
            <KeyRound className="h-4 w-4 text-sky-600" aria-hidden="true" />
            Recovery codes
          </h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-colors"
            >
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button
              type="button"
              onClick={handleDownload}
              className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 transition-colors"
            >
              <Download className="h-3.5 w-3.5" />
              Download
            </button>
          </div>
        </div>

        <ul
          className="grid grid-cols-2 gap-2 rounded-lg border border-gray-200 bg-gray-50 p-3 font-mono text-sm text-gray-800"
          aria-label="Recovery codes"
        >
          {codes.map((code) => (
            <li key={code} className="rounded bg-white px-2 py-1 text-center border border-gray-100">
              {code}
            </li>
          ))}
        </ul>
      </div>

      <label className="flex items-start gap-2 text-sm text-gray-700">
        <input
          type="checkbox"
          checked={saved}
          onChange={(event) => setSaved(event.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-gray-300 text-sky-600 focus:ring-sky-500"
        />
        <span>I&apos;ve saved these codes somewhere safe.</span>
      </label>

      <button
        type="button"
        onClick={onContinue}
        disabled={!saved}
        className="w-full rounded-lg bg-sky-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-sky-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
      >
        {continueLabel}
      </button>
    </div>
  );
}
