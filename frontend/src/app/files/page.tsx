"use client";

import { useState, useRef, useMemo } from "react";
import { motion } from "framer-motion";
import {
  FolderOpen, File, Folder, ChevronRight, Upload,
  Loader2, Bot, Download, RefreshCw,
  Search, ArrowUpDown, Clock, Hash, X,
} from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { FileEntry } from "@/lib/types";
import {
  FilePreview, FilePreviewEmpty,
  getFileColor, formatFileSize, formatModified, formatModifiedFull,
} from "@/components/files/file-preview";

const stateColors: Record<string, string> = {
  running: "bg-emerald-400",
  idle: "bg-emerald-400",
  working: "bg-blue-400",
  stopped: "bg-zinc-500",
  error: "bg-red-400",
  created: "bg-amber-400",
};

type SortMode = "name" | "date" | "size";

export default function FilesPage() {
  const { agents } = useAgents();
  const [expandedAgents, setExpandedAgents] = useState<Set<string>>(new Set());
  const [treeData, setTreeData] = useState<Record<string, FileEntry[]>>({});
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [selectedFile, setSelectedFile] = useState<{ agentId: string; path: string; entry?: FileEntry } | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("name");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadAgentRef = useRef<string>("");

  const runningAgents = agents.filter((a) => a.state !== "stopped");

  const loadDir = async (agentId: string, path: string) => {
    const key = `${agentId}:${path}`;
    try {
      const data = await api.getFiles(agentId, path);
      setTreeData((prev) => ({ ...prev, [key]: data.entries }));
    } catch {
      setTreeData((prev) => ({ ...prev, [key]: [] }));
    }
  };

  const toggleAgent = async (agentId: string) => {
    setExpandedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(agentId)) {
        next.delete(agentId);
      } else {
        next.add(agentId);
      }
      return next;
    });
    const key = `${agentId}:/workspace`;
    if (!treeData[key]) {
      await loadDir(agentId, "/workspace");
    }
  };

  const toggleDir = async (agentId: string, path: string) => {
    const dirKey = `${agentId}:${path}`;
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(dirKey)) {
        Array.from(next).forEach((k) => {
          if (k.startsWith(dirKey)) next.delete(k);
        });
      } else {
        next.add(dirKey);
      }
      return next;
    });
    if (!treeData[dirKey]) {
      await loadDir(agentId, path);
    }
  };

  const handleFileClick = (agentId: string, entry: FileEntry) => {
    setSelectedFile({ agentId, path: entry.path, entry });
  };

  const handleDownload = (agentId: string, path: string) => {
    const url = api.getFileDownloadUrl(agentId, path);
    window.open(url, "_blank");
  };

  const handleUploadClick = (agentId: string) => {
    uploadAgentRef.current = agentId;
    fileInputRef.current?.click();
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const agentId = uploadAgentRef.current;
    setUploading(agentId);
    try {
      await api.uploadFiles(agentId, "/workspace", files);
      await loadDir(agentId, "/workspace");
    } catch (e) {
      alert(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const refreshAgent = async (agentId: string) => {
    const keysToRefresh = Object.keys(treeData).filter((k) => k.startsWith(`${agentId}:`));
    await Promise.all(keysToRefresh.map((k) => {
      const path = k.substring(k.indexOf(":") + 1);
      return loadDir(agentId, path);
    }));
  };

  const sortEntries = (entries: FileEntry[]) =>
    [...entries].sort((a, b) => {
      if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
      if (sortMode === "date") return (b.modified || 0) - (a.modified || 0);
      if (sortMode === "size") return (b.size || 0) - (a.size || 0);
      return a.name.localeCompare(b.name);
    });

  const allFiles = useMemo(() => {
    if (!searchQuery.trim()) return [];
    const query = searchQuery.toLowerCase();
    const results: { agentId: string; agentName: string; entry: FileEntry }[] = [];
    for (const agent of runningAgents) {
      for (const [key, entries] of Object.entries(treeData)) {
        if (!key.startsWith(`${agent.id}:`)) continue;
        for (const entry of entries) {
          if (entry.type === "file" && entry.name.toLowerCase().includes(query)) {
            results.push({ agentId: agent.id, agentName: agent.name, entry });
          }
        }
      }
    }
    return sortEntries(results.map(r => r.entry)).map(e => {
      const match = results.find(r => r.entry.path === e.path);
      return match!;
    });
  }, [searchQuery, treeData, runningAgents, sortMode]);

  const renderTree = (agentId: string, path: string, depth: number): React.ReactNode => {
    const key = `${agentId}:${path}`;
    const entries = treeData[key];
    if (!entries) return null;

    return sortEntries(entries).map((entry) => {
      const isDir = entry.type === "directory";
      const dirKey = `${agentId}:${entry.path}`;
      const isExpanded = expandedDirs.has(dirKey);
      const isSelected = selectedFile?.agentId === agentId && selectedFile?.path === entry.path;

      return (
        <div key={entry.path}>
          <div
            className={cn(
              "flex items-center gap-2 py-1 px-3 hover:bg-foreground/[0.04] transition-colors cursor-pointer group",
              isSelected && "bg-primary/10 border-r-2 border-primary"
            )}
            style={{ paddingLeft: `${depth * 18 + 12}px` }}
            onClick={() => isDir ? toggleDir(agentId, entry.path) : handleFileClick(agentId, entry)}
          >
            {isDir ? (
              <ChevronRight className={cn(
                "h-3 w-3 text-muted-foreground/50 transition-transform duration-150 shrink-0",
                isExpanded && "rotate-90"
              )} />
            ) : (
              <span className="w-3 shrink-0" />
            )}
            {isDir ? (
              isExpanded ? (
                <FolderOpen className="h-3.5 w-3.5 text-amber-400/70 shrink-0" />
              ) : (
                <Folder className="h-3.5 w-3.5 text-amber-400/70 shrink-0" />
              )
            ) : (
              <File className={cn("h-3.5 w-3.5 shrink-0", getFileColor(entry.name))} />
            )}
            <span className="text-[12px] truncate flex-1 min-w-0">{entry.name}</span>
            {!isDir && entry.modified > 0 && (
              <span className="text-[10px] text-muted-foreground/30 tabular-nums shrink-0" title={formatModifiedFull(entry.modified)}>
                {formatModified(entry.modified)}
              </span>
            )}
            {!isDir && (
              <span className="text-[10px] text-muted-foreground/40 tabular-nums shrink-0 w-12 text-right">
                {formatFileSize(entry.size)}
              </span>
            )}
            {!isDir && (
              <button
                onClick={(e) => { e.stopPropagation(); handleDownload(agentId, entry.path); }}
                className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground/30 hover:text-foreground opacity-0 group-hover:opacity-100 transition-all shrink-0"
                title="Download"
              >
                <Download className="h-2.5 w-2.5" />
              </button>
            )}
          </div>
          {isDir && isExpanded && treeData[dirKey] && renderTree(agentId, entry.path, depth + 1)}
        </div>
      );
    });
  };

  return (
    <div>
      <Header title="Explorer" subtitle="Agent Workspace Dateien durchsuchen" />

      <motion.div
        className="px-8 py-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => handleUpload(e.target.files)}
        />

        {runningAgents.length === 0 ? (
          <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-foreground/[0.06] mb-4">
              <Bot className="h-7 w-7 text-muted-foreground" />
            </div>
            <h3 className="text-lg font-semibold mb-1.5">Keine aktiven Agents</h3>
            <p className="text-sm text-muted-foreground">
              Starte einen Agent, um seine Workspace-Dateien zu durchsuchen.
            </p>
          </div>
        ) : (
          <div className="flex gap-4 h-[calc(100vh-240px)]">
            {/* Tree panel */}
            <div className="w-[420px] shrink-0 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm flex flex-col overflow-hidden">
              {/* Search + Sort header */}
              <div className="border-b border-foreground/[0.06] px-3 py-2 space-y-2">
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/40" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Dateien suchen..."
                    className="w-full rounded-lg border border-foreground/[0.08] bg-foreground/[0.02] pl-8 pr-8 py-1.5 text-[12px] placeholder:text-muted-foreground/30 focus:outline-none focus:border-primary/30 transition-colors"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery("")}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground/40 hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  {(["name", "date", "size"] as SortMode[]).map((mode) => (
                    <button
                      key={mode}
                      onClick={() => setSortMode(mode)}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-medium transition-colors",
                        sortMode === mode
                          ? "bg-primary/10 text-primary"
                          : "text-muted-foreground/50 hover:text-muted-foreground hover:bg-foreground/[0.04]"
                      )}
                    >
                      {mode === "name" && <><Hash className="h-2.5 w-2.5" /> Name</>}
                      {mode === "date" && <><Clock className="h-2.5 w-2.5" /> Datum</>}
                      {mode === "size" && <><ArrowUpDown className="h-2.5 w-2.5" /> Groesse</>}
                    </button>
                  ))}
                  <span className="text-[10px] text-muted-foreground/30 ml-auto">
                    {runningAgents.length} Agent{runningAgents.length !== 1 && "s"}
                  </span>
                </div>
              </div>

              {/* File tree or search results */}
              <div className="flex-1 overflow-y-auto py-1 font-mono">
                {searchQuery.trim() ? (
                  allFiles.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-32 text-muted-foreground/30">
                      <Search className="h-5 w-5 mb-2" />
                      <span className="text-[11px]">Keine Treffer</span>
                    </div>
                  ) : (
                    <div className="py-1">
                      <div className="px-3 py-1 text-[10px] text-muted-foreground/40">
                        {allFiles.length} Treffer
                      </div>
                      {allFiles.map(({ agentId, agentName, entry }) => {
                        const isSelected = selectedFile?.agentId === agentId && selectedFile?.path === entry.path;
                        return (
                          <div
                            key={`${agentId}:${entry.path}`}
                            className={cn(
                              "flex items-center gap-2 py-1.5 px-3 hover:bg-foreground/[0.04] transition-colors cursor-pointer group",
                              isSelected && "bg-primary/10 border-r-2 border-primary"
                            )}
                            onClick={() => handleFileClick(agentId, entry)}
                          >
                            <File className={cn("h-3.5 w-3.5 shrink-0", getFileColor(entry.name))} />
                            <div className="flex flex-col flex-1 min-w-0">
                              <span className="text-[12px] truncate">{entry.name}</span>
                              <span className="text-[10px] text-muted-foreground/30 truncate">{agentName} &middot; {entry.path}</span>
                            </div>
                            {entry.modified > 0 && (
                              <span className="text-[10px] text-muted-foreground/30 tabular-nums shrink-0" title={formatModifiedFull(entry.modified)}>
                                {formatModified(entry.modified)}
                              </span>
                            )}
                            <span className="text-[10px] text-muted-foreground/40 tabular-nums shrink-0 w-12 text-right">
                              {formatFileSize(entry.size)}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )
                ) : (
                  runningAgents.map((agent) => {
                    const isExpanded = expandedAgents.has(agent.id);
                    const wsKey = `${agent.id}:/workspace`;
                    const isLoading = isExpanded && !treeData[wsKey];

                    return (
                      <div key={agent.id}>
                        <div
                          className="flex items-center gap-2 py-1.5 px-3 hover:bg-foreground/[0.04] transition-colors cursor-pointer group"
                          onClick={() => toggleAgent(agent.id)}
                        >
                          <ChevronRight className={cn(
                            "h-3 w-3 text-muted-foreground/50 transition-transform duration-150 shrink-0",
                            isExpanded && "rotate-90"
                          )} />
                          <div className="relative shrink-0">
                            <Bot className="h-4 w-4 text-primary" />
                            <div className={cn(
                              "absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full border border-card",
                              stateColors[agent.state] || "bg-zinc-500"
                            )} />
                          </div>
                          <span className="text-[12px] font-medium truncate flex-1 min-w-0 font-sans">
                            {agent.name}
                          </span>
                          <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                            <button
                              onClick={(e) => { e.stopPropagation(); handleUploadClick(agent.id); }}
                              className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground/40 hover:text-foreground transition-colors"
                              title="Upload"
                            >
                              {uploading === agent.id ? (
                                <Loader2 className="h-2.5 w-2.5 animate-spin" />
                              ) : (
                                <Upload className="h-2.5 w-2.5" />
                              )}
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); refreshAgent(agent.id); }}
                              className="flex h-5 w-5 items-center justify-center rounded text-muted-foreground/40 hover:text-foreground transition-colors"
                              title="Refresh"
                            >
                              <RefreshCw className="h-2.5 w-2.5" />
                            </button>
                          </div>
                          {isLoading && (
                            <Loader2 className="h-3 w-3 animate-spin text-muted-foreground/40 shrink-0" />
                          )}
                        </div>
                        {isExpanded && treeData[wsKey] && renderTree(agent.id, "/workspace", 1)}
                        {isExpanded && treeData[wsKey]?.length === 0 && (
                          <div className="py-2 pl-12 text-[11px] text-muted-foreground/40 font-sans">
                            Leerer Workspace
                          </div>
                        )}
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            {/* File preview - uses shared component */}
            <div className="flex-1 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm flex flex-col overflow-hidden">
              {selectedFile ? (
                <FilePreview
                  key={`${selectedFile.agentId}:${selectedFile.path}`}
                  fileUrl={api.getFileDownloadUrl(selectedFile.agentId, selectedFile.path)}
                  filePath={selectedFile.path}
                  fileSize={selectedFile.entry?.size}
                  fileModified={selectedFile.entry?.modified}
                  onDownload={() => handleDownload(selectedFile.agentId, selectedFile.path)}
                />
              ) : (
                <FilePreviewEmpty />
              )}
            </div>
          </div>
        )}
      </motion.div>
    </div>
  );
}
