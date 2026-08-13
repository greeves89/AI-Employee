"use client";

/** Verwaltung eigener Menuepunkte: fremde Seiten einbetten oder verlinken.
 *
 *  Wer eine Seite sehen darf, wird NICHT hier entschieden, sondern in den Rollen
 *  unter „Menüpfade" — der angelegte Punkt taucht dort als ``/p/<kurzname>`` auf.
 *  Damit gibt es weiterhin genau eine Stelle fuer Menue-Rechte statt zweier, die
 *  sich widersprechen koennten.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ExternalLink,
  Eye,
  EyeOff,
  Frame,
  Link2,
  Loader2,
  Plus,
  Save,
  Trash2,
  X,
} from "lucide-react";
import * as api from "@/lib/api";
import type { CustomPage, CustomPageInput, CustomPageOpenMode } from "@/lib/api";
import { PAGE_GROUPS, PAGE_ICON_NAMES, pageIcon } from "@/lib/page-icons";
import { cn } from "@/lib/utils";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

const EMPTY_DRAFT: CustomPageInput = {
  slug: "",
  title: "",
  url: "",
  description: "",
  icon: "Globe",
  group_key: "collab",
  open_mode: "iframe",
  sort_order: 0,
  enabled: true,
  allow_media: false,
};

/** Aus dem Titel einen brauchbaren Kurznamen vorschlagen — solange niemand von
 *  Hand eingegriffen hat. Umlaute bewusst ausgeschrieben statt weggeworfen. */
function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63);
}

