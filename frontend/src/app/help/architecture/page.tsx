import type { ReactNode } from "react";
import Link from "next/link";
import { ArrowLeft, Network, Boxes, GitBranch, ShieldCheck, Server, Database, Plug, Mic, KeyRound } from "lucide-react";
import { Mermaid } from "@/components/help/mermaid";
import {
  API_GROUPS, GRAPH_GROUPS, MCP_GROUPS, VOICE_TOOLS, WS_EVENTS, DB_GROUPS, FRONTEND_ROUTES, type Group,
} from "./data";

export const metadata = { title: "Architektur & Schnittstellen — Hilfe" };

const COMPONENT_DIAGRAM = `
graph TB
  U["Browser / Frontend<br/>Next.js + React"] -->|HTTPS| P["Caddy<br/>Reverse-Proxy"]
  CF["Cloudflare Tunnel"] --> P
  P --> O["Orchestrator<br/>FastAPI"]
  P --> FE["Frontend-Container"]
  O <-->|"Pub/Sub"| R["(Redis 7)"]
  O -->|SQL| DB["(PostgreSQL 16)"]
  O -->|"Docker-Socket-Proxy"| D["Docker Engine"]
  D --> A1["Agent-Container 1<br/>Claude Code + MCP"]
  D --> A2["Agent-Container N"]
  A1 <-->|"Pub/Sub"| R
  A1 -->|"MCP / HTTP"| O
  O --> EMB["embedding-service"]
  O --> STT["stt-service"]
`;

const CHAT_FLOW = `
sequenceDiagram
  participant B as Browser
  participant O as Orchestrator
  participant R as Redis
  participant A as Agent-Container
  participant DB as PostgreSQL
  B->>O: WS /ws/agents/{id}/chat {text}
  O->>R: publish agent:{id}:chat
  R->>A: Nachricht
  A->>A: Claude-API (Streaming)
  A-->>R: Antwort-Chunks
  R-->>O: Chunks
  O-->>B: WS text / tool_call / done
  A->>DB: Nachricht persistieren
`;

const APPROVAL_FLOW = `
sequenceDiagram
  participant A as Agent
  participant O as Orchestrator
  participant B as Browser
  A->>O: POST /approvals/request (Risiko)
  O->>B: WS Approval-Modal
  B->>O: POST /approvals/{id}/approve|deny
  O-->>A: Entscheidung (Redis)
  A->>A: ausführen oder abbrechen
`;

const SPAWN_FLOW = `
sequenceDiagram
  participant B as Browser
  participant O as Orchestrator
  participant DP as Docker-Socket-Proxy
  participant D as Docker Engine
  participant A as Agent-Container
  B->>O: POST /agents
  O->>O: Agent-Record + Template
  O->>DP: Container erstellen (Allowlist)
  DP->>D: Container starten
  D-->>A: Agent läuft
  A->>O: subscribe agent:{id}
`;

const DEPLOY_DIAGRAM = `
graph TB
  NET["Internet"] --> CF["Cloudflare Tunnel"]
  CF --> CAD["Caddy :80/:443"]
  subgraph internal["internal network (nicht extern erreichbar)"]
    CAD --> FE["frontend"]
    CAD --> O["orchestrator"]
    O --> PG["(postgres)"]
    O --> RD["(redis)"]
    O --> DSP["docker-socket-proxy"]
  end
  subgraph agentnet["agent-network"]
    O -.-> AG1["agent 1"]
    O -.-> AG2["agent N"]
  end
`;

function Ref({ groups }: { groups: Group[] }) {
  return (
    <div className="space-y-2">
      {groups.map((g) => (
        <details key={g.title} className="group rounded-lg border border-foreground/[0.08] bg-card/60">
          <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-2.5 text-sm font-medium hover:bg-foreground/[0.03]">
            <span>{g.title}</span>
            <span className="text-[11px] text-muted-foreground/50">{g.items.length}</span>
          </summary>
          <ul className="space-y-1 border-t border-foreground/[0.06] px-4 py-3">
            {g.items.map((it, i) => {
              const write = it.startsWith("W ");
              const text = write ? it.slice(2) : it;
              return (
                <li key={i} className="flex items-start gap-2 font-mono text-[11.5px] leading-relaxed text-muted-foreground">
                  {write && (
                    <span className="mt-0.5 rounded bg-amber-500/15 px-1 text-[9px] font-semibold not-italic text-amber-400">WRITE</span>
                  )}
                  <span className="min-w-0">{text}</span>
                </li>
              );
            })}
          </ul>
        </details>
      ))}
    </div>
  );
}

function SectionTitle({ icon: Icon, children, hint }: { icon: typeof Network; children: ReactNode; hint?: string }) {
  return (
    <div className="flex items-center gap-2.5 pt-2">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-foreground/[0.08] bg-foreground/[0.03]">
        <Icon className="h-4 w-4 text-primary" />
      </div>
      <div>
        <h3 className="text-base font-semibold">{children}</h3>
        {hint && <p className="text-[11px] text-muted-foreground/60">{hint}</p>}
      </div>
    </div>
  );
}

