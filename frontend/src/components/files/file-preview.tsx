"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import dynamic from "next/dynamic";
import {
  Download, Loader2, File, FileText, Code,
  Image as ImageIcon, Eye, FileCode,
} from "lucide-react";
import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Lazy-load heavy document viewers (SSR disabled)
const PdfViewer = dynamic(() => import("./viewers/pdf-viewer"), { ssr: false });
const DocxViewer = dynamic(() => import("./viewers/docx-viewer"), { ssr: false });
const XlsxViewer = dynamic(() => import("./viewers/xlsx-viewer"), { ssr: false });

// ── File type detection ──────────────────────────────────────────────

const imageExtensions = new Set(["png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp"]);
const markdownExtensions = new Set(["md", "mdx"]);
const htmlExtensions = new Set(["html", "htm"]);
const pdfExtensions = new Set(["pdf"]);
const docxExtensions = new Set(["docx"]);
const xlsxExtensions = new Set(["xlsx", "xls"]);
const binaryExtensions = new Set([
  "pptx", "zip", "tar", "gz", "7z", "rar", "exe", "bin", "dll",
  "so", "dylib", "woff", "woff2", "ttf", "eot", "mp3", "mp4",
  "avi", "mov", "wav", "dmg", "iso",
]);

export function getFileExt(path: string): string {
  return path.split(".").pop()?.toLowerCase() || "";
}

export function isPreviewable(path: string): boolean {
  const ext = getFileExt(path);
  return !binaryExtensions.has(ext);
}

export function needsArrayBuffer(path: string): boolean {
  const ext = getFileExt(path);
  return docxExtensions.has(ext) || xlsxExtensions.has(ext);
}

export function needsBlobUrl(path: string): boolean {
  const ext = getFileExt(path);
  return pdfExtensions.has(ext);
}

export { imageExtensions, markdownExtensions, binaryExtensions, htmlExtensions, pdfExtensions, docxExtensions, xlsxExtensions };

// ── File color map ───────────────────────────────────────────────────

const fileColorMap: Record<string, string> = {
  py: "text-blue-400", ts: "text-blue-400", tsx: "text-cyan-400",
  js: "text-amber-400", jsx: "text-amber-400", json: "text-emerald-400",
  md: "text-zinc-400", html: "text-orange-400", htm: "text-orange-400",
  css: "text-violet-400", sh: "text-emerald-400",
  yml: "text-red-400", yaml: "text-red-400", sql: "text-blue-400",
  txt: "text-zinc-400", pptx: "text-orange-400", docx: "text-blue-400",
  xlsx: "text-emerald-400", xls: "text-emerald-400", pdf: "text-red-400",
};

export function getFileColor(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  return fileColorMap[ext] || "text-muted-foreground";
}

// ── Size + date formatting ───────────────────────────────────────────

export function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(i > 0 ? 1 : 0)} ${sizes[i]}`;
}

export function formatModified(timestamp: number): string {
  if (!timestamp) return "";
  const date = new Date(timestamp * 1000);
  const now = Date.now();
  const diff = now - date.getTime();
  if (diff < 60000) return "gerade";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`;
  if (diff < 604800000) return `${Math.floor(diff / 86400000)}d`;
  return date.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" });
}

