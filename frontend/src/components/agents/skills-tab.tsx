"use client";

import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Sparkles,
  Plus,
  Pencil,
  Trash2,
  ChevronDown,
  Code2,
  RefreshCw,
  Save,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

const API = process.env.NEXT_PUBLIC_API_URL || "";

interface Skill {
  name: string;
  description: string;
  content: string;
}

interface SkillsTabProps {
  agentId: string;
}

export function SkillsTab({ agentId }: SkillsTabProps) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formContent, setFormContent] = useState("");
  const [saving, setSaving] = useState(false);

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/v1/agents/${agentId}/skills`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setSkills(data);
      }
    } catch {
      // ignore
    }
    setLoading(false);
  }, [agentId]);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const resetForm = () => {
    setFormName("");
    setFormDescription("");
    setFormContent("");
    setShowForm(false);
    setEditingSkill(null);
  };

  const startEdit = (skill: Skill) => {
    setEditingSkill(skill.name);
    setFormName(skill.name);
    setFormDescription(skill.description);
    setFormContent(skill.content);
    setShowForm(true);
  };

  const handleSave = async () => {
    if (!formName.trim() || !formDescription.trim()) return;
    setSaving(true);
    try {
      const payload = {
        name: formName.trim().toLowerCase().replace(/\s+/g, "-"),
        description: formDescription.trim(),
        content: formContent.trim(),
      };

      const isEdit = editingSkill !== null;
      const url = isEdit
        ? `${API}/api/v1/agents/${agentId}/skills/${editingSkill}`
        : `${API}/api/v1/agents/${agentId}/skills`;

      const res = await fetch(url, {
        method: isEdit ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        resetForm();
        fetchSkills();
      }
    } catch {
      // ignore
    }
    setSaving(false);
  };

  const handleDelete = async (name: string) => {
    try {
      const res = await fetch(
        `${API}/api/v1/agents/${agentId}/skills/${name}`,
        { method: "DELETE", credentials: "include" }
      );
      if (res.ok) {
        setSkills((prev) => prev.filter((s) => s.name !== name));
        if (editingSkill === name) resetForm();
      }
    } catch {
      // ignore
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles className="h-5 w-5 text-amber-400" />
          <h2 className="text-sm font-semibold">Skills</h2>
          <span className="text-xs text-muted-foreground">
            ({skills.length} {skills.length === 1 ? "skill" : "skills"})
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={fetchSkills}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          >
            <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
            Refresh
          </button>
          <button
            onClick={() => {
              resetForm();
              setShowForm(true);
            }}
            className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 inline-flex items-center gap-1.5"
          >
            <Plus className="h-3.5 w-3.5" />
            Add Skill
          </button>
        </div>
      </div>

      {/* Description */}
      <p className="text-xs text-muted-foreground/70">
        Skills are custom instructions stored as{" "}
        <code className="text-[11px] px-1 py-0.5 rounded bg-foreground/[0.04] font-mono">
          .claude/skills/&lt;name&gt;/SKILL.md
        </code>{" "}
        files. The agent can invoke them as slash commands.
      </p>

      {/* Add/Edit Form */}
      <AnimatePresence>
        {showForm && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm p-5 space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">
                  {editingSkill ? "Edit Skill" : "New Skill"}
                </h3>
                <button
                  onClick={resetForm}
                  className="p-1 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 block mb-1.5">
                    Name
                  </label>
                  <input
                    type="text"
                    value={formName}
                    onChange={(e) => setFormName(e.target.value)}
                    placeholder="e.g. deploy-to-prod"
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <p className="text-[10px] text-muted-foreground/50 mt-1">
                    Lowercase with hyphens. Used as the directory name.
                  </p>
                </div>

                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 block mb-1.5">
                    Description
                  </label>
                  <input
                    type="text"
                    value={formDescription}
                    onChange={(e) => setFormDescription(e.target.value)}
                    placeholder="What this skill does..."
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                </div>

                <div>
                  <label className="text-[11px] font-medium text-muted-foreground/70 block mb-1.5">
                    Instructions (Markdown)
                  </label>
                  <textarea
                    value={formContent}
                    onChange={(e) => setFormContent(e.target.value)}
                    placeholder="Step-by-step instructions for the agent..."
                    rows={8}
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] px-3.5 py-2.5 text-sm font-mono placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-primary resize-y"
                  />
                </div>
              </div>

              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={resetForm}
                  className="rounded-xl px-4 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-foreground/[0.04]"
                >
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving || !formName.trim() || !formDescription.trim()}
                  className="rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 inline-flex items-center gap-1.5 disabled:opacity-50"
                >
                  <Save className="h-3.5 w-3.5" />
                  {saving ? "Saving..." : editingSkill ? "Update" : "Create"}
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Skills list */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <RefreshCw className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : skills.length === 0 && !showForm ? (
        <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
          <Sparkles className="h-10 w-10 mb-3 opacity-20" />
          <p className="text-sm font-medium">No skills defined</p>
          <p className="text-xs mt-1">
            Add custom skills to give this agent specialized capabilities.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {skills.map((skill, i) => {
            const isExpanded = expandedSkill === skill.name;
            return (
              <motion.div
                key={skill.name}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
                className="group rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden"
              >
                <div
                  className="flex items-center gap-3 p-4 cursor-pointer"
                  onClick={() =>
                    setExpandedSkill(isExpanded ? null : skill.name)
                  }
                >
                  <div className="flex items-center justify-center h-8 w-8 rounded-lg bg-amber-500/10">
                    <Code2 className="h-4 w-4 text-amber-400" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{skill.name}</span>
                      <span className="inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-medium bg-amber-500/10 text-amber-400 border-amber-500/20">
                        skill
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5 truncate">
                      {skill.description}
                    </p>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        startEdit(skill);
                      }}
                      className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                      title="Edit"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(skill.name);
                      }}
                      className="p-1.5 rounded-lg text-muted-foreground hover:text-red-400 hover:bg-accent transition-colors"
                      title="Delete"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>

                  <ChevronDown
                    className={cn(
                      "h-4 w-4 text-muted-foreground transition-transform",
                      isExpanded && "rotate-180"
                    )}
                  />
                </div>

                {/* Expanded content */}
                <AnimatePresence>
                  {isExpanded && (
                    <motion.div
                      initial={{ height: 0 }}
                      animate={{ height: "auto" }}
                      exit={{ height: 0 }}
                      className="overflow-hidden"
                    >
                      <div className="px-4 pb-4 pt-0">
                        <div className="rounded-lg border border-foreground/[0.06] bg-foreground/[0.02] p-3">
                          <pre className="text-xs text-muted-foreground whitespace-pre-wrap font-mono leading-relaxed">
                            {skill.content || "(no instructions)"}
                          </pre>
                        </div>
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })}
        </div>
      )}
    </div>
  );
}
