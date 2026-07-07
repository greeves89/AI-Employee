"use client";

interface HeaderProps {
  title: string;
  subtitle?: string;
  /** Extra classes for the subtitle (e.g. "hidden lg:block" to hide it on mobile
   *  when the page renders its own collapsible description). */
  subtitleClassName?: string;
  actions?: React.ReactNode;
}

export function Header({ title, subtitle, subtitleClassName, actions }: HeaderProps) {
  return (
    <div className="sticky top-0 z-20 flex flex-col items-start gap-3 border-b border-foreground/[0.06] bg-background/80 backdrop-blur-lg py-4 pr-6 pl-16 lg:pl-6 sm:flex-row sm:items-center sm:justify-between sm:gap-2">
      <div className="min-w-0">
        <h2 className="text-2xl font-semibold tracking-tight truncate">{title}</h2>
        {subtitle && (
          <p className={`text-sm text-muted-foreground mt-0.5 ${subtitleClassName ?? ""}`}>{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2 max-w-full">{actions}</div>}
    </div>
  );
}
