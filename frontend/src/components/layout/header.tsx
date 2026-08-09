"use client";

interface HeaderProps {
  title: string;
  subtitle?: string;
  /** Extra classes for the subtitle (e.g. "hidden lg:block" to hide it on mobile
   *  when the page renders its own collapsible description). */
  subtitleClassName?: string;
  /** Kleine Bedienelemente, die direkt zum Titel gehören — etwa der Stift zum
   *  Umbenennen. Sie stehen NEBEN dem Namen statt am rechten Rand: dort war der
   *  Bezug zum Namen nicht erkennbar, und die Kopfzeile wurde unnötig breit. */
  titleAdornment?: React.ReactNode;
  actions?: React.ReactNode;
}

export function Header({
  title,
  subtitle,
  subtitleClassName,
  titleAdornment,
  actions,
}: HeaderProps) {
  return (
    <div className="sticky top-0 z-20 flex flex-col items-start gap-2 border-b border-foreground/[0.06] bg-background/80 py-2.5 pl-16 pr-6 backdrop-blur-lg sm:flex-row sm:items-center sm:justify-between sm:gap-2 lg:pl-6">
      <div className="min-w-0">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">{title}</h2>
          {titleAdornment}
        </div>
        {subtitle && (
          // EINE Zeile, abgeschnitten. Die Beschreibung eines Agenten kann mehrere
          // Sätze lang sein; ungekürzt schob sie den Chat auf kleinen Bildschirmen
          // in die untere Hälfte. Der volle Text steht im Tooltip.
          <p
            title={subtitle}
            className={`mt-0.5 truncate text-[13px] text-muted-foreground ${subtitleClassName ?? ""}`}
          >
            {subtitle}
          </p>
        )}
      </div>
      {actions && <div className="flex max-w-full flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