export function PagesPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const [pages, setPages] = useState<CustomPage[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<CustomPageInput>(EMPTY_DRAFT);
  const [slugTouched, setSlugTouched] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.listCustomPages();
      setPages(r.pages);
    } catch {
      toast.error("Seiten konnten nicht geladen werden");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const startNew = () => {
    setDraft(EMPTY_DRAFT);
    setSlugTouched(false);
    setEditingId("new");
  };

  const startEdit = (page: CustomPage) => {
    setDraft({
      slug: page.slug,
      title: page.title,
      url: page.url,
      description: page.description ?? "",
      icon: page.icon,
      group_key: page.group_key,
      open_mode: page.open_mode,
      sort_order: page.sort_order,
      enabled: page.enabled,
      allow_media: page.allow_media,
    });
    setSlugTouched(true);
    setEditingId(page.id);
  };

  const cancel = () => {
    setEditingId(null);
    setDraft(EMPTY_DRAFT);
  };

  const save = async () => {
    setSaving(true);
    try {
      if (editingId === "new") {
        await api.createCustomPage(draft);
        toast.success("Seite angelegt", `Jetzt in den Rollen unter „Menüpfade" freischalten: /p/${draft.slug}`);
      } else if (typeof editingId === "number") {
        await api.updateCustomPage(editingId, draft);
        toast.success("Seite gespeichert");
      }
      cancel();
      await load();
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (page: CustomPage) => {
    try {
      await api.updateCustomPage(page.id, { enabled: !page.enabled });
      await load();
    } catch (e) {
      toast.error("Umschalten fehlgeschlagen", e instanceof Error ? e.message : undefined);
    }
  };

  const remove = async (page: CustomPage) => {
    const ok = await confirm({
      title: `„${page.title}" löschen?`,
      message: "Der Menüpunkt verschwindet für alle. Rollen, die ihn freigeschaltet hatten, behalten den Eintrag bis zum nächsten Speichern — er zeigt dann ins Leere.",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await api.deleteCustomPage(page.id);
      toast.success("Seite gelöscht");
      await load();
    } catch (e) {
      toast.error("Löschen fehlgeschlagen", e instanceof Error ? e.message : undefined);
    }
  };

  const formValid = draft.title.trim() !== "" && draft.slug.trim() !== "" && /^https?:\/\//i.test(draft.url.trim());

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">Seiten & Links im Menü</p>
          <p className="mt-1 max-w-2xl text-[12px] text-muted-foreground">
            Fremde Oberflächen — etwa OpenWebUI — als eigenen Menüpunkt einbinden: eingebettet
            im Rahmen oder als Link in einem neuen Tab. Sichtbar wird ein Punkt erst, wenn er
            in einer Rolle unter „Menüpfade" freigeschaltet ist (Administratoren sehen alles).
          </p>
        </div>
        <button
          onClick={startNew}
          className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-primary px-3 py-2 text-[13px] font-medium text-primary-foreground transition-opacity hover:opacity-90"
        >
          <Plus className="h-4 w-4" />
          Neue Seite
        </button>
      </div>

      {editingId !== null && (
        <div className="rounded-2xl border border-primary/20 bg-card p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-[13px] font-medium">
              {editingId === "new" ? "Neue Seite anlegen" : "Seite bearbeiten"}
            </p>
            <button
              onClick={cancel}
              className="flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Titel (im Menü)">
              <input
                value={draft.title}
                onChange={(e) => {
                  const title = e.target.value;
                  setDraft((d) => ({
                    ...d,
                    title,
                    slug: slugTouched ? d.slug : slugify(title),
                  }));
                }}
                placeholder="z.B. OpenWebUI"
                className={inputClass}
              />
            </Field>

            <Field label="Kurzname (ergibt /p/<kurzname>)">
              <input
                value={draft.slug}
                onChange={(e) => {
                  setSlugTouched(true);
                  setDraft((d) => ({ ...d, slug: e.target.value }));
                }}
                placeholder="openwebui"
                className={cn(inputClass, "font-mono")}
              />
            </Field>

            <Field label="Adresse (http:// oder https://)" className="sm:col-span-2">
              <input
                value={draft.url}
                onChange={(e) => setDraft((d) => ({ ...d, url: e.target.value }))}
                placeholder="https://chat.kunde.de"
                className={cn(inputClass, "font-mono")}
              />
            </Field>

            <Field label="Beschreibung (optional)" className="sm:col-span-2">
              <input
                value={draft.description ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
                placeholder="Kurzer Hinweis, was den Nutzer dort erwartet"
                className={inputClass}
              />
            </Field>

            <Field label="Öffnen als">
              <div className="flex gap-2">
                {([
                  { value: "iframe" as CustomPageOpenMode, label: "Eingebettet", Icon: Frame },
                  { value: "link" as CustomPageOpenMode, label: "Neuer Tab", Icon: Link2 },
                ]).map(({ value, label, Icon }) => (
                  <button
                    key={value}
                    onClick={() => setDraft((d) => ({ ...d, open_mode: value }))}
                    className={cn(
                      "inline-flex flex-1 items-center justify-center gap-2 rounded-lg border px-3 py-2 text-[12px] font-medium transition-colors",
                      draft.open_mode === value
                        ? "border-primary/40 bg-primary/10 text-foreground"
                        : "border-foreground/[0.08] text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {label}
                  </button>
                ))}
              </div>
            </Field>

            <Field label="Menügruppe">
              <select
                value={draft.group_key}
                onChange={(e) => setDraft((d) => ({ ...d, group_key: e.target.value }))}
                className={inputClass}
              >
                {PAGE_GROUPS.map((g) => (
                  <option key={g.key} value={g.key}>{g.label}</option>
                ))}
              </select>
            </Field>

            <Field label="Symbol">
              <select
                value={draft.icon}
                onChange={(e) => setDraft((d) => ({ ...d, icon: e.target.value }))}
                className={inputClass}
              >
                {PAGE_ICON_NAMES.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </Field>

            <Field label="Reihenfolge (kleiner = weiter oben)">
              <input
                type="number"
                value={draft.sort_order ?? 0}
                onChange={(e) => setDraft((d) => ({ ...d, sort_order: Number(e.target.value) || 0 }))}
                className={inputClass}
              />
            </Field>
          </div>

          <div className="mt-3 flex flex-wrap gap-4">
            <Checkbox
              checked={draft.enabled ?? true}
              onChange={(v) => setDraft((d) => ({ ...d, enabled: v }))}
              label="Aktiv"
            />
            <Checkbox
              checked={draft.allow_media ?? false}
              onChange={(v) => setDraft((d) => ({ ...d, allow_media: v }))}
              label="Mikrofon & Kamera an die eingebettete Seite durchreichen"
            />
          </div>

          {draft.open_mode === "iframe" && (
            <p className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/[0.07] px-3 py-2 text-[11px] text-muted-foreground">
              Ob sich eine Seite einbetten lässt, entscheidet sie selbst. Sendet sie
              <span className="mx-1 font-mono">X-Frame-Options: DENY</span> oder ein
              <span className="mx-1 font-mono">frame-ancestors</span>-CSP ohne unsere Domain,
              bleibt der Rahmen leer — dann muss die Gegenseite uns freigeben, oder du nimmst
              „Neuer Tab". Gleiches gilt für die Anmeldung im Rahmen: die Sitzungs-Cookies der
              Zielseite brauchen <span className="font-mono">SameSite=None; Secure</span>.
            </p>
          )}

          <div className="mt-4 flex items-center gap-2">
            <button
              onClick={save}
              disabled={!formValid || saving}
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Speichern
            </button>
            <button
              onClick={cancel}
              className="rounded-xl border border-foreground/[0.08] px-4 py-2 text-[13px] text-muted-foreground hover:text-foreground"
            >
              Abbrechen
            </button>
            {!formValid && (
              <span className="text-[11px] text-muted-foreground/60">
                Titel, Kurzname und eine http(s)-Adresse werden gebraucht.
              </span>
            )}
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : pages.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-foreground/[0.08] py-12 text-center text-[13px] text-muted-foreground">
          Noch keine eigenen Menüpunkte angelegt.
        </div>
      ) : (
        <div className="space-y-2">
          {pages.map((page) => {
            const Icon = pageIcon(page.icon);
            const group = PAGE_GROUPS.find((g) => g.key === page.group_key);
            return (
              <div
                key={page.id}
                className={cn(
                  "flex flex-wrap items-center gap-3 rounded-xl border border-foreground/[0.06] bg-card px-4 py-3",
                  !page.enabled && "opacity-50"
                )}
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
                  <Icon className="h-4 w-4 text-primary" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] font-medium">{page.title}</span>
                    <span className="rounded-full bg-foreground/[0.06] px-2 py-0.5 text-[10px] font-mono text-muted-foreground">
                      {page.menu_path}
                    </span>
                    <span className="text-[10px] uppercase tracking-wide text-muted-foreground/60">
                      {group?.label ?? page.group_key} · {page.open_mode === "iframe" ? "eingebettet" : "neuer Tab"}
                    </span>
                  </div>
                  <p className="mt-0.5 truncate text-[11px] font-mono text-muted-foreground/70">{page.url}</p>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <a
                    href={page.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Adresse im neuen Tab prüfen"
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground"
                  >
                    <ExternalLink className="h-4 w-4" />
                  </a>
                  <button
                    onClick={() => toggleEnabled(page)}
                    title={page.enabled ? "Abschalten" : "Aktivieren"}
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground"
                  >
                    {page.enabled ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
                  </button>
                  <button
                    onClick={() => startEdit(page)}
                    className="rounded-lg border border-foreground/[0.08] px-3 py-1.5 text-[12px] text-muted-foreground hover:text-foreground"
                  >
                    Bearbeiten
                  </button>
                  <button
                    onClick={() => remove(page)}
                    title="Löschen"
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:text-red-400"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const inputClass =
  "w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50";

function Field({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={className}>
      <label className="mb-1 block text-[11px] font-medium text-muted-foreground">{label}</label>
      {children}
    </div>
  );
}

function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-[12px] text-muted-foreground">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 rounded border-foreground/20 bg-background accent-primary"
      />
      {label}
    </label>
  );
}
