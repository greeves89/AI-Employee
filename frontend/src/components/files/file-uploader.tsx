"use client";

import { useState, useRef, useCallback } from "react";
import { Upload, X, FileIcon, Loader2, CheckCircle2 } from "lucide-react";
import * as api from "@/lib/api";

interface FileUploaderProps {
  agentId: string;
  targetPath: string;
  onUploadComplete: () => void;
  onClose: () => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileUploader({
  agentId,
  targetPath,
  onUploadComplete,
  onClose,
}: FileUploaderProps) {
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback((newFiles: FileList | File[]) => {
    setFiles((prev) => [...prev, ...Array.from(newFiles)]);
    setError(null);
    setDone(false);
  }, []);

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.files.length > 0) {
        addFiles(e.dataTransfer.files);
      }
    },
    [addFiles]
  );

  const handleUpload = async () => {
    if (files.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      await api.uploadFiles(agentId, targetPath, files);
      setDone(true);
      setFiles([]);
      onUploadComplete();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="rounded-2xl border border-foreground/[0.06] bg-card/80 backdrop-blur-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-foreground/[0.06]">
        <div className="flex items-center gap-2">
          <Upload className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">Upload Files</h3>
          <span className="text-xs text-muted-foreground/60">to {targetPath}</span>
        </div>
        <button
          onClick={onClose}
          className="flex h-7 w-7 items-center justify-center rounded-lg hover:bg-foreground/[0.06] transition-colors"
        >
          <X className="h-4 w-4 text-muted-foreground" />
        </button>
      </div>

      {/* Drop Zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`m-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 transition-all ${
          dragOver
            ? "border-primary/50 bg-primary/5"
            : "border-foreground/[0.08] hover:border-foreground/[0.15] hover:bg-foreground/[0.02]"
        }`}
      >
        <Upload
          className={`h-8 w-8 mb-3 ${
            dragOver ? "text-primary" : "text-muted-foreground/40"
          }`}
        />
        <p className="text-sm text-muted-foreground">
          Drop files here or{" "}
          <span className="text-primary font-medium">browse</span>
        </p>
        <p className="mt-1 text-xs text-muted-foreground/50">
          Any file type supported
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {/* File List */}
      {files.length > 0 && (
        <div className="mx-4 mb-4 space-y-1.5">
          {files.map((file, i) => (
            <div
              key={`${file.name}-${i}`}
              className="flex items-center gap-3 rounded-lg bg-foreground/[0.03] px-3 py-2"
            >
              <FileIcon className="h-4 w-4 text-muted-foreground/60 shrink-0" />
              <span className="text-xs font-medium truncate flex-1">
                {file.name}
              </span>
              <span className="text-[10px] text-muted-foreground/50 shrink-0">
                {formatSize(file.size)}
              </span>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  removeFile(i);
                }}
                className="flex h-5 w-5 items-center justify-center rounded hover:bg-foreground/[0.08] transition-colors shrink-0"
              >
                <X className="h-3 w-3 text-muted-foreground" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Status + Actions */}
      <div className="flex items-center justify-between px-5 py-3 border-t border-foreground/[0.06]">
        <div>
          {error && (
            <p className="text-xs text-red-400">{error}</p>
          )}
          {done && (
            <p className="flex items-center gap-1.5 text-xs text-emerald-400">
              <CheckCircle2 className="h-3.5 w-3.5" />
              Upload complete
            </p>
          )}
          {!error && !done && files.length > 0 && (
            <p className="text-xs text-muted-foreground/60">
              {files.length} file{files.length > 1 ? "s" : ""} selected (
              {formatSize(files.reduce((sum, f) => sum + f.size, 0))})
            </p>
          )}
        </div>
        <button
          onClick={handleUpload}
          disabled={files.length === 0 || uploading}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-xs font-medium text-primary-foreground transition-all hover:brightness-110 disabled:opacity-40"
        >
          {uploading ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Upload className="h-3.5 w-3.5" />
          )}
          Upload
        </button>
      </div>
    </div>
  );
}
