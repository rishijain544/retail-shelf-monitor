import { ShieldCheck } from 'lucide-react';

/**
 * The two-panel auth layout: brand story on the left, a single focused card on the
 * right. Mirrors LoginPage/SignupPage so a visitor moving from signup to the
 * verification and enrolment screens stays in the same visual context.
 *
 * Kept small on purpose: the screens that use it own their own headings and forms.
 */
export default function AuthShell({
  title,
  subtitle,
  leftHeading,
  leftBody,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  leftHeading: string;
  leftBody: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div className="min-h-screen flex">
      {/* Left panel — brand */}
      <div className="hidden md:flex md:w-1/2 bg-sky-600 flex-col justify-center px-10 text-white">
        <div className="flex items-center gap-3 mb-6">
          <div className="bg-white/20 p-2 rounded-lg">
            <ShieldCheck className="h-8 w-8" />
          </div>
          <div className="text-xl font-bold">ShelfMonitor</div>
        </div>
        <h2 className="text-3xl font-bold leading-tight mb-4">{leftHeading}</h2>
        <p className="text-white/80 text-lg max-w-md">{leftBody}</p>
      </div>

      {/* Right panel — card */}
      <div className="w-full md:w-1/2 flex items-center justify-center px-6 py-10">
        <div className="w-full max-w-md">
          <div className="md:hidden flex items-center gap-3 mb-6">
            <div className="bg-sky-600 text-white p-2 rounded-lg">
              <ShieldCheck className="h-6 w-6" />
            </div>
            <div className="text-xl font-bold text-gray-900">ShelfMonitor</div>
          </div>

          <h1 className="text-2xl font-bold text-gray-900 mb-2">{title}</h1>
          {subtitle && <p className="text-sm text-gray-500 mb-8">{subtitle}</p>}

          {children}

          {footer && <div className="mt-6 text-center text-sm text-gray-500">{footer}</div>}
        </div>
      </div>
    </div>
  );
}
