"use client";

import { useEffect, useMemo, useState } from "react";
import type React from "react";
import { ChevronDown, Loader2, Plus, Save, Search, Shield, Trash2, UserPlus, Users, X } from "lucide-react";
import * as api from "@/lib/api";
import type { CustomRole, RolePermissions, MountCatalogEntry, AgentSecretEntry, McpServerInfo, CustomPage } from "@/lib/api";
import type { AdminUser, AgentTemplate, AIAccount, Integration } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useToast } from "@/components/ui/dialog-provider";

// Fest eingebaute Menuepunkte. Selbst angelegte Seiten kommen zur Laufzeit dazu
// (siehe ``menuOptions``) — deshalb steht hier nur, was im Code verdrahtet ist.
const MENU_PATHS = [
  "/dashboard",
  "/agents",
  "/tasks",
  "/analytics",
  "/knowledge",
  "/meeting-rooms",
  "/skills",
  "/triggers",
  "/approvals",
  "/health",
  "/audit",
  "/files",
  "/integrations",
  "/secrets",
  "/ai-accounts",
  "/settings",
];

const LLM_PROVIDERS = ["anthropic", "bedrock", "vertex", "foundry", "openai", "google", "ollama", "lm-studio"];

function listToText(value?: string[] | number[] | null) {
  return value == null ? "" : value.join(", ");
}

function parseStringList(value: string): string[] | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  return trimmed.split(",").map((v) => v.trim()).filter(Boolean);
}

function parseNumberList(value: string): number[] | null {
  const strings = parseStringList(value);
  if (strings == null) return null;
  return strings.map((v) => Number(v)).filter((n) => Number.isFinite(n));
}

function toggleListValue(list: string[] | null | undefined, value: string): string[] {
  const current = list ?? [];
  return current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
}

function toggleNumberValue(list: number[] | null | undefined, value: number): number[] {
  const current = list ?? [];
  return current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
}

interface RoleDraft {
  id?: number;
  name: string;
  description: string;
  max_agents: string;
  template_ids: string;
  llm_providers: string[] | null;
  models: string[] | null;
  mount_labels: string[] | null;
  ai_account_ids: number[] | null;
  secret_ids: number[] | null;
  mcp_server_ids: number[] | null;
  integration_providers: string[] | null;
  url_host_patterns: string;
  menu_paths: string[] | null;
}

function draftFromRole(role?: CustomRole): RoleDraft {
  const p = role?.permissions ?? {};
  return {
    id: role?.id,
    name: role?.name ?? "",
    description: role?.description ?? "",
    max_agents: p.max_agents == null ? "" : String(p.max_agents),
    template_ids: listToText(p.template_ids),
    llm_providers: p.llm_providers ?? null,
    models: p.models ?? null,
    mount_labels: p.mount_labels ?? null,
    ai_account_ids: p.ai_account_ids ?? null,
    secret_ids: p.secret_ids ?? null,
    mcp_server_ids: p.mcp_server_ids ?? null,
    integration_providers: p.integration_providers ?? null,
    url_host_patterns: listToText(p.url_host_patterns),
    menu_paths: p.menu_paths ?? null,
  };
}

function permissionsFromDraft(draft: RoleDraft): RolePermissions {
  const max = draft.max_agents.trim();
  return {
    max_agents: max === "" ? null : Math.max(0, Number(max) || 0),
    template_ids: parseNumberList(draft.template_ids),
    llm_providers: draft.llm_providers,
    models: draft.models,
    mount_labels: draft.mount_labels,
    ai_account_ids: draft.ai_account_ids,
    secret_ids: draft.secret_ids,
    mcp_server_ids: draft.mcp_server_ids,
    integration_providers: draft.integration_providers,
    url_host_patterns: parseStringList(draft.url_host_patterns),
    menu_paths: draft.menu_paths,
  };
}

interface Props {
  users: AdminUser[];
  onUserRoleAssigned: (userId: string, customRoleId: number | null) => void;
  onRolesChanged?: (roles: CustomRole[]) => void;
}

