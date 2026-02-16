"use client";

import { useState, useRef } from "react";
import { motion } from "framer-motion";
import {
  FolderOpen, File, Folder, ChevronRight, Upload,
  Code, FileText, Loader2, Bot, Download, RefreshCw,
} from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import * as api from "@/lib/api";
import type { FileEntry } from "@/lib/types";

const fileColorMap: Record<string, string> = {
  py: "text-blue-400",
  ts: "text-blue-400",
  tsx: "text-cyan-400",
  js: "text-amber-400",
  jsx: "text-amber-400",
  json: "text-emerald-400",
  md: "text-zinc-400",
  html: "text-orange-400",
  css: "text-violet-400",
  sh: "text-emerald-400",
  yml: "text-red-400",
  yaml: "text-red-400",
  sql: "text-blue-400",
  txt: "text-zinc-400",
};

function getFileColor(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return fileColorMap[ext] || "text-muted-foreground";
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 0 ? 1 : 0)} ${sizes[i]}`;
}

const stateColors: Record<string, string> = {
  running: "bg-emerald-400",
  idle: "bg-emerald-400",
  working: "bg-blue-400",
  stopped: "bg-zinc-500",
  error: "bg-red-400",
  created: "bg-amber-400",
};

export default function FilesPage() {
  const { agents } = useAgents();
  const [expandedAgents, setExpandedAgents] = useState<Set<string>>(new Set());
  const [treeData, setTreeData] = useState<Record<string, FileEntry[]>>({});
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());
  const [selectedFile, setSelectedFile] = useState<{ agentId: string; path: string } | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
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

  const handleFileClick = async (agentId: string, path: string) => {
    setSelectedFile({ agentId, path });
    setLoadingContent(true);
    setFileContent(null);
    try {
      const url = api.getFileDownloadUrl(agentId, path);
      const res = await fetch(url);
      const text = await res.text();
      setFileContent(text);
    } catch {
      setFileContent("Error loading file");
    } finally {
      setLoadingContent(false);
    }
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
      return a.name.localeCompare(b.name);
    });

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
              isSelected && "bg-foreground/[0.06]"
            )}
            style={{ paddingLeft: `${depth * 18 + 12}px` }}
            onClick={() => isDir ? toggleDir(agentId, entry.path) : handleFileClick(agentId, entry.path)}
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
            {!isDir && (
              <span className="text-[10px] text-muted-foreground/40 tabular-nums shrink-0">
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
      <Header title="Files" subtitle="Browse agent workspace files" />

      <motion.div
        className="px-8 py-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Hidden file input for uploads */}
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
            <div className="w-[360px] shrink-0 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm flex flex-col overflow-hidden">
              <div className="border-b border-foreground/[0.06] px-4 py-2.5 flex items-center gap-2">
                <FolderOpen className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-xs font-medium">Workspaces</span>
                <span className="text-[10px] text-muted-foreground/50 ml-auto">
                  {runningAgents.length} Agent{runningAgents.length !== 1 && "s"}
                </span>
              </div>

              <div className="flex-1 overflow-y-auto py-1 font-mono">
                {runningAgents.map((agent) => {
                  const isExpanded = expandedAgents.has(agent.id);
                  const wsKey = `${agent.id}:/workspace`;
                  const isLoading = isExpanded && !treeData[wsKey];

                  return (
                    <div key={agent.id}>
                      {/* Agent root node */}
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

                        {/* Agent actions */}
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

                      {/* Agent workspace tree */}
                      {isExpanded && treeData[wsKey] && renderTree(agent.id, "/workspace", 1)}

                      {/* Empty workspace */}
                      {isExpanded && treeData[wsKey]?.length === 0 && (
                        <div className="py-2 pl-12 text-[11px] text-muted-foreground/40 font-sans">
                          Leerer Workspace
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* File preview */}
            <div className="flex-1 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm flex flex-col overflow-hidden">
              <div className="border-b border-foreground/[0.06] px-4 py-2.5 flex items-center gap-2">
                {selectedFile ? (
                  <>
                    <Code className={cn("h-3.5 w-3.5", getFileColor(selectedFile.path.split("/").pop() || ""))} />
                    <span className="text-xs font-mono text-muted-foreground truncate">
                      {selectedFile.path}
                    </span>
                    <button
                      onClick={() => handleDownload(selectedFile.agentId, selectedFile.path)}
                      className="ml-auto flex h-6 items-center gap-1.5 rounded-lg px-2 text-[11px] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors"
                    >
                      <Download className="h-3 w-3" />
                      Download
                    </button>
                  </>
                ) : (
                  <>
                    <FileText className="h-3.5 w-3.5 text-muted-foreground/50" />
                    <span className="text-xs text-muted-foreground/50">Datei auswaehlen</span>
                  </>
                )}
              </div>
              <div className="flex-1 overflow-auto">
                {loadingContent ? (
                  <div className="flex items-center justify-center h-full">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : fileContent !== null ? (
                  <pre className="p-4 text-[12px] font-mono leading-relaxed whitespace-pre-wrap text-foreground/90">
                    {fileContent}
                  </pre>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-muted-foreground/30">
                    <FileText className="h-10 w-10 mb-3" />
                    <span className="text-sm">Datei auswaehlen</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </motion.div>
    </div>
  );
}
