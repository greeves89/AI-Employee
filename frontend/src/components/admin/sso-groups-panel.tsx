"use client";

/** IdP-Gruppen auf Rollen abbilden (Entra/Azure AD, SAML).
 *
 * Loest die fruehere freie JSON-Textbox ab (`{"IT-Admins": "admin"}`, blind
 * getippt, kein Bezug zu echten Gruppennamen). State-of-the-art-Referenz war
 * OpenWebUIs Gruppen-Sync: Gruppen kommen bei jedem Login mit, die Verwaltung
 * zeigt sie an statt sie zu erraten.
 *
 * Zwei Bloecke pro Anbieter:
 * - Beobachtete Gruppen: tatsaechlich beim Login gesehene Namen, anklickbar statt
 *   abtippbar. Ein unbeobachteter Name laesst sich trotzdem manuell anlegen — sonst
 *   liesse sich nichts vorbereiten, bevor die erste Person aus einer Gruppe sich
 *   ueberhaupt anmeldet.
 * - Zuordnungen: was tatsaechlich gilt, inkl. Ziel (feste Rolle ODER CustomRole)
 *   und Prioritaet (entscheidet, wenn eine Person in mehreren zugeordneten Gruppen
 *   steht).
 */

import { useCallback, useEffect, useState } from "react";
import {
  Check,
  ChevronRight,
  Loader2,
  Plus,
  Save,
  Shield,
  Trash2,
  Users,
  X,
} from "lucide-react";
import * as api from "@/lib/api";
import type { SsoGroupRoleMapping, SsoObservedGroup, SsoProvider, SsoTargetKind } from "@/lib/api";
import type { CustomRole } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useConfirm, useToast } from "@/components/ui/dialog-provider";

const PROVIDER_TABS: { id: SsoProvider; label: string; hint: string }[] = [
  { id: "microsoft", label: "Microsoft / Entra ID", hint: "Normaler Microsoft-SSO-Login (OIDC)" },
  { id: "saml", label: "SAML 2.0", hint: "ADFS, Keycloak, Entra ID via SAML" },
];

const ROLE_LABELS: Record<string, string> = {
  admin: "Administrator", manager: "Manager", member: "Mitglied", viewer: "Betrachter",
};

interface TargetDraft {
  kind: SsoTargetKind;
  role: string;
  customRoleId: string;
  priority: string;
}

const EMPTY_TARGET: TargetDraft = { kind: "role", role: "member", customRoleId: "", priority: "0" };