export default function ArchitecturePage() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-6 p-6">
      <div className="flex items-center gap-3">
        <Link href="/help" className="flex h-9 w-9 items-center justify-center rounded-lg border border-foreground/[0.08] bg-foreground/[0.03] hover:bg-foreground/[0.06]">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Architektur &amp; Schnittstellen</h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Vollständige technische Referenz: Komponenten, Datenflüsse, API-Endpunkte, Tools, WebSockets und Datenmodelle.
          </p>
        </div>
      </div>

      <div className="flex-1 space-y-8 overflow-y-auto pb-8">
        {/* System-Overview */}
        <section className="space-y-3">
          <SectionTitle icon={Boxes} hint="Wie die Bausteine zusammenspielen">Systemüberblick</SectionTitle>
          <p className="text-sm text-muted-foreground">
            Multi-Agent-Plattform: Nutzer erstellen KI-Agenten (Claude), jeder läuft als isolierter Docker-Container und
            kommuniziert per WebSocket in Echtzeit. Der Orchestrator (FastAPI) steuert Lifecycle, Tasks, Auth und Streaming;
            Redis übernimmt Pub/Sub &amp; Queues, PostgreSQL die Persistenz. Riskante Aktionen laufen über einen Freigabe-Workflow.
          </p>
          <Mermaid chart={COMPONENT_DIAGRAM} caption="Komponenten & Kommunikationswege" />
        </section>

        {/* Datenflüsse */}
        <section className="space-y-3">
          <SectionTitle icon={GitBranch} hint="Die vier wichtigsten Abläufe">Datenflüsse</SectionTitle>
          <div className="grid gap-4 lg:grid-cols-2">
            <Mermaid chart={CHAT_FLOW} caption="Chat-Nachricht (Streaming)" />
            <Mermaid chart={APPROVAL_FLOW} caption="Tool-Freigabe (riskante Aktion)" />
            <Mermaid chart={SPAWN_FLOW} caption="Agent-Erstellung (Container-Spawn)" />
            <Mermaid chart={DEPLOY_DIAGRAM} caption="Deployment-/Netzwerk-Topologie" />
          </div>
        </section>

        {/* API */}
        <section className="space-y-3">
          <SectionTitle icon={Server} hint="~330 Endpunkte, Basis-Prefix /api/v1">HTTP-API</SectionTitle>
          <p className="text-[11px] text-muted-foreground/70">
            Auth-Legende: <b>[auth]</b> Session/JWT · <b>[admin]</b> Admin · <b>[auth|agent]</b> User oder Agent-Token ·
            <b> [agent]</b> agent-facing (Agent-Token) · <b>[public]</b> kein FastAPI-Dependency (meist Ticket/Bearer/Webhook-Token).
          </p>
          <Ref groups={API_GROUPS} />
        </section>

        {/* MS Graph */}
        <section className="space-y-3">
          <SectionTitle icon={Plug} hint="61 Tools (31 lesen, 30 schreiben)">MS-Graph-Connector (M365)</SectionTitle>
          <Ref groups={GRAPH_GROUPS} />
        </section>

        {/* MCP */}
        <section className="space-y-3">
          <SectionTitle icon={Plug} hint="Was ein Agent-Container aufrufen kann">MCP-Server & Agent-Tools</SectionTitle>
          <Ref groups={MCP_GROUPS} />
        </section>

        {/* Voice + WS */}
        <section className="space-y-3">
          <SectionTitle icon={Mic} hint="Realtime-Cockpit & Stream-Protokolle">Voice-Layer & WebSockets</SectionTitle>
          <Ref groups={VOICE_TOOLS} />
          <Ref groups={WS_EVENTS} />
        </section>

        {/* DB */}
        <section className="space-y-3">
          <SectionTitle icon={Database} hint="PostgreSQL-Modelle (gruppiert)">Datenmodelle</SectionTitle>
          <Ref groups={DB_GROUPS} />
        </section>

        {/* Frontend */}
        <section className="space-y-3">
          <SectionTitle icon={Network} hint="Next.js App-Router-Seiten">Frontend-Routen</SectionTitle>
          <Ref groups={FRONTEND_ROUTES} />
        </section>

        {/* Sicherheit */}
        <section className="space-y-3">
          <SectionTitle icon={ShieldCheck} hint="Wie Zugriffe abgesichert sind">Sicherheits-Grundlagen</SectionTitle>
          <ul className="space-y-1.5 text-sm text-muted-foreground">
            <li className="flex gap-2"><KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> WebSockets authentifizieren per kurzlebigem Einmal-Ticket (30s TTL) statt langlebigem JWT in der URL.</li>
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> Agent-Ownership-Checks auf Chat-/Voice-/Ressourcen-Endpunkten (Schutz gegen IDOR).</li>
            <li className="flex gap-2"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> Riskante Agenten-Aktionen erfordern eine Freigabe (Command-Policies, Approval-Rules, Autonomie-Matrix L1–L4).</li>
            <li className="flex gap-2"><Server className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> Nur der Reverse-Proxy ist extern erreichbar; Datenbank, Redis und Agenten liegen in internen Docker-Netzen.</li>
            <li className="flex gap-2"><KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-primary" /> Secrets, Provider-Keys und OAuth-Token werden verschlüsselt gespeichert (nie im Klartext committet).</li>
          </ul>
        </section>

        <p className="pt-2 text-center text-[11px] text-muted-foreground/50">
          Faktenbasiert aus dem Code inventarisiert. Bei neuen Endpunkten/Tools die Referenz in <span className="font-mono">help/architecture/data.ts</span> ergänzen.
        </p>
      </div>
    </div>
  );
}
