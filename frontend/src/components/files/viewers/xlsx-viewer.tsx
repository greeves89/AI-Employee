"use client";

import { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface XlsxViewerProps {
  fileData: ArrayBuffer;
}

export default function XlsxViewer({ fileData }: XlsxViewerProps) {
  const [html, setHtml] = useState("");
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheet, setActiveSheet] = useState("");
  const [loading, setLoading] = useState(true);
  const [workbookRef, setWorkbookRef] = useState<unknown>(null);

  useEffect(() => {
    if (!fileData) return;
    setLoading(true);

    import("xlsx").then((XLSX) => {
      const workbook = XLSX.read(fileData, { type: "array" });
      setWorkbookRef(workbook);
      setSheetNames(workbook.SheetNames);

      const firstSheet = workbook.SheetNames[0];
      setActiveSheet(firstSheet);

      const worksheet = workbook.Sheets[firstSheet];
      setHtml(XLSX.utils.sheet_to_html(worksheet, { editable: false }));
      setLoading(false);
    });
  }, [fileData]);

  const switchSheet = (name: string) => {
    if (!workbookRef) return;
    import("xlsx").then((XLSX) => {
      const wb = workbookRef as ReturnType<typeof XLSX.read>;
      const worksheet = wb.Sheets[name];
      setHtml(XLSX.utils.sheet_to_html(worksheet, { editable: false }));
      setActiveSheet(name);
    });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Sheet tabs */}
      {sheetNames.length > 1 && (
        <div className="flex items-center gap-1 px-3 py-2 border-b border-foreground/[0.06] shrink-0 overflow-x-auto">
          {sheetNames.map((name) => (
            <button
              key={name}
              onClick={() => switchSheet(name)}
              className={cn(
                "px-3 py-1 text-[11px] font-medium rounded-md whitespace-nowrap transition-colors",
                activeSheet === name
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground/50 hover:text-muted-foreground hover:bg-foreground/[0.04]"
              )}
            >
              {name}
            </button>
          ))}
        </div>
      )}

      {/* Table content */}
      <div
        className="flex-1 overflow-auto p-2
          [&_table]:w-full [&_table]:border-collapse
          [&_td]:border [&_td]:border-foreground/[0.08] [&_td]:px-2.5 [&_td]:py-1.5 [&_td]:text-[12px] [&_td]:font-mono
          [&_th]:border [&_th]:border-foreground/[0.08] [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-[11px] [&_th]:font-medium [&_th]:bg-foreground/[0.04]"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}