export function SsoGroupsPanel() {
  const toast = useToast();
  const confirm = useConfirm();
  const [provider, setProvider] = useState<SsoProvider>("microsoft");
  const [mappings, setMappings] = useState<SsoGroupRoleMapping[]>([]);
  const [observed, setObserved] = useState<SsoObservedGroup[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [customRoles, setCustomRoles] = useState<CustomRole[]>([]);
  const [loading, setLoading] = useState(true);

  // Welche Gruppe gerade bearbeitet wird — entweder eine bestehende Zuordnung
  // (edit) oder eine beobachtete/neue Gruppe (assign), nie beides gleichzeitig.
  const [editingId, setEditingId] = useState<number | null>(null);
  const [assigningGroup, setAssigningGroup] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [manualGroupName, setManualGroupName] = useState("");
  const [draft, setDraft] = useState<TargetDraft>(EMPTY_TARGET);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [m, o, r] = await Promise.all([
        api.listSsoGroupMappings(provider),
        api.listSsoObservedGroups(provider),
        api.listRoles().catch(() => ({ roles: [] as CustomRole[] })),
      ]);
      setMappings(m.mappings);
      setRoles(m.roles);
      setObserved(o.groups);
      setCustomRoles(r.roles);
    } catch {
      toast.error("SSO-Gruppen konnten nicht geladen werden");
    } finally {
      setLoading(false);
    }
  }, [provider, toast]);

  useEffect(() => {
    load();
  }, [load]);

  const closeEditors = () => {
    setEditingId(null);
    setAssigningGroup(null);
    setManualOpen(false);
    setManualGroupName("");
    setDraft(EMPTY_TARGET);
  };

  const startAssign = (groupName: string) => {
    closeEditors();
    setAssigningGroup(groupName);
  };

  const startEdit = (m: SsoGroupRoleMapping) => {
    closeEditors();
    setEditingId(m.id);
    setDraft({
      kind: m.target_kind,
      role: m.target_kind === "role" ? m.target_value : "member",
      customRoleId: m.target_kind === "custom_role" ? m.target_value : "",
      priority: String(m.priority),
    });
  };

  const targetValue = () => (draft.kind === "role" ? draft.role : draft.customRoleId);
  const targetValid = draft.kind === "role" ? Boolean(draft.role) : Boolean(draft.customRoleId);

  const saveAssign = async (groupName: string) => {
    if (!targetValid) return;
    setSaving(true);
    try {
      await api.createSsoGroupMapping({
        provider, group_name: groupName, target_kind: draft.kind,
        target_value: targetValue(), priority: Number(draft.priority) || 0,
      });
      toast.success("Zuordnung angelegt");
      closeEditors();
      await load();
    } catch (e) {
      toast.error("Anlegen fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  const saveEdit = async (id: number) => {
    if (!targetValid) return;
    setSaving(true);
    try {
      await api.updateSsoGroupMapping(id, {
        target_kind: draft.kind, target_value: targetValue(),
        priority: Number(draft.priority) || 0,
      });
      toast.success("Zuordnung gespeichert");
      closeEditors();
      await load();
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", e instanceof Error ? e.message : undefined);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (m: SsoGroupRoleMapping) => {
    const ok = await confirm({
      title: `Zuordnung „${m.group_name}" löschen?`,
      message: "Mitglieder dieser IdP-Gruppe bekommen die Rolle beim nächsten Login nicht mehr automatisch zugewiesen. Bereits vergebene Rollen bleiben unverändert.",
      variant: "destructive",
    });
    if (!ok) return;
    try {
      await api.deleteSsoGroupMapping(m.id);
      toast.success("Zuordnung gelöscht");
      await load();
    } catch (e) {
      toast.error("Löschen fehlgeschlagen", e instanceof Error ? e.message : undefined);
    }
  };

  const mappedByName = new Map(mappings.map((m) => [m.group_name.toLowerCase(), m]));
  const unmappedObserved = observed.filter((g) => !g.mapped);
  const targetLabel = (m: SsoGroupRoleMapping) =>
    m.target_kind === "role" ? (ROLE_LABELS[m.target_value] ?? m.target_value) : (m.custom_role_name ?? `Rolle #${m.target_value}`);

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-medium">SSO-Gruppen → Rollen</p>
        <p className="mt-1 max-w-2xl text-[12px] text-muted-foreground">
          Meldet sich jemand über SSO an, ordnen wir seine Gruppen beim Anbieter (Entra
          ID, ADFS, ...) automatisch einer Rolle hier zu — bei jedem Login neu, nicht
          nur beim ersten. Ohne Treffer bleibt die Rolle unverändert; niemandem werden
          von Hand vergebene Rechte weggenommen.
        </p>
      </div>

      <div className="flex gap-1 rounded-xl border border-foreground/[0.06] bg-foreground/[0.02] p-1">
        {PROVIDER_TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => { setProvider(t.id); closeEditors(); }}
            title={t.hint}
            className={cn(
              "flex-1 rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors",
              provider === t.id ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <>
          {/* Beobachtete, noch unzugeordnete Gruppen — der eigentliche Grund, warum
              das hier besser ist als eine JSON-Textbox: echte Namen zum Anklicken. */}
          {unmappedObserved.length > 0 && (
            <div className="rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-4">
              <div className="mb-2 flex items-center gap-2 text-[12px] font-medium text-amber-500">
                <Users className="h-3.5 w-3.5" />
                {unmappedObserved.length} beobachtete Gruppe{unmappedObserved.length === 1 ? "" : "n"} ohne Zuordnung
              </div>
              <div className="space-y-1.5">
                {unmappedObserved.map((g) => (
                  <div key={g.group_name} className="flex items-center gap-2 rounded-lg bg-card/60 px-3 py-2">
                    <span className="min-w-0 flex-1 truncate text-[12px] font-medium">{g.group_name}</span>
                    <span className="shrink-0 text-[10px] text-muted-foreground/60">
                      zuletzt gesehen: {g.last_seen_at ? new Date(g.last_seen_at).toLocaleDateString("de-DE") : "–"}
                    </span>
                    <button
                      onClick={() => startAssign(g.group_name)}
                      className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-foreground/[0.08] px-2.5 py-1 text-[11px] font-medium text-muted-foreground hover:text-foreground"
                    >
                      Rolle zuweisen <ChevronRight className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
              {assigningGroup && unmappedObserved.some((g) => g.group_name === assigningGroup) && (
                <TargetEditor
                  draft={draft} setDraft={setDraft} roles={roles} customRoles={customRoles}
                  saving={saving} valid={targetValid}
                  onSave={() => saveAssign(assigningGroup)} onCancel={closeEditors}
                  className="mt-3"
                />
              )}
            </div>
          )}

          {/* Bestehende Zuordnungen */}
          <div className="rounded-xl border border-foreground/[0.06] bg-card/80">
            <div className="flex items-center justify-between border-b border-foreground/[0.04] px-4 py-2.5">
              <span className="text-[12px] font-medium text-muted-foreground">
                {mappings.length} Zuordnung{mappings.length === 1 ? "" : "en"}
              </span>
              <button
                onClick={() => { closeEditors(); setManualOpen(true); }}
                className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-[11px] font-medium text-primary hover:bg-primary/10"
              >
                <Plus className="h-3.5 w-3.5" /> Manuell hinzufügen
              </button>
            </div>

            {manualOpen && (
              <div className="border-b border-foreground/[0.04] p-4">
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
                  Gruppenname (genau wie beim Anbieter, z. B. der Entra-Anzeigename)
                </label>
                <input
                  autoFocus
                  value={manualGroupName}
                  onChange={(e) => setManualGroupName(e.target.value)}
                  placeholder="z. B. Vertrieb"
                  className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
                />
                <TargetEditor
                  draft={draft} setDraft={setDraft} roles={roles} customRoles={customRoles}
                  saving={saving} valid={targetValid && manualGroupName.trim() !== ""}
                  onSave={() => saveAssign(manualGroupName.trim())} onCancel={closeEditors}
                  className="mt-3"
                />
              </div>
            )}

            {mappings.length === 0 && !manualOpen ? (
              <div className="px-4 py-8 text-center text-[12px] text-muted-foreground">
                Noch keine Zuordnung für {PROVIDER_TABS.find((t) => t.id === provider)?.label}.
              </div>
            ) : (
              <div className="divide-y divide-foreground/[0.04]">
                {mappings.map((m) => (
                  <div key={m.id}>
                    <div className="flex items-center gap-3 px-4 py-2.5">
                      <Users className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" />
                      <span className="min-w-0 flex-1 truncate text-[12px] font-medium">{m.group_name}</span>
                      <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/40" />
                      <span className={cn(
                        "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium",
                        m.target_kind === "role" ? "bg-primary/10 text-primary" : "bg-violet-500/10 text-violet-400"
                      )}>
                        {m.target_kind === "custom_role" && <Shield className="h-2.5 w-2.5" />}
                        {targetLabel(m)}
                      </span>
                      {m.priority !== 0 && (
                        <span title="Priorität — höher gewinnt bei mehreren zutreffenden Gruppen"
                              className="shrink-0 rounded bg-foreground/[0.06] px-1.5 py-0.5 text-[10px] text-muted-foreground">
                          P{m.priority}
                        </span>
                      )}
                      <button onClick={() => startEdit(m)}
                              className="shrink-0 rounded-lg border border-foreground/[0.08] px-2 py-1 text-[11px] text-muted-foreground hover:text-foreground">
                        Bearbeiten
                      </button>
                      <button onClick={() => remove(m)} title="Löschen"
                              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:text-red-400">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                    {editingId === m.id && (
                      <TargetEditor
                        draft={draft} setDraft={setDraft} roles={roles} customRoles={customRoles}
                        saving={saving} valid={targetValid}
                        onSave={() => saveEdit(m.id)} onCancel={closeEditors}
                        className="px-4 pb-4"
                      />
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Beobachtete UND bereits zugeordnete Gruppen — Transparenz, was gerade
              wirklich hereinkommt, auch ohne dass etwas zu tun waere. */}
          {observed.some((g) => g.mapped) && (
            <details className="text-[11px] text-muted-foreground/60">
              <summary className="cursor-pointer select-none">
                Bereits zugeordnete, beobachtete Gruppen ({observed.filter((g) => g.mapped).length})
              </summary>
              <ul className="mt-1.5 space-y-1 pl-4">
                {observed.filter((g) => g.mapped).map((g) => (
                  <li key={g.group_name} className="flex items-center gap-1.5">
                    <Check className="h-3 w-3 text-emerald-500" /> {g.group_name}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function TargetEditor({
  draft, setDraft, roles, customRoles, saving, valid, onSave, onCancel, className,
}: {
  draft: TargetDraft;
  setDraft: (updater: (d: TargetDraft) => TargetDraft) => void;
  roles: string[];
  customRoles: CustomRole[];
  saving: boolean;
  valid: boolean;
  onSave: () => void;
  onCancel: () => void;
  className?: string;
}) {
  return (
    <div className={cn("rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] p-3", className)}>
      <div className="flex gap-2">
        {([
          { kind: "role" as SsoTargetKind, label: "Feste Rolle" },
          { kind: "custom_role" as SsoTargetKind, label: "Eigene Rolle" },
        ]).map(({ kind, label }) => (
          <button
            key={kind}
            onClick={() => setDraft((d) => ({ ...d, kind }))}
            className={cn(
              "flex-1 rounded-lg border px-3 py-1.5 text-[11px] font-medium transition-colors",
              draft.kind === kind ? "border-primary/40 bg-primary/10 text-foreground" : "border-foreground/[0.08] text-muted-foreground hover:text-foreground"
            )}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_auto]">
        {draft.kind === "role" ? (
          <select
            value={draft.role}
            onChange={(e) => setDraft((d) => ({ ...d, role: e.target.value }))}
            className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
          >
            {roles.map((r) => (
              <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
            ))}
          </select>
        ) : (
          <select
            value={draft.customRoleId}
            onChange={(e) => setDraft((d) => ({ ...d, customRoleId: e.target.value }))}
            className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
          >
            <option value="">Rolle wählen…</option>
            {customRoles.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        )}
        <input
          type="number"
          title="Priorität — bei mehreren zutreffenden Gruppen gewinnt die höhere Zahl"
          value={draft.priority}
          onChange={(e) => setDraft((d) => ({ ...d, priority: e.target.value }))}
          placeholder="Priorität"
          className="w-24 rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
        />
      </div>

      {draft.kind === "custom_role" && customRoles.length === 0 && (
        <p className="mt-1.5 text-[10px] text-muted-foreground/60">
          Noch keine eigenen Rollen angelegt — siehe Reiter „Rollen".
        </p>
      )}

      <div className="mt-2.5 flex items-center gap-2">
        <button
          onClick={onSave}
          disabled={!valid || saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[11px] font-medium text-primary-foreground disabled:opacity-40"
        >
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
          Speichern
        </button>
        <button onClick={onCancel} className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-[11px] text-muted-foreground hover:text-foreground">
          <X className="h-3 w-3" /> Abbrechen
        </button>
      </div>
    </div>
  );
}
