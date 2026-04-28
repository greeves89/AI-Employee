"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth";
import {
  Bot,
  Github,
  Shield,
  Zap,
  Users,
  Brain,
  GitBranch,
  Lock,
  MessageSquare,
  BarChart3,
  Globe,
  Cpu,
  ArrowRight,
  CheckCircle2,
  Star,
} from "lucide-react";

const FEATURES = [
  {
    icon: Shield,
    color: "text-emerald-400",
    bg: "bg-emerald-500/10 border-emerald-500/20",
    title: "Governance & Compliance",
    desc: "Autonomy levels L1–L4 with whitelist enforcement. Every action outside the whitelist triggers a human-in-the-loop approval. Full audit trail for DSGVO-ready deployments.",
  },
  {
    icon: Lock,
    color: "text-blue-400",
    bg: "bg-blue-500/10 border-blue-500/20",
    title: "True Isolation",
    desc: "Each agent runs in its own Docker container with isolated filesystem, memory, and resource limits. No shared scratch dirs, no data leaking between tenants.",
  },
  {
    icon: Brain,
    color: "text-purple-400",
    bg: "bg-purple-500/10 border-purple-500/20",
    title: "Semantic Memory",
    desc: "Local bge-m3 embeddings (1024-dim, multilingual). No OpenAI embedding fees, no data leaving your server. Per-user knowledge base with backlinks and tags.",
  },
  {
    icon: Users,
    color: "text-cyan-400",
    bg: "bg-cyan-500/10 border-cyan-500/20",
    title: "Multi-Agent Meetings",
    desc: "Put 3–4 agents in a room and they round-robin on a topic. Design reviews, legal-vs-marketing tradeoffs, architecture debates — all fully automated.",
  },
  {
    icon: GitBranch,
    color: "text-orange-400",
    bg: "bg-orange-500/10 border-orange-500/20",
    title: "LLM-Agnostic",
    desc: "Claude, GPT-4o, Gemini 2.0, Mistral, or local Ollama — swap the model per agent without touching code. Runs on Claude Pro subscription (no per-token costs).",
  },
  {
    icon: Globe,
    color: "text-rose-400",
    bg: "bg-rose-500/10 border-rose-500/20",
    title: "Microsoft 365 & OAuth",
    desc: "25 MS Graph MCP tools — Outlook, Calendar, Teams, Planner, OneDrive. Per-user OAuth tokens, never shared. Google and Apple integrations included.",
  },
];

const TEMPLATES = [
  "Fullstack Developer",
  "Frontend Specialist",
  "Backend Engineer",
  "DevOps Engineer",
  "Data Scientist",
  "QA Engineer",
  "Code Reviewer",
  "Marketing Manager",
  "Content Creator",
  "SEO Specialist",
  "Sales Assistant",
  "Customer Support",
  "Legal Assistant",
  "Tax Advisor",
  "Accountant",
  "Financial Analyst",
  "HR Assistant",
  "Project Manager",
  "Medical Scribe",
  "Research Analyst",
  "Technical Writer",
  "Product Manager",
  "Procurement Agent",
  "IT Support",
  "Personal Assistant",
];

const STATS = [
  { value: "25", label: "Agent Templates" },
  { value: "L1–L4", label: "Autonomy Levels" },
  { value: "100%", label: "Self-Hosted" },
  { value: "DSGVO", label: "Ready*" },
];