export function RolesPanel({ users, onUserRoleAssigned, onRolesChanged }: Props) {
  const toast = useToast();
  const [roles, setRoles] = useState<CustomRole[]>([]);
  const [selectedId, setSelectedId] = useState<number | "new">("new");
  const [draft, setDraft] = useState<RoleDraft>(draftFromRole());
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [mounts, setMounts] = useState<MountCatalogEntry[]>([]);
  const [aiAccounts, setAiAccounts] = useState<AIAccount[]>([]);
  const [secrets, setSecrets] = useState<AgentSecretEntry[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServerInfo[]>([]);
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [customPages, setCustomPages] = useState<CustomPage[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Mitglieder-Sektion: aufklappbar, zeigt nur die User DIESER Rolle
  const [membersOpen, setMembersOpen] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [memberQuery, setMemberQuery] = useState("");
  const [pendingUserId, setPendingUserId] = useState<string | null>(null);

  // Model names offered for the per-group allowlist — gathered from the configured AI accounts.
  const modelOptions = useMemo(() => {
    const names = new Set<string>();
    for (const acc of aiAccounts) {
      for (const m of (acc.models ?? [])) {
        const name = typeof m === "string" ? m : (m as { name?: string }).name;
        if (name) names.add(name);
      }
    }
    return Array.from(names).sort();
  }, [aiAccounts]);

  const selectedRole = useMemo(
    () => roles.find((r) => r.id === selectedId),
    [roles, selectedId],
  );

  // Wie viele User hängen an welcher Rolle — für die Liste links und den Zähler oben.
  const memberCounts = useMemo(() => {
    const counts = new Map<number, number>();
    for (const u of users) {
      if (u.custom_role_id != null) counts.set(u.custom_role_id, (counts.get(u.custom_role_id) ?? 0) + 1);
    }
    return counts;
  }, [users]);

  const members = useMemo(
    () => (selectedRole ? users.filter((u) => u.custom_role_id === selectedRole.id) : []),
    [users, selectedRole],
  );

  const candidates = useMemo(() => {
    if (!selectedRole) return [];
    const q = memberQuery.trim().toLowerCase();
    return users
      .filter((u) => u.custom_role_id !== selectedRole.id)
      .filter((u) => !q || u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q));
  }, [users, selectedRole, memberQuery]);

  const roleNameById = useMemo(() => new Map(roles.map((r) => [r.id, r.name])), [roles]);

  // Menuepfade zum Abhaken: die eingebauten plus die selbst angelegten Seiten.
  // Ohne den zweiten Teil waere eine neue Seite zwar da, aber in keiner Rolle
  // freischaltbar — und damit fuer alle ausser Administratoren unsichtbar.
  const menuOptions = useMemo(() => {
    const builtin = MENU_PATHS.map((path) => ({ path, label: path }));
    const pages = customPages.map((p) => ({
      path: p.menu_path,
      label: `${p.title} (${p.menu_path})`,
    }));
    return [...builtin, ...pages];
  }, [customPages]);

  const reload = async () => {
    setLoading(true);
    try {
      const [roleData, templateData, mountData, aiAccountData, secretData, mcpData, integrationData, pageData] = await Promise.all([
        api.listRoles(),
        api.getTemplates(),
        api.getAgentMountCatalog(),
        api.listAIAccounts().catch(() => [] as AIAccount[]),
        api.listSecrets().catch(() => [] as AgentSecretEntry[]),
        api.getMcpServers().then((r) => r.servers).catch(() => [] as McpServerInfo[]),
        api.getIntegrations().then((r) => r.integrations).catch(() => [] as Integration[]),
        api.listCustomPages().then((r) => r.pages).catch(() => [] as CustomPage[]),
      ]);
      setRoles(roleData.roles);
      onRolesChanged?.(roleData.roles);
      setTemplates(templateData.templates);
      setMounts(mountData.mounts);
      setAiAccounts(aiAccountData);
      setSecrets(secretData);
      setMcpServers(mcpData);
      setIntegrations(integrationData);
      setCustomPages(pageData);
      if (selectedId !== "new") {
        const role = roleData.roles.find((r) => r.id === selectedId);
        setDraft(draftFromRole(role));
      }
    } catch (e) {
      toast.error("Rollen konnten nicht geladen werden", String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectRole = (role: CustomRole | "new") => {
    setAddOpen(false);
    setMemberQuery("");
    if (role === "new") {
      setSelectedId("new");
      setDraft(draftFromRole());
    } else {
      setSelectedId(role.id);
      setDraft(draftFromRole(role));
    }
  };

  const handleSave = async () => {
    const name = draft.name.trim();
    if (!name) {
      toast.error("Name fehlt", "Bitte gib der Rolle einen Namen.");
      return;
    }
    setSaving(true);
    try {
      const permissions = permissionsFromDraft(draft);
      if (draft.id) {
        const updated = await api.updateRole(draft.id, {
          name,
          description: draft.description,
          permissions,
        });
        const next = roles.map((r) => r.id === updated.id ? updated : r);
        setRoles(next);
        onRolesChanged?.(next);
        setDraft(draftFromRole(updated));
        toast.success("Rolle gespeichert", name);
      } else {
        const created = await api.createRole(name, draft.description, permissions);
        const next = [...roles, created].sort((a, b) => a.name.localeCompare(b.name));
        setRoles(next);
        onRolesChanged?.(next);
        setSelectedId(created.id);
        setDraft(draftFromRole(created));
        toast.success("Rolle erstellt", name);
      }
    } catch (e) {
      toast.error("Speichern fehlgeschlagen", String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!draft.id) return;
    setSaving(true);
    try {
      await api.deleteRole(draft.id);
      const next = roles.filter((r) => r.id !== draft.id);
      setRoles(next);
      onRolesChanged?.(next);
      users
        .filter((u) => u.custom_role_id === draft.id)
        .forEach((u) => onUserRoleAssigned(u.id, null));
      selectRole("new");
      toast.success("Rolle gelöscht");
    } catch (e) {
      toast.error("Löschen fehlgeschlagen", String(e));
    } finally {
      setSaving(false);
    }
  };

  const assign = async (userId: string, customRoleId: number | null) => {
    setPendingUserId(userId);
    try {
      await api.assignUserRole(userId, customRoleId);
      onUserRoleAssigned(userId, customRoleId);
    } catch (e) {
      toast.error("Zuweisung fehlgeschlagen", String(e));
    } finally {
      setPendingUserId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[280px_minmax(0,1fr)] gap-5">
      <div className="space-y-3">
        <button
          onClick={() => selectRole("new")}
          className={cn(
            "flex w-full items-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors",
            selectedId === "new"
              ? "border-primary/40 bg-primary/10 text-primary"
              : "border-foreground/[0.08] bg-card/60 text-muted-foreground hover:text-foreground"
          )}
        >
          <Plus className="h-4 w-4" />
          Neue Rolle
        </button>
        <div className="space-y-2">
          {roles.map((role) => (
            <button
              key={role.id}
              onClick={() => selectRole(role)}
              className={cn(
                "w-full rounded-lg border px-3 py-3 text-left transition-colors",
                selectedId === role.id
                  ? "border-primary/40 bg-primary/10"
                  : "border-foreground/[0.08] bg-card/60 hover:bg-card"
              )}
            >
              <div className="flex items-center gap-2">
                <Shield className="h-4 w-4 shrink-0 text-primary" />
                <span className="truncate text-sm font-semibold">{role.name}</span>
                <span className="ml-auto shrink-0 rounded-md bg-foreground/10 px-1.5 py-0.5 text-[10px] text-muted-foreground">
                  {memberCounts.get(role.id) ?? 0}
                </span>
              </div>
              {role.description && (
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{role.description}</p>
              )}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-5">
        <div className="rounded-xl border border-foreground/[0.08] bg-card/70 p-5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Name</label>
              <input
                value={draft.name}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
                className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
              />
            </div>
            <div>
              <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Max Agents</label>
              <input
                type="number"
                min={0}
                placeholder="leer = unbegrenzt"
                value={draft.max_agents}
                onChange={(e) => setDraft((d) => ({ ...d, max_agents: e.target.value }))}
                className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
              />
            </div>
          </div>

          <div className="mt-3">
            <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Beschreibung</label>
            <input
              value={draft.description}
              onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
              className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
          </div>

          <div className="mt-4 grid grid-cols-2 gap-4">
            <PermissionBlock title="LLM-Provider">
              <div className="flex flex-wrap gap-2">
                {LLM_PROVIDERS.map((provider) => (
                  <ToggleChip
                    key={provider}
                    active={draft.llm_providers == null || draft.llm_providers.includes(provider)}
                    muted={draft.llm_providers == null}
                    label={provider}
                    onClick={() => setDraft((d) => ({
                      ...d,
                      llm_providers: toggleListValue(d.llm_providers, provider),
                    }))}
                  />
                ))}
              </div>
              <SetUnlimitedButton onClick={() => setDraft((d) => ({ ...d, llm_providers: null }))} />
            </PermissionBlock>

            <PermissionBlock title="Modelle">
              <div className="flex flex-wrap gap-2">
                {modelOptions.length === 0 ? (
                  <span className="text-[11px] text-muted-foreground/50">Keine Modelle aus AI-Accounts</span>
                ) : modelOptions.map((model) => (
                  <ToggleChip
                    key={model}
                    active={draft.models == null || draft.models.includes(model)}
                    muted={draft.models == null}
                    label={model}
                    onClick={() => setDraft((d) => ({
                      ...d,
                      models: toggleListValue(d.models, model),
                    }))}
                  />
                ))}
              </div>
              <SetUnlimitedButton onClick={() => setDraft((d) => ({ ...d, models: null }))} />
            </PermissionBlock>

            <PermissionBlock title="Mountshares">
              <div className="flex flex-wrap gap-2">
                {mounts.map((mount) => (
                  <ToggleChip
                    key={mount.label}
                    active={draft.mount_labels == null || draft.mount_labels.includes(mount.label)}
                    muted={draft.mount_labels == null}
                    label={mount.label}
                    onClick={() => setDraft((d) => ({
                      ...d,
                      mount_labels: toggleListValue(d.mount_labels, mount.label),
                    }))}
                  />
                ))}
              </div>
              <SetUnlimitedButton onClick={() => setDraft((d) => ({ ...d, mount_labels: null }))} />
            </PermissionBlock>

            <PermissionBlock title="AI-Accounts (Konten)">
              <div className="flex flex-wrap gap-2">
                {aiAccounts.length === 0 && (
                  <span className="text-[11px] text-muted-foreground/50">Keine AI-Accounts angelegt</span>
                )}
                {aiAccounts.map((acc) => (
                  <ToggleChip
                    key={acc.id}
                    active={draft.ai_account_ids == null || draft.ai_account_ids.includes(acc.id)}
                    muted={draft.ai_account_ids == null}
                    label={acc.name}
                    onClick={() => setDraft((d) => ({
                      ...d,
                      ai_account_ids: toggleNumberValue(d.ai_account_ids, acc.id),
                    }))}
                  />
                ))}
              </div>
              <SetUnlimitedButton onClick={() => setDraft((d) => ({ ...d, ai_account_ids: null }))} />
            </PermissionBlock>

            <PermissionBlock title="Keys / Secrets">
              <div className="flex flex-wrap gap-2">
                {secrets.length === 0 && (
                  <span className="text-[11px] text-muted-foreground/50">Keine Keys angelegt</span>
                )}
                {secrets.map((s) => (
                  <ToggleChip
                    key={s.id}
                    active={draft.secret_ids == null || draft.secret_ids.includes(s.id)}
                    muted={draft.secret_ids == null}
                    label={s.name}
                    onClick={() => setDraft((d) => ({
                      ...d,
                      secret_ids: toggleNumberValue(d.secret_ids, s.id),
                    }))}
                  />
                ))}
              </div>
              <SetUnlimitedButton onClick={() => setDraft((d) => ({ ...d, secret_ids: null }))} />
            </PermissionBlock>

            <PermissionBlock title="MCP-Server / Tools">
              <div className="flex flex-wrap gap-2">
                {mcpServers.length === 0 && (
                  <span className="text-[11px] text-muted-foreground/50">Keine MCP-Server angelegt</span>
                )}
                {mcpServers.map((m) => (
                  <ToggleChip
                    key={m.id}
                    active={draft.mcp_server_ids == null || draft.mcp_server_ids.includes(m.id)}
                    muted={draft.mcp_server_ids == null}
                    label={m.name}
                    onClick={() => setDraft((d) => ({
                      ...d,
                      mcp_server_ids: toggleNumberValue(d.mcp_server_ids, m.id),
                    }))}
                  />
                ))}
              </div>
              <SetUnlimitedButton onClick={() => setDraft((d) => ({ ...d, mcp_server_ids: null }))} />
            </PermissionBlock>

            <PermissionBlock title="Integrationen (M365 / Exchange)">
              <div className="flex flex-wrap gap-2">
                {integrations.length === 0 && (
                  <span className="text-[11px] text-muted-foreground/50">Keine Integrationen verfügbar</span>
                )}
                {integrations.map((it) => (
                  <ToggleChip
                    key={it.provider}
                    active={draft.integration_providers == null || draft.integration_providers.includes(it.provider)}
                    muted={draft.integration_providers == null}
                    label={it.display_name || it.provider}
                    onClick={() => setDraft((d) => ({
                      ...d,
                      integration_providers: toggleListValue(d.integration_providers, it.provider),
                    }))}
                  />
                ))}
              </div>
              <SetUnlimitedButton onClick={() => setDraft((d) => ({ ...d, integration_providers: null }))} />
            </PermissionBlock>

            <PermissionBlock title="Menüpfade">
              <div className="flex flex-wrap gap-2">
                {menuOptions.map((opt) => (
                  <ToggleChip
                    key={opt.path}
                    active={draft.menu_paths == null || draft.menu_paths.includes(opt.path)}
                    muted={draft.menu_paths == null}
                    label={opt.label}
                    onClick={() => setDraft((d) => ({
                      ...d,
                      menu_paths: toggleListValue(d.menu_paths, opt.path),
                    }))}
                  />
                ))}
              </div>
              <SetUnlimitedButton onClick={() => setDraft((d) => ({ ...d, menu_paths: null }))} />
            </PermissionBlock>

            <PermissionBlock title="Templates">
              <input
                placeholder="Template-IDs, z.B. 1, 4, 9; leer = alle"
                value={draft.template_ids}
                onChange={(e) => setDraft((d) => ({ ...d, template_ids: e.target.value }))}
                className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
              />
              <div className="mt-2 max-h-24 overflow-y-auto text-[11px] text-muted-foreground">
                {templates.slice(0, 20).map((t) => (
                  <div key={t.id}>{t.id}: {t.display_name}</div>
                ))}
              </div>
            </PermissionBlock>
          </div>

          <div className="mt-4">
            <label className="mb-1 block text-[11px] font-medium text-muted-foreground">URL-Host-Patterns</label>
            <input
              placeholder="github.com, *.wikipedia.org; leer = keine Rollenbeschränkung"
              value={draft.url_host_patterns}
              onChange={(e) => setDraft((d) => ({ ...d, url_host_patterns: e.target.value }))}
              className="w-full rounded-lg border border-foreground/[0.08] bg-background px-3 py-2 text-sm outline-none focus:border-primary/50"
            />
          </div>

          <div className="mt-5 flex items-center justify-end gap-2">
            {selectedRole && (
              <button
                onClick={handleDelete}
                disabled={saving}
                className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-400 hover:bg-red-500/10 disabled:opacity-50"
              >
                <Trash2 className="h-4 w-4" />
                Löschen
              </button>
            )}
            <button
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
            >
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Speichern
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-foreground/[0.08] bg-card/70">
          <button
            type="button"
            onClick={() => setMembersOpen((o) => !o)}
            className="flex w-full items-center gap-2 px-5 py-4 text-left"
          >
            <Users className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-semibold">Mitglieder</h3>
            {selectedRole && (
              <span className="rounded-md bg-foreground/10 px-1.5 py-0.5 text-[11px] text-muted-foreground">
                {members.length}
              </span>
            )}
            <span className="ml-auto flex items-center gap-2 text-[11px] text-muted-foreground">
              {selectedRole ? selectedRole.name : "keine Rolle gewählt"}
              <ChevronDown className={cn("h-4 w-4 transition-transform", membersOpen && "rotate-180")} />
            </span>
          </button>

          {membersOpen && (
            <div className="border-t border-foreground/[0.06] p-5 pt-4">
              {!selectedRole ? (
                <p className="text-xs text-muted-foreground">
                  Rolle zuerst speichern — danach lassen sich hier User zuweisen.
                </p>
              ) : (
                <div className="space-y-3">
                  {members.length === 0 ? (
                    <p className="text-xs text-muted-foreground">Noch niemand in dieser Rolle.</p>
                  ) : (
                    <div className="grid grid-cols-2 gap-2">
                      {members.map((u) => (
                        <div
                          key={u.id}
                          className="flex items-center justify-between gap-3 rounded-lg border border-foreground/[0.06] bg-background/60 px-3 py-2"
                        >
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{u.name}</p>
                            <p className="truncate text-[11px] text-muted-foreground">{u.email}</p>
                          </div>
                          <button
                            type="button"
                            onClick={() => assign(u.id, null)}
                            disabled={pendingUserId === u.id}
                            title="Aus der Rolle entfernen"
                            className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-red-500/10 hover:text-red-400 disabled:opacity-50"
                          >
                            {pendingUserId === u.id
                              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              : <X className="h-3.5 w-3.5" />}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  {addOpen ? (
                    <div className="rounded-lg border border-foreground/[0.08] bg-background/60 p-3">
                      <div className="mb-2 flex items-center gap-2">
                        <Search className="h-3.5 w-3.5 text-muted-foreground" />
                        <input
                          autoFocus
                          value={memberQuery}
                          onChange={(e) => setMemberQuery(e.target.value)}
                          placeholder="Name oder E-Mail suchen"
                          className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
                        />
                        <button
                          type="button"
                          onClick={() => { setAddOpen(false); setMemberQuery(""); }}
                          className="rounded-md p-1 text-muted-foreground hover:text-foreground"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <div className="max-h-56 space-y-1 overflow-y-auto">
                        {candidates.length === 0 ? (
                          <p className="px-1 py-2 text-xs text-muted-foreground">Kein passender User.</p>
                        ) : candidates.map((u) => (
                          <button
                            key={u.id}
                            type="button"
                            disabled={pendingUserId === u.id}
                            onClick={() => assign(u.id, selectedRole.id)}
                            className="flex w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-foreground/[0.05] disabled:opacity-50"
                          >
                            <div className="min-w-0">
                              <p className="truncate text-sm">{u.name}</p>
                              <p className="truncate text-[11px] text-muted-foreground">{u.email}</p>
                            </div>
                            <span className="shrink-0 text-[11px] text-muted-foreground">
                              {pendingUserId === u.id
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : u.custom_role_id != null
                                  ? `aktuell: ${roleNameById.get(u.custom_role_id) ?? "andere Rolle"}`
                                  : u.role}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setAddOpen(true)}
                      className="inline-flex items-center gap-2 rounded-lg border border-foreground/[0.08] px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
                    >
                      <UserPlus className="h-3.5 w-3.5" />
                      User hinzufügen
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PermissionBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-foreground/[0.06] bg-background/50 p-3">
      <h4 className="mb-2 text-xs font-semibold">{title}</h4>
      {children}
    </div>
  );
}

function ToggleChip({ label, active, muted, onClick }: { label: string; active: boolean; muted?: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-2 py-1 text-[11px] transition-colors",
        active
          ? muted
            ? "border-primary/20 bg-primary/5 text-primary/70"
            : "border-primary/40 bg-primary/10 text-primary"
          : "border-foreground/[0.08] text-muted-foreground hover:text-foreground"
      )}
    >
      {label}
    </button>
  );
}

function SetUnlimitedButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="mt-2 text-[11px] font-medium text-muted-foreground hover:text-foreground"
    >
      Alle erlauben
    </button>
  );
}
