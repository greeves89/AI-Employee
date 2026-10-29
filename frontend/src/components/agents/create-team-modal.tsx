"use client";

import { useState, useEffect } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, AnimatePresence } from "framer-motion";
import { X, Plus, Loader2, Check, Crown, Users } from "lucide-react";
import * as api from "@/lib/api";
import type { Agent } from "@/lib/types";
import type { Team } from "@/lib/api";
import { cn } from "@/lib/utils";
import { AgentAvatar } from "@/components/agents/agent-avatar";
import { useToast } from "@/components/ui/dialog-provider";
import { PROJEKT_FARBEN, TAKTE, istGithubRepo } from "@/lib/projekte";
import { useAuthStore } from "@/lib/auth";

interface CreateTeamModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
  agents: Agent[];
  /** When provided, the modal edits this team instead of creating a new one. */
  team?: Team | null;
  /** Als Projekt (Seite /projekte): andere Worte und eine Kennfarbe — dasselbe Team darunter. */
  projekt?: boolean;
}

export function CreateTeamModal({
  open,
  onOpenChange,
  onSaved,
  agents,
  team,
  projekt = false,
}: CreateTeamModalProps) {
  const toast = useToast();
  const isEdit = Boolean(team);
  const wort = projekt ? "Projekt" : "Team";

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [leadAgentId, setLeadAgentId] = useState<string>("");
  const [farbe, setFarbe] = useState<string>("blau");
  const [takt, setTakt] = useState<number>(0);
  const [geteilt, setGeteilt] = useState<string[]>([]);
  const [repo, setRepo] = useState("");
  const [schluessel, setSchluessel] = useState<number>(0);
  // Zusammenarbeit am Repo: Dauer einer Übernahme, Obergrenze je Bearbeiter, Ablösen erlaubt?
  const [uebernahmeStunden, setUebernahmeStunden] = useState<number>(2);
  const [maxUebernahmen, setMaxUebernahmen] = useState<number>(2);
  const [abloesen, setAbloesen] = useState(false);
  const [rueckkanal, setRueckkanal] = useState<"an" | "aus" | "auto">("auto");
  const [schluesselListe, setSchluesselListe] = useState<{ id: number; name: string; key_name: string }[]>([]);
  const [brains, setBrains] = useState<string[]>([]);
  const [brainListe, setBrainListe] = useState<{ label: string; name: string; description: string | null }[]>([]);
  const istAdmin = useAuthStore((s) => s.user?.role) === "admin";
  const [nutzer, setNutzer] = useState<{ id: string; name: string; email?: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset / prefill on open
  useEffect(() => {
    if (open) {
      setName(team?.name ?? "");
      setDescription(team?.description ?? "");
      setMemberIds(team?.member_agent_ids ?? []);
      setLeadAgentId(team?.lead_agent_id ?? "");
      setFarbe(team?.farbe ?? "blau");
      setTakt(team?.takt_minuten ?? 0);
      setGeteilt(team?.geteilt_mit ?? []);
      setRepo(team?.repo ?? "");
      setSchluessel(team?.repo_secret_id ?? 0);
      setUebernahmeStunden(team?.uebernahme_stunden ?? 2);
      setMaxUebernahmen(team?.max_uebernahmen ?? 2);
      setAbloesen(!!team?.abgelaufene_uebernehmen);
      setRueckkanal(team?.rueckkanal ?? "auto");
      setBrains(team?.angebundene_brains ?? []);
      setError(null);
    }
  }, [open, team]);

  // Nutzerliste für die Freigabe — nur im Projekt-Fenster gebraucht.
  useEffect(() => {
    if (!open || !projekt) return;
    api.getProjektVerzeichnis().then((r) => setNutzer(r.users)).catch(() => setNutzer([]));
    api.listSecrets().then((s) => setSchluesselListe(s.filter((x) => x.is_active))).catch(() => setSchluesselListe([]));
    // Nur Brains, die man selbst nutzen darf (dieselbe Regel wie beim Server); das
    // eigene Projektwissen nicht — es ist ohnehin dabei.
    api.listSecondBrains(true)
      .then((l) => setBrainListe(l.filter((b) => !b.label.startsWith("brain-projekt-") || team?.angebundene_brains?.includes(b.label))
        .map((b) => ({ label: b.label, name: b.name, description: b.description }))))
      .catch(() => setBrainListe([]));
  }, [open, projekt, team]);

  const toggleMember = (id: string) => {
    setMemberIds((prev) => {
      if (prev.includes(id)) {
        // Removing the current lead clears the lead selection
        if (leadAgentId === id) setLeadAgentId("");
        return prev.filter((m) => m !== id);
      }
      return [...prev, id];
    });
  };

  const handleSave = async () => {
    if (!name.trim()) {
      setError("Name ist erforderlich");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const body = {
        name: name.trim(),
        description: description.trim() || undefined,
        member_agent_ids: memberIds,
        lead_agent_id: leadAgentId || null,
        ...(projekt ? { farbe, takt_minuten: takt, geteilt_mit: geteilt } : {}),
        ...(projekt ? { repo: repo.trim(), repo_secret_id: repo.trim() ? schluessel : 0 } : {}),
        ...(projekt && isEdit && repo.trim() ? {
          uebernahme_stunden: uebernahmeStunden, max_uebernahmen: maxUebernahmen, abgelaufene_uebernehmen: abloesen,
          rueckkanal,
        } : {}),
        // Nur bei Änderung mitschicken — sonst blockierte ein inzwischen entzogenes
        // Brain das Speichern jeder anderen Einstellung.
        ...(projekt && brains.join("\n") !== (team?.angebundene_brains ?? []).join("\n") ? { angebundene_brains: brains } : {}),
      };
      if (isEdit && team) {
        await api.updateTeam(team.id, body);
        toast.success(`${wort} aktualisiert`, name.trim());
      } else {
        await api.createTeam(body);
        toast.success(`${wort} erstellt`, name.trim());
      }
      onOpenChange(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : `${wort} konnte nicht gespeichert werden`);
    } finally {
      setSaving(false);
    }
  };

  const memberAgents = agents.filter((a) => memberIds.includes(a.id));

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild>
              <motion.div
                className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
              />
            </Dialog.Overlay>

            {/* Dialog.Content deckt den ganzen Viewport ab und faengt daher JEDEN
                Klick ab, auch den auf den "Hintergrund" — der Overlay-onClick oben
                erreicht deshalb nie den Nutzer. target===currentTarget unterscheidet
                Klick-auf-Hintergrund von Klick-auf-Karte (Bubbling). */}
            <Dialog.Content asChild>
              <motion.div
                className="fixed inset-0 z-50 flex items-center justify-center p-4"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
                onClick={(e) => { if (e.target === e.currentTarget) onOpenChange(false); }}
              >
                <motion.div
                  className="w-full max-w-2xl rounded-2xl border border-foreground/[0.08] bg-card shadow-2xl shadow-black/40 overflow-hidden max-h-[90vh] overflow-y-auto"
                  initial={{ scale: 0.95, y: 10 }}
                  animate={{ scale: 1, y: 0 }}
                  exit={{ scale: 0.95, y: 10 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                >
                  {/* Header */}
                  <div className="flex items-center justify-between border-b border-foreground/[0.06] px-6 py-4">
                    <Dialog.Title className="text-lg font-semibold">
                      {isEdit ? `${wort} bearbeiten` : `${wort} erstellen`}
                    </Dialog.Title>
                    <Dialog.Close className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors">
                      <X className="h-4 w-4" />
                    </Dialog.Close>
                  </div>

                  {/* Body */}
                  <div className="px-6 py-5 space-y-5">
                    {/* Name */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        Name <span className="text-red-400">*</span>
                      </label>
                      <input
                        type="text"
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        onKeyDown={(e) => e.key === "Enter" && !saving && handleSave()}
                        placeholder={projekt ? "z. B. Website-Relaunch, Messe 2027 …" : "z.B. Research-Team, Delivery-Squad..."}
                        autoFocus
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                      />
                    </div>

                    {/* Description */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        Beschreibung <span className="text-muted-foreground/40">(optional)</span>
                      </label>
                      <textarea
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        placeholder={projekt ? "Worum geht es, und woran merkt man, dass es fertig ist? Die Agenten lesen das bei jedem Auftrag." : "Wofür ist dieses Team zuständig?"}
                        rows={2}
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all resize-none"
                      />
                    </div>

                    {projekt && (
                      <div>
                        <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                          Git-Repo <span className="text-muted-foreground/40">(optional)</span>
                        </label>
                        <input
                          type="text"
                          value={repo}
                          onChange={(e) => setRepo(e.target.value)}
                          placeholder="GitHub: owner/name · Forgejo/Gitea: https://git.example.com/team/repo"
                          className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                        />
                        <p className="text-[11px] text-muted-foreground/50 mt-1">
                          GitHub als owner/name, ein eigener Forgejo- oder Gitea-Server mit voller Adresse. Offene
                          Issues kommen in den Eingang, Pull Requests mit CI-Stand unter „Wartet auf Merge“.
                        </p>
                        {repo.trim() && (
                          <div className="mt-2">
                            <label className="block text-xs font-medium text-muted-foreground mb-1.5">Schlüssel für das Repo</label>
                            <select
                              value={schluessel}
                              onChange={(e) => setSchluessel(Number(e.target.value))}
                              className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                            >
                              <option value={0}>
                                {istAdmin && istGithubRepo(repo)
                                  ? "Kein eigener — GitHub-Verbindung der Anlage bzw. öffentlich"
                                  : "Keiner — nur öffentliche Repos"}
                              </option>
                              {schluesselListe.map((k) => (
                                <option key={k.id} value={k.id}>{k.name} ({k.key_name})</option>
                              ))}
                            </select>
                            <p className="text-[11px] text-muted-foreground/50 mt-1">
                              Für private Repos: einen Zugangsschlüssel mit Leserecht aus der Schlüsselverwaltung
                              wählen, bei GitHub ein GitHub-Token, bei Forgejo/Gitea ein Token dieses Servers.
                              Öffentliche Repos gehen ohne (abgeglichen alle 30 Min.).
                            </p>
                          </div>
                        )}
                        {repo.trim() && isEdit && (
                          <div className="mt-3 space-y-2 rounded-lg border border-foreground/[0.08] p-3">
                            <p className="text-xs font-medium text-muted-foreground">Zusammenarbeit am Repo</p>
                            <div className="grid grid-cols-2 gap-2">
                              <label className="block text-[11px] text-muted-foreground">
                                Übernahme gilt ohne Lebenszeichen (Stunden)
                                <input type="number" min={1} max={24} value={uebernahmeStunden}
                                  onChange={(e) => setUebernahmeStunden(Math.min(24, Math.max(1, Number(e.target.value) || 2)))}
                                  className="mt-1 w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50" />
                              </label>
                              <label className="block text-[11px] text-muted-foreground">
                                Höchstens offene Übernahmen je Bearbeiter
                                <input type="number" min={1} max={20} value={maxUebernahmen}
                                  onChange={(e) => setMaxUebernahmen(Math.min(20, Math.max(1, Number(e.target.value) || 2)))}
                                  className="mt-1 w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50" />
                              </label>
                            </div>
                            <label className="flex items-start gap-2 text-[11px] text-muted-foreground">
                              <input type="checkbox" checked={abloesen} onChange={(e) => setAbloesen(e.target.checked)} className="mt-0.5" />
                              <span>
                                Agenten dürfen abgelaufene fremde Übernahmen selbst übernehmen (30 Minuten nach dem Ablauf,
                                mit Kommentar an den bisherigen Bearbeiter). Aus: nur ein Mensch gibt sie im Board frei.
                              </span>
                            </label>
                            <label className="block text-[11px] text-muted-foreground">
                              Fragen aus dem Issue („@ai-employee Agent: …“) wecken Agenten
                              <select value={rueckkanal} onChange={(e) => setRueckkanal(e.target.value as "an" | "aus" | "auto")}
                                className="mt-1 w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-3 py-2 text-sm outline-none focus:border-primary/50">
                                <option value="auto">
                                  Automatisch — {team?.repo_oeffentlich === false && schluessel ? "an (privates Repo mit Schlüssel)" : "aus (öffentliches Repo oder ohne Schlüssel)"}
                                </option>
                                <option value="an">An — auch bei öffentlichem Repo</option>
                                <option value="aus">Aus</option>
                              </select>
                              <span className="mt-1 block">Nur Kommentare von Konten mit Schreibrecht am Repo zählen; höchstens fünf Weckrufe je Stunde.</span>
                            </label>
                          </div>
                        )}
                      </div>
                    )}

                    {projekt && brainListe.length > 0 && (
                      <div>
                        <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                          Second Brains anbinden <span className="text-muted-foreground/40">(optional)</span>
                        </label>
                        <div className="flex flex-wrap gap-1.5">
                          {brainListe.map((b) => {
                            const an = brains.includes(b.label);
                            return (
                              <button key={b.label} type="button" title={b.description ?? undefined}
                                onClick={() => setBrains((alt) => (an ? alt.filter((x) => x !== b.label) : [...alt, b.label]))}
                                className={cn("inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-colors",
                                  an ? "border-primary/50 bg-primary/10 text-foreground" : "border-foreground/[0.1] text-muted-foreground hover:text-foreground")}>
                                {an && <Check className="h-3 w-3" />} {b.name}
                              </button>
                            );
                          })}
                        </div>
                        <p className="text-[11px] text-muted-foreground/50 mt-1">
                          Die Suche im Projektwissen — auch die der Agenten — sieht dann dort mit nach. Gelesen wird nur, nichts verändert.
                        </p>
                      </div>
                    )}

                    {projekt && (
                      <div>
                        <label className="block text-xs font-medium text-muted-foreground mb-1.5">Farbe</label>
                        <div className="flex flex-wrap gap-2">
                          {Object.entries(PROJEKT_FARBEN).map(([key, f]) => (
                            <button
                              key={key}
                              type="button"
                              title={f.name}
                              aria-label={f.name}
                              onClick={() => setFarbe(key)}
                              className={cn(
                                "h-7 w-7 rounded-full ring-offset-2 ring-offset-card transition-all",
                                farbe === key ? "ring-2 ring-foreground" : "hover:scale-110",
                              )}
                              style={{ background: f.hex }}
                            />
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Members */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-2.5">
                        Mitglieder <span className="text-muted-foreground/40">({memberIds.length})</span>
                      </label>
                      <div className="space-y-2">
                        {agents.map((agent) => {
                          const isSelected = memberIds.includes(agent.id);
                          return (
                            <button
                              key={agent.id}
                              type="button"
                              onClick={() => toggleMember(agent.id)}
                              className={cn(
                                "w-full flex items-center gap-3 rounded-xl border p-3 text-left transition-all duration-200",
                                isSelected
                                  ? "border-primary/40 bg-primary/[0.08]"
                                  : "border-foreground/[0.06] bg-foreground/[0.02] hover:bg-foreground/[0.04]"
                              )}
                            >
                              <AgentAvatar config={agent.config} size="sm" />
                              <div className="flex-1 min-w-0">
                                <p className={cn("text-sm font-medium truncate", isSelected ? "text-foreground" : "text-muted-foreground")}>
                                  {agent.name}
                                </p>
                                {agent.role && (
                                  <p className="text-xs text-muted-foreground/70 truncate">{agent.role}</p>
                                )}
                              </div>
                              <div
                                className={cn(
                                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-md border transition-all",
                                  isSelected ? "border-primary bg-primary text-white" : "border-foreground/20"
                                )}
                              >
                                {isSelected && <Check className="h-3 w-3" />}
                              </div>
                            </button>
                          );
                        })}

                        {agents.length === 0 && (
                          <p className="text-xs text-muted-foreground/50 text-center py-3">
                            Keine Agents vorhanden — lege zuerst einen Agent an.
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Lead */}
                    <div>
                      <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                        <span className="inline-flex items-center gap-1.5">
                          <Crown className="h-3.5 w-3.5 text-amber-700 dark:text-amber-400" /> Lead
                        </span>{" "}
                        <span className="text-muted-foreground/40">(optional)</span>
                      </label>
                      <select
                        value={leadAgentId}
                        onChange={(e) => setLeadAgentId(e.target.value)}
                        disabled={memberAgents.length === 0}
                        className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all disabled:opacity-50"
                      >
                        <option value="">Kein Lead</option>
                        {memberAgents.map((agent) => (
                          <option key={agent.id} value={agent.id}>
                            {agent.name}
                          </option>
                        ))}
                      </select>
                      <p className="text-[11px] text-muted-foreground/50 mt-1">
                        {projekt
                          ? "Der Lead verteilt Aufträge ans Projekt an die passenden Kollegen. Ohne Lead wählst du je Auftrag selbst."
                          : "Der Lead muss Mitglied des Teams sein und erhält delegierte Tasks."}
                      </p>
                    </div>

                    {projekt && (
                      <div>
                        <label className="block text-xs font-medium text-muted-foreground mb-1.5">Arbeitstakt</label>
                        <select
                          value={takt}
                          onChange={(e) => setTakt(Number(e.target.value))}
                          className="w-full rounded-lg border border-foreground/[0.1] bg-background/80 px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all"
                        >
                          {TAKTE.map((t) => (
                            <option key={t.minuten} value={t.minuten}>{t.label}</option>
                          ))}
                        </select>
                        <p className="text-[11px] text-muted-foreground/50 mt-1">
                          Im Takt sichtet der Lead den Eingang, plant ein und vergibt die nächsten Karten. Ohne offene
                          Karten fällt der Lauf aus — er kostet dann nichts. Braucht einen Lead (oder genau ein Mitglied).
                        </p>
                      </div>
                    )}

                    {projekt && (
                      <div>
                        <label className="block text-xs font-medium text-muted-foreground mb-1.5">
                          Freigeben für <span className="text-muted-foreground/40">(optional)</span>
                        </label>
                        <p className="text-[11px] text-muted-foreground/60 mb-2">
                          Ohne Freigabe siehst nur du das Projekt. Freigegebene sehen Board und Wissen und arbeiten mit —
                          ihren eigenen Agenten geben sie Aufträge. Verwalten kannst nur du.
                        </p>
                        {nutzer.length === 0 ? (
                          <p className="text-xs text-muted-foreground/50">Keine weiteren Nutzer auf dieser Anlage.</p>
                        ) : (
                          <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-foreground/[0.06] p-1.5">
                            {nutzer.map((n) => {
                              const an = geteilt.includes(n.id);
                              return (
                                <button
                                  key={n.id}
                                  type="button"
                                  onClick={() => setGeteilt((g) => (an ? g.filter((x) => x !== n.id) : [...g, n.id]))}
                                  className={cn(
                                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                                    an ? "bg-primary/[0.08]" : "hover:bg-foreground/[0.04]",
                                  )}
                                >
                                  <span className={cn("flex h-4 w-4 shrink-0 items-center justify-center rounded border",
                                    an ? "border-primary bg-primary text-white" : "border-foreground/20")}>
                                    {an && <Check className="h-3 w-3" />}
                                  </span>
                                  <span className="truncate">{n.name}</span>
                                  {n.email && <span className="truncate text-xs text-muted-foreground">{n.email}</span>}
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Error */}
                    {error && (
                      <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
                        {error}
                      </div>
                    )}
                  </div>

                  {/* Footer */}
                  <div className="flex items-center justify-end gap-3 border-t border-foreground/[0.06] px-6 py-4 bg-foreground/[0.02]">
                    <Dialog.Close className="rounded-lg px-4 py-2.5 text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04] transition-all">
                      Abbrechen
                    </Dialog.Close>
                    <button
                      onClick={handleSave}
                      disabled={saving || !name.trim()}
                      className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground disabled:opacity-50 transition-all shadow-lg shadow-primary/20 hover:bg-primary/90"
                    >
                      {saving ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : isEdit ? (
                        <Users className="h-4 w-4" />
                      ) : (
                        <Plus className="h-4 w-4" />
                      )}
                      {saving ? "Speichere..." : isEdit ? "Speichern" : `${wort} erstellen`}
                    </button>
                  </div>
                </motion.div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}