export function formatModifiedFull(timestamp: number): string {
  if (!timestamp) return "";
  const date = new Date(timestamp * 1000);
  return date.toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

// ── Main Preview Component ───────────────────────────────────────────

interface FilePreviewProps {
  fileUrl: string;
  filePath: string;
  fileSize?: number;
  fileModified?: number;
  onDownload?: () => void;
}

type HtmlTab = "rendered" | "source";

export function FilePreview({
  fileUrl,
  filePath,
  fileSize,
  fileModified,
  onDownload,
}: FilePreviewProps) {
  const ext = getFileExt(filePath);
  const fileName = filePath.split("/").pop() || "";
  const [textContent, setTextContent] = useState<string | null>(null);
  const [arrayBuffer, setArrayBuffer] = useState<ArrayBuffer | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [htmlTab, setHtmlTab] = useState<HtmlTab>("rendered");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setTextContent(null);
    setArrayBuffer(null);
    setBlobUrl(null);
    setHtmlTab("rendered");

    const load = async () => {
      try {
        if (imageExtensions.has(ext) || binaryExtensions.has(ext)) {
          // Images use <img src> directly, binary shows download
          setLoading(false);
          return;
        }

        const res = await fetch(fileUrl, { credentials: "include" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        if (pdfExtensions.has(ext)) {
          const blob = await res.blob();
          if (!cancelled) setBlobUrl(URL.createObjectURL(blob));
        } else if (docxExtensions.has(ext) || xlsxExtensions.has(ext)) {
          const buf = await res.arrayBuffer();
          if (!cancelled) setArrayBuffer(buf);
        } else {
          const text = await res.text();
          if (!cancelled) setTextContent(text);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Fehler beim Laden");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [fileUrl, ext]);

  // ── Header ──
  const header = (
    <div className="border-b border-foreground/[0.06] px-4 py-2.5 flex items-center gap-2 shrink-0">
      {imageExtensions.has(ext) ? (
        <ImageIcon className="h-3.5 w-3.5 text-violet-400" />
      ) : pdfExtensions.has(ext) ? (
        <FileText className="h-3.5 w-3.5 text-red-400" />
      ) : htmlExtensions.has(ext) ? (
        <FileCode className="h-3.5 w-3.5 text-orange-400" />
      ) : (
        <Code className={cn("h-3.5 w-3.5", getFileColor(fileName))} />
      )}
      <span className="text-xs font-mono text-muted-foreground truncate">
        {filePath}
      </span>
      {fileSize != null && (
        <span className="text-[10px] text-muted-foreground/40 tabular-nums shrink-0 ml-2">
          {formatFileSize(fileSize)}
        </span>
      )}
      {fileModified != null && fileModified > 0 && (
        <span className="text-[10px] text-muted-foreground/40 tabular-nums shrink-0" title={formatModifiedFull(fileModified)}>
          {formatModifiedFull(fileModified)}
        </span>
      )}

      {/* HTML tab switcher */}
      {htmlExtensions.has(ext) && !loading && textContent !== null && (
        <div className="flex items-center gap-0.5 ml-2 rounded-md bg-foreground/[0.04] p-0.5">
          <button
            onClick={() => setHtmlTab("rendered")}
            className={cn(
              "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium transition-colors",
              htmlTab === "rendered" ? "bg-primary/10 text-primary" : "text-muted-foreground/50 hover:text-muted-foreground"
            )}
          >
            <Eye className="h-2.5 w-2.5" /> Rendered
          </button>
          <button
            onClick={() => setHtmlTab("source")}
            className={cn(
              "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium transition-colors",
              htmlTab === "source" ? "bg-primary/10 text-primary" : "text-muted-foreground/50 hover:text-muted-foreground"
            )}
          >
            <FileCode className="h-2.5 w-2.5" /> HTML
          </button>
        </div>
      )}

      {onDownload && (
        <button
          onClick={onDownload}
          className="ml-auto flex h-6 items-center gap-1.5 rounded-lg px-2 text-[11px] text-muted-foreground hover:text-foreground hover:bg-foreground/[0.06] transition-colors shrink-0"
        >
          <Download className="h-3 w-3" />
          Download
        </button>
      )}
    </div>
  );

  // ── Content ──
  const content = loading ? (
    <div className="flex items-center justify-center h-full">
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
    </div>
  ) : error ? (
    <div className="flex flex-col items-center justify-center h-full text-red-400/60 gap-2">
      <span className="text-sm">{error}</span>
    </div>
  ) : imageExtensions.has(ext) ? (
    // Image preview
    <div className="flex items-center justify-center h-full p-4 bg-[repeating-conic-gradient(#80808015_0%_25%,transparent_0%_50%)] bg-[length:16px_16px]">
      <img
        src={fileUrl}
        alt={fileName}
        className="max-w-full max-h-full object-contain rounded-lg shadow-lg"
      />
    </div>
  ) : binaryExtensions.has(ext) ? (
    // Binary file - download only
    <div className="flex flex-col items-center justify-center h-full text-muted-foreground/40 gap-3">
      <File className="h-12 w-12" />
      <span className="text-sm font-medium">{fileName}</span>
      <span className="text-[11px]">
        {fileSize != null ? formatFileSize(fileSize) : ""} &middot; Binaerdatei
      </span>
      {onDownload && (
        <button
          onClick={onDownload}
          className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <Download className="h-4 w-4" /> Download
        </button>
      )}
    </div>
  ) : pdfExtensions.has(ext) && blobUrl ? (
    // PDF preview
    <PdfViewer fileUrl={blobUrl} />
  ) : docxExtensions.has(ext) && arrayBuffer ? (
    // DOCX preview
    <DocxViewer fileData={arrayBuffer} />
  ) : xlsxExtensions.has(ext) && arrayBuffer ? (
    // XLSX preview
    <XlsxViewer fileData={arrayBuffer} />
  ) : htmlExtensions.has(ext) && textContent !== null ? (
    // HTML - rendered or source
    htmlTab === "rendered" ? (
      <iframe
        srcDoc={textContent}
        className="w-full h-full border-0 bg-white rounded-b-xl"
        sandbox="allow-same-origin"
        title={fileName}
      />
    ) : (
      <pre className="p-4 text-[12px] font-mono leading-relaxed whitespace-pre-wrap text-foreground/90">
        {textContent}
      </pre>
    )
  ) : markdownExtensions.has(ext) && textContent !== null ? (
    // Markdown
    <div className="p-6 prose prose-sm dark:prose-invert max-w-none prose-headings:font-semibold prose-code:text-xs prose-pre:bg-foreground/5 prose-pre:text-foreground/80">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{textContent}</ReactMarkdown>
    </div>
  ) : textContent !== null ? (
    // Plain text / code
    <pre className="p-4 text-[12px] font-mono leading-relaxed whitespace-pre-wrap text-foreground/90">
      {textContent}
    </pre>
  ) : null;

  return (
    <>
      {header}
      <div className="flex-1 overflow-auto">{content}</div>
    </>
  );
}

// ── Empty state ──────────────────────────────────────────────────────

export function FilePreviewEmpty() {
  return (
    <>
      <div className="border-b border-foreground/[0.06] px-4 py-2.5 flex items-center gap-2">
        <FileText className="h-3.5 w-3.5 text-muted-foreground/50" />
        <span className="text-xs text-muted-foreground/50">Datei auswaehlen</span>
      </div>
      <div className="flex flex-col items-center justify-center flex-1 text-muted-foreground/30">
        <FileText className="h-10 w-10 mb-3" />
        <span className="text-sm">Datei auswaehlen</span>
      </div>
    </>
  );
}
