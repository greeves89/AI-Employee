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
      {/* w-full: In der Handy-Spalte (items-start) war der Block sonst nur so
          breit wie sein Inhalt — der Untertitel lief über den Rand und wurde ohne
          „…" abgeschnitten (#907). */}
      <div className="w-full min-w-0 sm:w-auto sm:flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="truncate text-xl font-semibold tracking-tight sm:text-2xl">{title}</h2>
          {titleAdornment}
        </div>
        {subtitle && (
          // Gekürzt mit „…": auf dem Handy zwei Zeilen, ab sm eine. Die
          // Beschreibung eines Agenten kann mehrere Sätze lang sein; ungekürzt
          // schob sie den Chat auf kleinen Bildschirmen in die untere Hälfte.
          // Der volle Text steht im Tooltip. ``subtitleClassName`` sitzt am
          // Rahmen, damit ein „hidden" nicht mit der Zeilenkürzung kollidiert.
          <div className={subtitleClassName}>
            <p
              title={subtitle}
              className="mt-0.5 line-clamp-2 break-words text-[13px] text-muted-foreground sm:block sm:truncate"
            >
              {subtitle}
            </p>
          </div>
        )}
      </div>
      {actions && <div className="flex max-w-full flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