export default function LandingPage() {
  const router = useRouter();
  const { user, loading } = useAuthStore();

  useEffect(() => {
    if (!loading && user) {
      router.replace("/dashboard");
    }
  }, [user, loading, router]);

  if (loading || user) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
      </div>
    );
  }

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-background text-foreground">
      {/* Background grid + glow */}
      <div
        className="pointer-events-none fixed inset-0"
        style={{
          backgroundImage:
            "radial-gradient(ellipse 80% 50% at 50% -10%, hsl(217 91% 60% / 0.12) 0%, transparent 70%), " +
            "linear-gradient(hsl(217 33% 15% / 0.3) 1px, transparent 1px), " +
            "linear-gradient(90deg, hsl(217 33% 15% / 0.3) 1px, transparent 1px)",
          backgroundSize: "100% 100%, 48px 48px, 48px 48px",
        }}
      />

      {/* Navbar */}
      <nav className="sticky top-0 z-50 border-b border-foreground/[0.06] bg-background/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-cyan-400 shadow-lg shadow-blue-500/20">
              <Bot className="h-4 w-4 text-white" />
            </div>
            <span className="text-sm font-semibold tracking-tight">AI Employee</span>
          </div>

          <div className="flex items-center gap-3">
            <a
              href="https://github.com/greeves89/AI-Employee"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-foreground/[0.04] hover:text-foreground"
            >
              <Github className="h-4 w-4" />
              GitHub
            </a>
            <Link
              href="/login"
              className="rounded-xl bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 transition-colors hover:bg-primary/90"
            >
              Sign In
            </Link>
          </div>
        </div>
      </nav>

      <main className="relative mx-auto max-w-6xl px-6">
        {/* Hero */}
        <section className="pb-16 pt-24 text-center">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-blue-500/20 bg-blue-500/10 px-4 py-1.5">
            <Star className="h-3.5 w-3.5 text-blue-400" />
            <span className="text-xs font-medium text-blue-300">
              Self-hosted · Open Source · DSGVO-ready
            </span>
          </div>

          <h1 className="mx-auto mb-6 max-w-3xl text-5xl font-bold tracking-tight leading-[1.1]">
            Your team of{" "}
            <span
              style={{
                background:
                  "linear-gradient(135deg, hsl(217 91% 70%), hsl(199 89% 60%), hsl(217 91% 60%))",
                WebkitBackgroundClip: "text",
                WebkitTextFillColor: "transparent",
              }}
            >
              AI agents
            </span>
            {" "}— fully under your control
          </h1>

          <p className="mx-auto mb-10 max-w-2xl text-lg text-muted-foreground leading-relaxed">
            The self-hosted multi-agent platform for teams who need compliance, governance, and true isolation.
            Every agent runs in its own Docker container. All data stays on your infrastructure.
            Built for KMU and regulated industries in the DACH region.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-3">
            <Link
              href="/login"
              className="inline-flex items-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/25 transition-all hover:bg-primary/90 hover:shadow-primary/30 hover:shadow-xl"
            >
              Open Platform
              <ArrowRight className="h-4 w-4" />
            </Link>
            <a
              href="https://github.com/greeves89/AI-Employee"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-xl border border-foreground/[0.08] bg-foreground/[0.02] px-6 py-3 text-sm font-medium text-foreground transition-colors hover:bg-foreground/[0.06]"
            >
              <Github className="h-4 w-4" />
              View on GitHub
            </a>
          </div>
        </section>

        {/* Stats bar */}
        <section className="mb-16">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {STATS.map((s) => (
              <div
                key={s.label}
                className="rounded-xl border border-foreground/[0.06] bg-card/80 p-5 text-center backdrop-blur-sm"
              >
                <div className="text-2xl font-bold text-foreground">{s.value}</div>
                <div className="mt-1 text-xs text-muted-foreground">{s.label}</div>
              </div>
            ))}
          </div>
        </section>

        {/* Features */}
        <section className="mb-20">
          <div className="mb-10 text-center">
            <p className="mb-2 text-xs font-medium uppercase tracking-widest text-muted-foreground/60">
              What sets it apart
            </p>
            <h2 className="text-2xl font-bold tracking-tight">
              Built for the real world
            </h2>
          </div>

          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div
                key={f.title}
                className="group rounded-xl border border-foreground/[0.06] bg-card/80 p-5 backdrop-blur-sm transition-colors hover:border-foreground/[0.10] hover:bg-card"
              >
                <div className={`mb-4 inline-flex h-10 w-10 items-center justify-center rounded-xl border ${f.bg}`}>
                  <f.icon className={`h-5 w-5 ${f.color}`} />
                </div>
                <h3 className="mb-2 text-sm font-semibold">{f.title}</h3>
                <p className="text-[13px] leading-relaxed text-muted-foreground">{f.desc}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Comparison highlights */}
        <section className="mb-20">
          <div className="rounded-2xl border border-foreground/[0.06] bg-card/80 p-8 backdrop-blur-sm">
            <div className="mb-8 text-center">
              <p className="mb-2 text-xs font-medium uppercase tracking-widest text-muted-foreground/60">
                Why not just use SaaS?
              </p>
              <h2 className="text-2xl font-bold tracking-tight">
                AI Employee vs. the alternatives
              </h2>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {[
                { label: "Docker-isolated agents per user", yes: true },
                { label: "Multi-tenant with RLS data isolation", yes: true },
                { label: "Local embeddings (no OpenAI required)", yes: true },
                { label: "Whitelist-based L1–L4 autonomy", yes: true },
                { label: "Agent-to-agent Meeting Rooms", yes: true },
                { label: "Agents deploy their own Docker apps", yes: true },
                { label: "Full governance audit trail", yes: true },
                { label: "Telegram + Voice (STT/TTS)", yes: true },
                { label: "Microsoft 365 via MS Graph MCP", yes: true },
                { label: "Self-hosted, no vendor lock-in", yes: true },
                { label: "Desktop Bridge (computer use)", yes: true },
                { label: "Skill Analytics & ROI tracking", yes: true },
              ].map((item) => (
                <div
                  key={item.label}
                  className="flex items-center gap-2.5 rounded-lg border border-foreground/[0.04] bg-foreground/[0.02] px-3 py-2.5"
                >
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
                  <span className="text-[13px] text-foreground/80">{item.label}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Agent Templates */}
        <section className="mb-20">
          <div className="mb-10 text-center">
            <p className="mb-2 text-xs font-medium uppercase tracking-widest text-muted-foreground/60">
              Ready to launch
            </p>
            <h2 className="text-2xl font-bold tracking-tight">
              25 pre-built agent templates
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Spin up any role in seconds — or build your own from scratch.
            </p>
          </div>

          <div className="flex flex-wrap gap-2 justify-center">
            {TEMPLATES.map((t, i) => (
              <span
                key={t}
                className={`inline-flex items-center rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors ${
                  i % 5 === 0
                    ? "border-blue-500/20 bg-blue-500/10 text-blue-300"
                    : i % 5 === 1
                    ? "border-purple-500/20 bg-purple-500/10 text-purple-300"
                    : i % 5 === 2
                    ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-300"
                    : i % 5 === 3
                    ? "border-orange-500/20 bg-orange-500/10 text-orange-300"
                    : "border-cyan-500/20 bg-cyan-500/10 text-cyan-300"
                }`}
              >
                {t}
              </span>
            ))}
          </div>
        </section>

        {/* Architecture callout */}
        <section className="mb-20">
          <div className="grid gap-6 lg:grid-cols-2">
            <div className="rounded-2xl border border-foreground/[0.06] bg-card/80 p-7 backdrop-blur-sm">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10 border border-blue-500/20">
                <Cpu className="h-5 w-5 text-blue-400" />
              </div>
              <h3 className="mb-2 text-base font-semibold">Claude Code CLI Runtime</h3>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                Battle-tested headless Claude with native tool use, file editing, and shell access.
                Runs with your existing Claude Pro or Team subscription — no per-token costs.
                Swap to GPT-4o, Gemini, or local Ollama anytime.
              </p>
            </div>

            <div className="rounded-2xl border border-foreground/[0.06] bg-card/80 p-7 backdrop-blur-sm">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-purple-500/10 border border-purple-500/20">
                <BarChart3 className="h-5 w-5 text-purple-400" />
              </div>
              <h3 className="mb-2 text-base font-semibold">Skill Analytics & ROI</h3>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                The analytics dashboard shows time savings vs. manual baseline, ROI per skill,
                daily task volume, per-agent success rate, cost, and average duration.
                Set manual effort estimates to calculate real productivity gains.
              </p>
            </div>

            <div className="rounded-2xl border border-foreground/[0.06] bg-card/80 p-7 backdrop-blur-sm">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500/10 border border-emerald-500/20">
                <MessageSquare className="h-5 w-5 text-emerald-400" />
              </div>
              <h3 className="mb-2 text-base font-semibold">Telegram-Native</h3>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                Each agent gets its own Telegram bot with voice STT/TTS. Approve governance requests
                with a single button tap. Start tasks, read results, and get notified — all from
                Telegram on any device.
              </p>
            </div>

            <div className="rounded-2xl border border-foreground/[0.06] bg-card/80 p-7 backdrop-blur-sm">
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-orange-500/10 border border-orange-500/20">
                <Zap className="h-5 w-5 text-orange-400" />
              </div>
              <h3 className="mb-2 text-base font-semibold">Desktop Bridge</h3>
              <p className="text-[13px] leading-relaxed text-muted-foreground">
                Native macOS and Windows tray app connects your local desktop to the AI-Employee server.
                Agents can take screenshots, click, type, and interact with any local app — all
                authorized and audited.
              </p>
            </div>
          </div>
        </section>

        {/* CTA section */}
        <section className="mb-20">
          <div className="relative overflow-hidden rounded-2xl border border-blue-500/20 bg-gradient-to-br from-blue-500/10 via-cyan-500/5 to-transparent p-10 text-center">
            <div
              className="pointer-events-none absolute inset-0 opacity-30"
              style={{
                background:
                  "radial-gradient(ellipse 60% 80% at 50% 100%, hsl(217 91% 60% / 0.25), transparent)",
              }}
            />
            <h2 className="relative mb-3 text-2xl font-bold tracking-tight">
              Ready to deploy your AI team?
            </h2>
            <p className="relative mb-8 text-sm text-muted-foreground">
              Self-hosted in under 5 minutes. One command, no vendor lock-in.
            </p>

            <div className="relative flex flex-wrap items-center justify-center gap-3">
              <Link
                href="/login"
                className="inline-flex items-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/25 transition-all hover:bg-primary/90"
              >
                Open Platform
                <ArrowRight className="h-4 w-4" />
              </Link>
              <a
                href="https://github.com/greeves89/AI-Employee"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-xl border border-foreground/[0.12] bg-background/50 px-6 py-3 text-sm font-medium transition-colors hover:bg-background/80"
              >
                <Github className="h-4 w-4" />
                Star on GitHub
              </a>
            </div>

            <div className="relative mt-6 font-mono text-xs text-muted-foreground/60">
              git clone https://github.com/greeves89/AI-Employee.git &amp;&amp; ./scripts/setup.sh
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-foreground/[0.06] py-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 sm:flex-row">
          <div className="flex items-center gap-2">
            <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-gradient-to-br from-blue-500 to-cyan-400">
              <Bot className="h-3 w-3 text-white" />
            </div>
            <span className="text-sm font-medium">AI Employee</span>
            <span className="text-xs text-muted-foreground/50">· Fair-Code License</span>
          </div>

          <div className="flex items-center gap-4 text-xs text-muted-foreground/60">
            <a
              href="https://github.com/greeves89/AI-Employee"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 transition-colors hover:text-muted-foreground"
            >
              <Github className="h-3.5 w-3.5" />
              GitHub
            </a>
            <Link href="/login" className="transition-colors hover:text-muted-foreground">
              Sign In
            </Link>
            <Link href="/register" className="transition-colors hover:text-muted-foreground">
              Register
            </Link>
          </div>

          <p className="text-[11px] text-muted-foreground/40">
            *DSGVO: use local models for full compliance
          </p>
        </div>
      </footer>
    </div>
  );
}
