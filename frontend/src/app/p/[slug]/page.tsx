"use client";

/** Eine vom Administrator angelegte Seite anzeigen.
 *
 *  Der Rahmen laedt die fremde Adresse direkt im Browser des Nutzers — wir
 *  reichen nichts durch. Ob sich die Seite einbetten laesst, entscheidet
 *  ausschliesslich sie selbst (``X-Frame-Options`` /
 *  ``Content-Security-Policy: frame-ancestors``). Wird sie abgewiesen, bleibt
 *  der Rahmen weiss und der Browser verraet uns den Grund nicht: fremde Rahmen
 *  duerfen nicht ausgelesen werden. Deshalb der Hinweis nach kurzer Wartezeit
 *  samt Weg im neuen Tab, statt einer Fehlermeldung, die wir gar nicht kennen.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, ExternalLink, Loader2, RefreshCw, ShieldAlert } from "lucide-react";
import { Header } from "@/components/layout/header";
import * as api from "@/lib/api";
import { pageIcon } from "@/lib/page-icons";

// Nach dieser Zeit ohne Ladebestaetigung gehen wir davon aus, dass die Seite das
// Einbetten verweigert. Grosszuegig gewaehlt: ein langsam startender Dienst soll
// nicht als "verweigert" dastehen.
const FRAME_HINT_AFTER_MS = 8000;

export default function CustomPageView() {
  const params = useParams<{ slug: string }>();
  const slug = typeof params?.slug === "string" ? params.slug : "";
  const [page, setPage] = useState<api.CustomPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [frameLoaded, setFrameLoaded] = useState(false);
  const [hintVisible, setHintVisible] = useState(false);
  // Zaehler im src-Schluessel: erhoehen laedt den Rahmen neu, ohne dass wir an
  // contentWindow fassen muessten (was uns fremd-domain ohnehin verwehrt ist).
  const [reloadKey, setReloadKey] = useState(0);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!slug) return;
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .getCustomPageBySlug(slug)
      .then((p) => {
        if (alive) setPage(p);
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "Seite konnte nicht geladen werden");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [slug]);

  // Wartezeit-Hinweis neu aufziehen, sobald ein Rahmen (neu) geladen wird.
  useEffect(() => {
    if (!page || page.open_mode !== "iframe") return;
    setFrameLoaded(false);
    setHintVisible(false);
    if (hintTimer.current) clearTimeout(hintTimer.current);
    hintTimer.current = setTimeout(() => setHintVisible(true), FRAME_HINT_AFTER_MS);
    return () => {
      if (hintTimer.current) clearTimeout(hintTimer.current);
    };
  }, [page, reloadKey]);

  const onFrameLoad = useCallback(() => {
    setFrameLoaded(true);
    setHintVisible(false);
    if (hintTimer.current) clearTimeout(hintTimer.current);
  }, []);

  const openExternal = () => {
    if (page) window.open(page.url, "_blank", "noopener,noreferrer");
  };

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !page) {
    const forbidden = (error ?? "").includes("403") || (error ?? "").toLowerCase().includes("zugriff");
    return (
      <div className="flex min-h-screen items-center justify-center p-6">
        <div className="w-full max-w-md rounded-2xl border border-border bg-card p-8 text-center">
          <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full bg-amber-500/15">
            <ShieldAlert className="h-7 w-7 text-amber-700 dark:text-amber-400" />
          </div>
          <p className="text-base font-semibold">
            {forbidden ? "Kein Zugriff auf diese Seite" : "Seite nicht gefunden"}
          </p>
          <p className="mt-2 text-[13px] text-muted-foreground">
            {forbidden
              ? "Deine Rolle darf diesen Menüpunkt nicht öffnen. Wende dich an einen Administrator."
              : "Der Menüpunkt wurde entfernt oder abgeschaltet."}
          </p>
        </div>
      </div>
    );
  }

  const Icon = pageIcon(page.icon);

  // Als Link angelegt: nicht einbetten, sondern anbieten. Ein automatisches
  // Aufpoppen wuerde der Browser blockieren und der Nutzer saehe nichts.
  if (page.open_mode === "link") {
    return (
      <div className="flex min-h-screen flex-col">
        <Header title={page.title} subtitle={page.description ?? undefined} />
        <div className="flex flex-1 items-center justify-center p-6">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-8 text-center">
            <div className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-full bg-primary/10">
              <Icon className="h-7 w-7 text-primary" />
            </div>
            <p className="text-base font-semibold">{page.title}</p>
            {page.description && (
              <p className="mt-2 text-[13px] text-muted-foreground">{page.description}</p>
            )}
            <button
              onClick={openExternal}
              className="mt-6 inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground transition-opacity hover:opacity-90"
            >
              <ExternalLink className="h-4 w-4" />
              In neuem Tab öffnen
            </button>
            <p className="mt-4 break-all text-[11px] text-muted-foreground/60">{page.url}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      <Header
        title={page.title}
        subtitle={page.description ?? undefined}
        actions={
          <>
            <button
              onClick={() => setReloadKey((k) => k + 1)}
              title="Neu laden"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border text-muted-foreground transition-colors hover:text-foreground"
            >
              <RefreshCw className="h-4 w-4" />
            </button>
            <button
              onClick={openExternal}
              className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              In neuem Tab
            </button>
          </>
        }
      />

      {hintVisible && !frameLoaded && (
        <div className="flex items-start gap-3 border-b border-amber-500/20 bg-amber-500/[0.07] px-6 py-3">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700 dark:text-amber-400" />
          <div className="min-w-0 text-[12px] text-muted-foreground">
            <span className="font-medium text-foreground">Bleibt der Bereich leer?</span> Dann
            erlaubt <span className="break-all font-mono">{page.url}</span> das Einbetten nicht
            (<span className="font-mono">X-Frame-Options</span> bzw.{" "}
            <span className="font-mono">frame-ancestors</span>). Das lässt sich nur auf der
            Gegenseite freigeben — bis dahin hilft „In neuem Tab".
          </div>
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        {!frameLoaded && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        )}
        <iframe
          key={reloadKey}
          src={page.url}
          title={page.title}
          onLoad={onFrameLoad}
          className="h-full w-full border-0 bg-background"
          referrerPolicy="strict-origin-when-cross-origin"
          allow={
            page.allow_media
              ? "microphone; camera; clipboard-read; clipboard-write; fullscreen"
              : "clipboard-write; fullscreen"
          }
        />
      </div>
    </div>
  );
}
