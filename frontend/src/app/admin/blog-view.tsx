"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ExternalLink,
  Globe,
  ImagePlus,
  Loader2,
  Newspaper,
  Plus,
  Save,
  Trash2,
  Undo2,
} from "lucide-react";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import { apiFehlertext } from "@/lib/api-fehler";
import type { BlogBild, BlogFaq, BlogPost, BlogPostKurz, BlogPruefung, BlogStatus } from "@/lib/api";

/**
 * Blog der Landingpage: Beiträge schreiben, prüfen und veröffentlichen.
 *
 * Dieselben Regeln wie der MCP-Dienst des Blogs — die Seite ist nur der Weg
 * für Menschen. Ein neuer Beitrag ist ein Entwurf; veröffentlicht wird erst,
 * wenn die Prüfung keine Fehler mehr meldet. Die Adresse eines Beitrags steht
 * nach dem Anlegen fest, weil eine umbenannte Adresse ihre Verweise verliert.
 */

interface Entwurf {
  slug: string;
  title: string;
  description: string;
  keyword: string;
  body_markdown: string;
  tags: string;
  author: string;
  cover: string;
  faq: BlogFaq[];
}

const LEER: Entwurf = {
  slug: "", title: "", description: "", keyword: "", body_markdown: "", tags: "", author: "", cover: "", faq: [],
};

const FELD =
  "w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3 py-2 text-xs outline-none focus:border-primary/50";

function ausBeitrag(p: BlogPost): Entwurf {
  return {
    slug: p.slug,
    title: p.titel,
    description: p.beschreibung,
    keyword: p.hauptbegriff,
    body_markdown: p.body_markdown,
    tags: (p.themen ?? []).join(", "),
    author: p.autor ?? "",
    cover: p.titelbild ?? "",
    faq: p.faq ?? [],
  };
}

function datum(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("de-DE", { day: "numeric", month: "long", year: "numeric" });
}

function StatusMarke({ status }: { status: "draft" | "published" }) {
  return (
    <span
      className={cn(
        "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium",
        status === "published"
          ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          : "border-foreground/[0.1] bg-foreground/[0.04] text-muted-foreground",
      )}
    >
      {status === "published" ? "Veröffentlicht" : "Entwurf"}
    </span>
  );
}

function Zaehler({ wert, von, bis }: { wert: number; von: number; bis: number }) {
  const passt = wert >= von && wert <= bis;
  return (
    <span className={cn("text-[10px]", passt ? "text-muted-foreground/60" : "text-amber-700 dark:text-amber-400")}>
      {wert} Zeichen · üblich {von} bis {bis}
    </span>
  );
}

