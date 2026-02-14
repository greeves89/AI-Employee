"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import {
  FolderOpen, File, ChevronRight, ChevronUp, Upload,
  Cpu, Code, FileText, Loader2, FolderClosed,
} from "lucide-react";
import { useAgents } from "@/hooks/use-agents";
import { Header } from "@/components/layout/header";
import { cn } from "@/lib/utils";
import { formatBytes } from "@/lib/utils";
import * as api from "@/lib/api";
import type { FileEntry } from "@/lib/types";
import { FileUploader } from "@/components/files/file-uploader";

const fileIconMap: Record<string, string> = {
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
  return fileIconMap[ext] || "text-muted-foreground";
}

export default function FilesPage() {
  const { agents } = useAgents();
  const [selectedAgent, setSelectedAgent] = useState<string>("");
  const [currentPath, setCurrentPath] = useState("/workspace");
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [showUpload, setShowUpload] = useState(false);

  useEffect(() => {
    if (selectedAgent) {
      loadDirectory(currentPath);
    }
  }, [selectedAgent, currentPath]);

  const loadDirectory = async (path: string) => {
    setLoading(true);
    setFileContent(null);
    setSelectedFile("");
    try {
      const data = await api.getFiles(selectedAgent, path);
      setEntries(data.entries);
    } catch {
      setEntries([]);
    } finally {
      setLoading(false);
    }
  };

  const handleEntryClick = (entry: FileEntry) => {
    if (entry.type === "directory") {
      setCurrentPath(entry.path);
    } else {
      setSelectedFile(entry.path);
      loadFileContent(entry.path);
    }
  };

  const loadFileContent = async (path: string) => {
    try {
      const url = api.getFileDownloadUrl(selectedAgent, path);
      const res = await fetch(url);
      const text = await res.text();
      setFileContent(text);
    } catch {
      setFileContent("Error loading file");
    }
  };

  const navigateUp = () => {
    const parts = currentPath.split("/").filter(Boolean);
    if (parts.length > 1) {
      parts.pop();
      setCurrentPath("/" + parts.join("/"));
    }
  };

  const pathParts = currentPath.split("/").filter(Boolean);

  return (
    <div>
      <Header title="Files" subtitle="Browse agent workspace files" />

      <motion.div
        className="px-8 py-8"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        {/* Agent selector */}
        <div className="mb-5">
          <label className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-1.5">
            <Cpu className="h-3 w-3" />
            Agent Workspace
          </label>
          <select
            value={selectedAgent}
            onChange={(e) => {
              setSelectedAgent(e.target.value);
              setCurrentPath("/workspace");
              setFileContent(null);
              setSelectedFile("");
            }}
            className="rounded-xl border border-foreground/[0.08] bg-card/80 backdrop-blur-sm px-4 py-2.5 text-sm outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20 transition-all appearance-none min-w-[260px]"
          >
            <option value="">Select an agent...</option>
            {agents
              .filter((a) => a.state !== "stopped")
              .map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name} ({agent.id.slice(0, 8)})
                </option>
              ))}
          </select>
        </div>

        {selectedAgent ? (
          <div className="flex gap-4 h-[calc(100vh-240px)]">
            {/* File tree */}
            <div className="w-[340px] shrink-0 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm flex flex-col overflow-hidden">
              {/* Breadcrumb bar + Upload button */}
              <div className="border-b border-foreground/[0.06] px-4 py-2.5 flex items-center gap-1.5">
                <button
                  onClick={navigateUp}
                  disabled={currentPath === "/workspace"}
                  className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] disabled:opacity-30 transition-colors"
                >
                  <ChevronUp className="h-3.5 w-3.5" />
                </button>
                <div className="flex items-center gap-0.5 text-xs font-mono text-muted-foreground/70 overflow-hidden">
                  {pathParts.map((part, idx) => (
                    <span key={idx} className="flex items-center gap-0.5 shrink-0">
                      {idx > 0 && <ChevronRight className="h-3 w-3 shrink-0 opacity-40" />}
                      <button
                        onClick={() => setCurrentPath("/" + pathParts.slice(0, idx + 1).join("/"))}
                        className="hover:text-foreground transition-colors truncate max-w-[120px]"
                      >
                        {part}
                      </button>
                    </span>
                  ))}
                </div>
                <button
                  onClick={() => setShowUpload(!showUpload)}
                  className="ml-auto flex h-6 items-center gap-1.5 rounded-lg bg-primary/10 border border-primary/20 px-2.5 text-[11px] font-medium text-primary hover:bg-primary/20 transition-colors shrink-0"
                >
                  <Upload className="h-3 w-3" />
                  Upload
                </button>
              </div>

              {/* Upload panel */}
              {showUpload && (
                <div className="border-b border-foreground/[0.06]">
                  <FileUploader
                    agentId={selectedAgent}
                    targetPath={currentPath}
                    onUploadComplete={() => loadDirectory(currentPath)}
                    onClose={() => setShowUpload(false)}
                  />
                </div>
              )}

              {/* File list */}
              <div className="flex-1 overflow-y-auto">
                {loading ? (
                  <div className="flex items-center justify-center h-32 text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                  </div>
                ) : entries.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-32 text-muted-foreground/60">
                    <FolderOpen className="h-5 w-5 mb-1.5" />
                    <span className="text-xs">Empty directory</span>
                  </div>
                ) : (
                  entries.map((entry) => (
                    <button
                      key={entry.path}
                      onClick={() => handleEntryClick(entry)}
                      className={cn(
                        "w-full text-left px-4 py-2 text-[13px] flex items-center justify-between hover:bg-foreground/[0.04] transition-colors border-l-2",
                        selectedFile === entry.path
                          ? "bg-foreground/[0.06] border-l-primary text-foreground"
                          : "border-l-transparent"
                      )}
                    >
                      <span className="flex items-center gap-2.5 min-w-0">
                        {entry.type === "directory" ? (
                          <FolderClosed className="h-4 w-4 shrink-0 text-amber-400/70" />
                        ) : (
                          <File className={cn("h-4 w-4 shrink-0", getFileColor(entry.name))} />
                        )}
                        <span className="truncate">{entry.name}</span>
                      </span>
                      {entry.type === "file" && (
                        <span className="text-[10px] text-muted-foreground/50 tabular-nums shrink-0 ml-2">
                          {formatBytes(entry.size)}
                        </span>
                      )}
                    </button>
                  ))
                )}
              </div>
            </div>

            {/* File preview */}
            <div className="flex-1 rounded-xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm flex flex-col overflow-hidden">
              <div className="border-b border-foreground/[0.06] px-4 py-2.5 flex items-center gap-2">
                {selectedFile ? (
                  <>
                    <Code className={cn("h-3.5 w-3.5", getFileColor(selectedFile.split("/").pop() || ""))} />
                    <span className="text-xs font-mono text-muted-foreground truncate">
                      {selectedFile}
                    </span>
                  </>
                ) : (
                  <>
                    <FileText className="h-3.5 w-3.5 text-muted-foreground/50" />
                    <span className="text-xs text-muted-foreground/50">
                      Select a file to preview
                    </span>
                  </>
                )}
              </div>
              <div className="flex-1 overflow-auto">
                {fileContent !== null ? (
                  <pre className="p-4 text-[12px] font-mono leading-relaxed whitespace-pre-wrap text-foreground/90">
                    {fileContent}
                  </pre>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full text-muted-foreground/40">
                    <FileText className="h-8 w-8 mb-2" />
                    <span className="text-xs">No file selected</span>
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-foreground/[0.1] bg-card/30 p-16 text-center">
            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-foreground/[0.06] mb-4">
              <FolderOpen className="h-7 w-7 text-muted-foreground" />
            </div>
            <h3 className="text-lg font-semibold mb-1.5">Browse Workspace Files</h3>
            <p className="text-sm text-muted-foreground">
              Select an agent above to browse its workspace files.
            </p>
          </div>
        )}
      </motion.div>
    </div>
  );
}