function Pruefung({ p }: { p: BlogPruefung }) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        {[
          ["Wörter", p.worte],
          ["Lesezeit", `${p.lesezeit_minuten} Min.`],
          ["Zwischenüberschriften", p.zwischenueberschriften],
          ["Verweise auf eigene Seiten", p.interne_verweise],
          ["Bilder im Text", p.bilder],
        ].map(([name, wert]) => (
          <div key={String(name)} className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-3 py-2">
            <p className="text-muted-foreground/60">{name}</p>
            <p className="mt-0.5 text-sm font-semibold tabular-nums">{wert}</p>
          </div>
        ))}
      </div>
      {p.fehler.length === 0 && p.hinweise.length === 0 && (
        <p className="flex items-start gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/[0.06] p-3 text-[11px] text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Nichts zu beanstanden.
        </p>
      )}
      {p.fehler.length > 0 && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/[0.06] p-3">
          <p className="text-[11px] font-semibold text-red-600 dark:text-red-400">Verhindert die Veröffentlichung</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[11px] text-red-600 dark:text-red-400">
            {p.fehler.map((f) => <li key={f}>{f}</li>)}
          </ul>
        </div>
      )}
      {p.hinweise.length > 0 && (
        <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] p-3">
          <p className="text-[11px] font-semibold text-amber-700 dark:text-amber-400">Hinweise</p>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[11px] text-amber-700 dark:text-amber-300/90">
            {p.hinweise.map((h) => <li key={h}>{h}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

export function BlogView() {
  const toast = useToast();
  const confirm = useConfirm();

  const [status, setStatus] = useState<BlogStatus | null>(null);
  const [posts, setPosts] = useState<BlogPostKurz[]>([]);
  const [laedt, setLaedt] = useState(true);
  const [ladefehler, setLadefehler] = useState<string | null>(null);

  // null = Liste; sonst der geöffnete Beitrag ("" als slug = neuer Beitrag).
  const [offen, setOffen] = useState<BlogPost | null>(null);
  const [neu, setNeu] = useState(false);
  const [entwurf, setEntwurf] = useState<Entwurf>(LEER);
  const [gespeichert, setGespeichert] = useState<Entwurf>(LEER);
  const [arbeitet, setArbeitet] = useState<string | null>(null);

  const [bilder, setBilder] = useState<BlogBild[]>([]);
  const [bildAlt, setBildAlt] = useState("");
  const [laedtHoch, setLaedtHoch] = useState(false);
  const dateiwahl = useRef<HTMLInputElement>(null);
  const textfeld = useRef<HTMLTextAreaElement>(null);

  const laden = useCallback(async () => {
    try {
      const s = await api.getBlogStatus();
      setStatus(s);
      if (s.enabled) {
        const [p, b] = await Promise.all([api.listBlogPosts(), api.listBlogImages()]);
        setPosts(p.posts);
        setBilder(b.images);
      }
      setLadefehler(null);
    } catch (e) {
      // Ein Ladefehler ist etwas anderes als ein abgeschalteter Blog — getrennt zeigen.
      setLadefehler(apiFehlertext(e, "Der Blog konnte nicht geladen werden."));
    } finally {
      setLaedt(false);
    }
  }, []);

  useEffect(() => {
    laden();
  }, [laden]);

  const uebernehmen = (p: BlogPost) => {
    const e = ausBeitrag(p);
    setOffen(p);
    setNeu(false);
    setEntwurf(e);
    setGespeichert(e);
  };

  const oeffnen = async (slug: string) => {
    setArbeitet(`oeffnen:${slug}`);
    try {
      uebernehmen(await api.getBlogPost(slug));
    } catch (e) {
      toast.error("Beitrag konnte nicht geöffnet werden.", apiFehlertext(e));
    } finally {
      setArbeitet(null);
    }
  };

  const neuerBeitrag = () => {
    setOffen(null);
    setNeu(true);
    setEntwurf(LEER);
    setGespeichert(LEER);
  };

  const ungespeichert = JSON.stringify(entwurf) !== JSON.stringify(gespeichert);

  const zurueckZurListe = async () => {
    if (ungespeichert) {
      const ok = await confirm({
        title: "Änderungen verwerfen?",
        message: "Der Beitrag hat ungespeicherte Änderungen.",
        confirmLabel: "Verwerfen",
        variant: "destructive",
      });
      if (!ok) return;
    }
    setOffen(null);
    setNeu(false);
    laden();
  };

  const eingabe = (): api.BlogPostEingabe => ({
    title: entwurf.title,
    description: entwurf.description,
    keyword: entwurf.keyword,
    body_markdown: entwurf.body_markdown,
    tags: entwurf.tags.split(",").map((t) => t.trim()).filter(Boolean),
    faq: entwurf.faq.filter((f) => f.frage.trim() && f.antwort.trim()),
    author: entwurf.author,
    cover: entwurf.cover,
  });

  const speichern = async (): Promise<BlogPost | null> => {
    setArbeitet("speichern");
    try {
      const p = neu
        ? await api.createBlogPost({ ...eingabe(), ...(entwurf.slug.trim() ? { slug: entwurf.slug.trim() } : {}) })
        : await api.updateBlogPost(entwurf.slug, eingabe());
      uebernehmen(p);
      toast.success(
        p.status === "published" ? "Gespeichert — die Änderung ist online." : "Entwurf gespeichert.",
      );
      return p;
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", apiFehlertext(e));
      return null;
    } finally {
      setArbeitet(null);
    }
  };

  const veroeffentlichen = async () => {
    const aktuell = ungespeichert || neu ? await speichern() : offen;
    if (!aktuell) return;
    const ok = await confirm({
      title: `„${aktuell.titel}“ veröffentlichen?`,
      message: "Der Beitrag ist danach öffentlich erreichbar und erscheint in Übersicht, Feed und Sitemap.",
      confirmLabel: "Veröffentlichen",
    });
    if (!ok) return;
    setArbeitet("veroeffentlichen");
    try {
      uebernehmen(await api.publishBlogPost(aktuell.slug));
      toast.success("Veröffentlicht.");
    } catch (e) {
      toast.error("Noch nicht veröffentlicht", apiFehlertext(e));
    } finally {
      setArbeitet(null);
    }
  };

  const zurueckziehen = async () => {
    if (!offen) return;
    const ok = await confirm({
      title: `„${offen.titel}“ zurückziehen?`,
      message: "Der Beitrag ist danach nicht mehr öffentlich erreichbar. Er bleibt als Entwurf erhalten.",
      confirmLabel: "Zurückziehen",
      variant: "destructive",
    });
    if (!ok) return;
    setArbeitet("zurueckziehen");
    try {
      uebernehmen(await api.unpublishBlogPost(offen.slug));
      toast.success("Zurück im Entwurf.");
    } catch (e) {
      toast.error("Zurückziehen fehlgeschlagen", apiFehlertext(e));
    } finally {
      setArbeitet(null);
    }
  };

  const loeschen = async () => {
    if (!offen) return;
    const ok = await confirm({
      title: `„${offen.titel}“ löschen?`,
      message:
        offen.status === "published"
          ? "Der Beitrag ist veröffentlicht. Nach dem Löschen führt seine Adresse ins Leere."
          : "Der Entwurf wird endgültig gelöscht.",
      confirmLabel: "Löschen",
      variant: "destructive",
    });
    if (!ok) return;
    setArbeitet("loeschen");
    try {
      await api.deleteBlogPost(offen.slug);
      toast.success("Gelöscht.");
      setOffen(null);
      setNeu(false);
      laden();
    } catch (e) {
      toast.error("Löschen fehlgeschlagen", apiFehlertext(e));
    } finally {
      setArbeitet(null);
    }
  };

  const bildHochladen = async (datei: File | undefined) => {
    if (!datei) return;
    setLaedtHoch(true);
    try {
      const bild = await api.uploadBlogImage(datei, bildAlt.trim());
      setBilder((v) => [bild, ...v.filter((b) => b.name !== bild.name)]);
      setBildAlt("");
      toast.success("Bild hochgeladen.", bild.name);
    } catch (e) {
      toast.error("Bild nicht hochgeladen", apiFehlertext(e));
    } finally {
      setLaedtHoch(false);
      if (dateiwahl.current) dateiwahl.current.value = "";
    }
  };

  const bildEinfuegen = (bild: BlogBild) => {
    // An der Schreibmarke einfügen, als eigener Absatz.
    const feld = textfeld.current;
    const stelle = feld ? feld.selectionStart : entwurf.body_markdown.length;
    const vorher = entwurf.body_markdown.slice(0, stelle).replace(/\n*$/, "");
    const nachher = entwurf.body_markdown.slice(stelle).replace(/^\n*/, "");
    setEntwurf((v) => ({ ...v, body_markdown: `${vorher}${vorher ? "\n\n" : ""}${bild.markdown}\n\n${nachher}` }));
  };

  const bildLoeschen = async (bild: BlogBild) => {
    const ok = await confirm({
      title: `„${bild.name}“ löschen?`,
      message: "Das geht nur, solange kein Beitrag das Bild verwendet.",
      confirmLabel: "Löschen",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await api.deleteBlogImage(bild.name);
      setBilder((v) => v.filter((b) => b.name !== bild.name));
      if (entwurf.cover === bild.name) setEntwurf((v) => ({ ...v, cover: "" }));
    } catch (e) {
      toast.error("Bild nicht gelöscht", apiFehlertext(e));
    }
  };

  const setze = <K extends keyof Entwurf>(feld: K, wert: Entwurf[K]) => setEntwurf((v) => ({ ...v, [feld]: wert }));
  const setzeFaq = (i: number, feld: keyof BlogFaq, wert: string) =>
    setEntwurf((v) => ({ ...v, faq: v.faq.map((f, n) => (n === i ? { ...f, [feld]: wert } : f)) }));

  if (laedt) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (ladefehler || !status) {
    return (
      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Blog der Landingpage</h2>
        <div className="rounded-xl border border-red-500/20 bg-red-500/[0.06] p-5 text-xs leading-relaxed text-red-600 dark:text-red-400">
          <p className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4" />
            Der Blog konnte nicht geladen werden.
          </p>
          {ladefehler && <p className="mt-2">{ladefehler}</p>}
          <button
            onClick={() => { setLaedt(true); laden(); }}
            className="mt-3 rounded-lg border border-red-500/30 px-3 py-1.5 text-xs font-medium transition-colors hover:bg-red-500/10"
          >
            Erneut versuchen
          </button>
        </div>
      </section>
    );
  }

  if (!status.enabled) {
    return (
      <section className="space-y-3">
        <h2 className="text-sm font-semibold">Blog der Landingpage</h2>
        <div className="rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] p-5 text-xs leading-relaxed text-muted-foreground">
          <p className="flex items-center gap-2 font-medium text-foreground">
            <Newspaper className="h-4 w-4" />
            {status.state === "ohne_adresse"
              ? "Der Blog ist eingeschaltet, aber die öffentliche Adresse fehlt."
              : "Der Blog ist auf dieser Installation nicht eingeschaltet."}
          </p>
          {status.state === "ohne_adresse" && (
            <p className="mt-2">
              Ohne <code className="font-mono">BLOG_BASE_URL</code> stimmen die Verweise in Sitemap, Feed und
              Seitenkopf nicht — deshalb bleibt der Blog aus, bis die Adresse in der{" "}
              <code className="font-mono">.env</code> steht.
            </p>
          )}
          <p className="mt-2">
            Er gehört zur öffentlichen Landingpage und wird in der <code className="font-mono">.env</code> der
            Installation eingeschaltet (<code className="font-mono">BLOG_ENABLED=true</code>, dazu die öffentliche
            Adresse und ein Schlüssel für den MCP-Dienst). Die Schritte stehen in{" "}
            <code className="font-mono">docs/BLOG.md</code>.
          </p>
        </div>
      </section>
    );
  }

  // ── Editor ──
  if (offen || neu) {
    const beschaeftigt = arbeitet !== null;
    return (
      <div className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <button
            onClick={zurueckZurListe}
            className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Alle Beiträge
          </button>
          <div className="flex flex-wrap items-center gap-2">
            {offen && <StatusMarke status={offen.status} />}
            {offen && (
              <a
                href={offen.vorschau}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-3 py-2 text-xs transition-colors hover:bg-foreground/[0.04]"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                {offen.status === "published" ? "Ansehen" : "Vorschau"}
              </a>
            )}
            <button
              onClick={speichern}
              disabled={beschaeftigt || !ungespeichert || !entwurf.title.trim() || !entwurf.body_markdown.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-3 py-2 text-xs font-medium transition-colors hover:bg-foreground/[0.04] disabled:opacity-40"
            >
              {arbeitet === "speichern" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              Speichern
            </button>
            {offen?.status === "published" ? (
              <button
                onClick={zurueckziehen}
                disabled={beschaeftigt}
                className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-3 py-2 text-xs font-medium transition-colors hover:bg-foreground/[0.04] disabled:opacity-40"
              >
                {arbeitet === "zurueckziehen" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Undo2 className="h-3.5 w-3.5" />}
                Zurückziehen
              </button>
            ) : (
              <button
                onClick={veroeffentlichen}
                disabled={beschaeftigt || !entwurf.title.trim() || !entwurf.body_markdown.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
              >
                {arbeitet === "veroeffentlichen" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Globe className="h-3.5 w-3.5" />}
                Veröffentlichen
              </button>
            )}
            {offen && (
              <button
                onClick={loeschen}
                disabled={beschaeftigt}
                title="Beitrag löschen"
                className="rounded-lg p-2 text-muted-foreground/50 transition-colors hover:text-red-500 disabled:opacity-40"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="space-y-4">
            <label className="block space-y-1.5">
              <span className="text-xs font-medium">Titel</span>
              <input
                value={entwurf.title}
                onChange={(e) => setze("title", e.target.value)}
                maxLength={200}
                placeholder="Die Frage, die der Beitrag beantwortet"
                className={cn(FELD, "text-sm")}
              />
              <Zaehler wert={entwurf.title.length} von={30} bis={60} />
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block space-y-1.5">
                <span className="text-xs font-medium">Adresse</span>
                <div className="flex items-center gap-1.5">
                  <span className="shrink-0 font-mono text-[11px] text-muted-foreground/60">/blog/</span>
                  <input
                    value={entwurf.slug}
                    onChange={(e) => setze("slug", e.target.value.toLowerCase())}
                    disabled={!neu}
                    maxLength={80}
                    placeholder="entsteht aus dem Titel"
                    className={cn(FELD, "font-mono disabled:opacity-60")}
                  />
                </div>
                <span className="text-[10px] text-muted-foreground/60">
                  {neu ? "Kleinbuchstaben, Ziffern, Bindestriche. Steht nach dem Anlegen fest." : "Steht fest, damit Verweise gültig bleiben."}
                </span>
              </label>
              <label className="block space-y-1.5">
                <span className="text-xs font-medium">Hauptbegriff</span>
                <input
                  value={entwurf.keyword}
                  onChange={(e) => setze("keyword", e.target.value)}
                  maxLength={120}
                  placeholder="Wonach jemand sucht, der diesen Beitrag finden soll"
                  className={FELD}
                />
                <span className="text-[10px] text-muted-foreground/60">
                  Gehört in Titel, Beschreibung, die ersten Absätze und eine Zwischenüberschrift.
                </span>
              </label>
            </div>

            <label className="block space-y-1.5">
              <span className="text-xs font-medium">Beschreibung</span>
              <textarea
                value={entwurf.description}
                onChange={(e) => setze("description", e.target.value)}
                rows={2}
                maxLength={320}
                placeholder="Der Text unter dem Suchtreffer"
                className={cn(FELD, "leading-relaxed")}
              />
              <Zaehler wert={entwurf.description.length} von={120} bis={160} />
            </label>

            <label className="block space-y-1.5">
              <span className="text-xs font-medium">Text (Markdown)</span>
              <textarea
                ref={textfeld}
                value={entwurf.body_markdown}
                onChange={(e) => setze("body_markdown", e.target.value)}
                rows={26}
                spellCheck
                placeholder={
                  "Die Antwort steht im ersten Absatz.\n\n## Zwischenüberschrift\n\nKurze Absätze, Listen, Tabellen.\n" +
                  "Verweise auf eigene Seiten: [Text](/blog/andere-adresse)"
                }
                className={cn(FELD, "p-4 font-mono text-[12px] leading-relaxed")}
              />
              <span className="text-[10px] text-muted-foreground/60">
                Markdown ohne HTML. Keine Überschrift erster Ordnung — die gehört dem Titel.
              </span>
            </label>

            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block space-y-1.5">
                <span className="text-xs font-medium">Themen</span>
                <input
                  value={entwurf.tags}
                  onChange={(e) => setze("tags", e.target.value)}
                  placeholder="Betrieb, Sicherheit, Kosten"
                  className={FELD}
                />
                <span className="text-[10px] text-muted-foreground/60">Mit Komma getrennt, höchstens 8.</span>
              </label>
              <label className="block space-y-1.5">
                <span className="text-xs font-medium">Name unter dem Beitrag</span>
                <input
                  value={entwurf.author}
                  onChange={(e) => setze("author", e.target.value)}
                  maxLength={120}
                  placeholder="Leer: der Standard der Installation"
                  className={FELD}
                />
              </label>
            </div>

            <div className="space-y-2">
              <span className="text-xs font-medium">Bilder</span>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  value={bildAlt}
                  onChange={(e) => setBildAlt(e.target.value)}
                  maxLength={300}
                  placeholder="Was auf dem Bild zu sehen ist"
                  className={cn(FELD, "min-w-[220px] flex-1")}
                />
                <input
                  ref={dateiwahl}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(e) => bildHochladen(e.target.files?.[0])}
                />
                <button
                  onClick={() => dateiwahl.current?.click()}
                  disabled={laedtHoch}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-foreground/[0.08] px-3 py-2 text-xs font-medium transition-colors hover:bg-foreground/[0.04] disabled:opacity-40"
                >
                  {laedtHoch ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ImagePlus className="h-3.5 w-3.5" />}
                  Bild hochladen
                </button>
              </div>
              <span className="block text-[10px] text-muted-foreground/60">
                PNG, JPEG oder WebP bis 1,5 MB. Für das Titelbild passt das Querformat 1200 × 630 am besten.
              </span>
              {bilder.length > 0 && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {bilder.map((b) => (
                    <div
                      key={b.name}
                      className={cn(
                        "flex items-center gap-3 rounded-lg border bg-card/60 p-2",
                        entwurf.cover === b.name ? "border-primary/50" : "border-foreground/[0.06]",
                      )}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={b.adresse} alt="" className="h-12 w-20 shrink-0 rounded object-cover" />
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-[11px]">{b.name}</p>
                        <p className="text-[10px] text-muted-foreground/60">
                          {b.breite} × {b.hoehe} · {b.kb} kB
                        </p>
                        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px]">
                          <button onClick={() => bildEinfuegen(b)} className="text-primary hover:underline">
                            In den Text
                          </button>
                          <button
                            onClick={() => setze("cover", entwurf.cover === b.name ? "" : b.name)}
                            className="text-primary hover:underline"
                          >
                            {entwurf.cover === b.name ? "Titelbild entfernen" : "Als Titelbild"}
                          </button>
                        </div>
                      </div>
                      <button
                        onClick={() => bildLoeschen(b)}
                        title="Bild löschen"
                        className="shrink-0 rounded-lg p-1.5 text-muted-foreground/40 transition-colors hover:text-red-500"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium">Häufige Fragen</span>
                <button
                  onClick={() => setze("faq", [...entwurf.faq, { frage: "", antwort: "" }])}
                  disabled={entwurf.faq.length >= 12}
                  className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-40"
                >
                  <Plus className="h-3 w-3" />
                  Frage
                </button>
              </div>
              {entwurf.faq.length === 0 && (
                <p className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-4 py-4 text-center text-[11px] text-muted-foreground/60">
                  Drei bis fünf Nebenfragen mit kurzer Antwort erscheinen als eigener Abschnitt am Ende.
                </p>
              )}
              {entwurf.faq.map((f, i) => (
                <div key={i} className="flex items-start gap-2 rounded-lg border border-foreground/[0.06] bg-card/60 p-3">
                  <div className="min-w-0 flex-1 space-y-2">
                    <input
                      value={f.frage}
                      onChange={(e) => setzeFaq(i, "frage", e.target.value)}
                      maxLength={200}
                      placeholder="Frage"
                      className={FELD}
                    />
                    <textarea
                      value={f.antwort}
                      onChange={(e) => setzeFaq(i, "antwort", e.target.value)}
                      rows={2}
                      maxLength={1200}
                      placeholder="Antwort"
                      className={cn(FELD, "leading-relaxed")}
                    />
                  </div>
                  <button
                    onClick={() => setze("faq", entwurf.faq.filter((_, n) => n !== i))}
                    title="Frage entfernen"
                    className="shrink-0 rounded-lg p-1.5 text-muted-foreground/40 transition-colors hover:text-red-500"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <aside className="space-y-3">
            <h3 className="text-xs font-semibold">Prüfung</h3>
            {offen ? (
              <>
                <Pruefung p={offen.pruefung} />
                {ungespeichert && (
                  <p className="text-[10px] text-muted-foreground/60">Stand der letzten Speicherung. Speichern prüft neu.</p>
                )}
              </>
            ) : (
              <p className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3 text-[11px] text-muted-foreground/70">
                Nach dem ersten Speichern steht hier, was dem Beitrag noch fehlt.
              </p>
            )}
            {offen?.veroeffentlicht_am && (
              <p className="text-[10px] text-muted-foreground/60">Veröffentlicht am {datum(offen.veroeffentlicht_am)}</p>
            )}
          </aside>
        </div>
      </div>
    );
  }

  // ── Liste ──
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold">Blog der Landingpage</h2>
          <p className="mt-1 text-xs text-muted-foreground/70">
            Beiträge schreiben, prüfen und veröffentlichen. Öffentlich unter{" "}
            {status.blog_url ? (
              <a href={status.blog_url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:text-foreground">
                {status.blog_url.replace(/^https?:\/\//, "")}
              </a>
            ) : (
              "/blog"
            )}
            .
          </p>
        </div>
        <button
          onClick={neuerBeitrag}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90"
        >
          <Plus className="h-3.5 w-3.5" />
          Neuer Beitrag
        </button>
      </div>

      {posts.length === 0 ? (
        <p className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] px-4 py-8 text-center text-xs text-muted-foreground/60">
          Noch kein Beitrag. Ein guter Beitrag beantwortet genau eine Frage, die deine Kunden wirklich stellen.
        </p>
      ) : (
        <div className="space-y-2">
          {posts.map((p) => (
            <button
              key={p.slug}
              onClick={() => oeffnen(p.slug)}
              disabled={arbeitet !== null}
              className="flex w-full items-center gap-3 rounded-lg border border-foreground/[0.06] bg-card/60 px-4 py-3 text-left transition-colors hover:border-primary/40 disabled:opacity-60"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-medium">{p.titel}</p>
                <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground/60">
                  /blog/{p.slug} · {p.worte} Wörter
                  {p.veroeffentlicht_am ? ` · ${datum(p.veroeffentlicht_am)}` : ""}
                </p>
              </div>
              {arbeitet === `oeffnen:${p.slug}` && <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />}
              <StatusMarke status={p.status} />
            </button>
          ))}
        </div>
      )}

      <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-4 text-[11px] leading-relaxed text-muted-foreground/80">
        <p className="font-medium text-foreground">Schreiben lassen</p>
        <p className="mt-1">
          {status.mcp_ready ? (
            <>
              Der MCP-Dienst des Blogs ist bereit: <code className="font-mono">{status.mcp_url}</code>. Wer ihn mit dem
              Schlüssel aus der <code className="font-mono">.env</code> einbindet — ein Agent dieser Plattform über
              Integrationen oder ein eigener MCP-Client —, kann Beiträge entwerfen, prüfen und veröffentlichen.
            </>
          ) : (
            <>
              Der MCP-Dienst des Blogs ist nicht bereit: In der <code className="font-mono">.env</code> fehlt ein
              Schlüssel mit mindestens 32 Zeichen (<code className="font-mono">BLOG_MCP_TOKEN</code>).
            </>
          )}
        </p>
      </div>
    </div>
  );
}
