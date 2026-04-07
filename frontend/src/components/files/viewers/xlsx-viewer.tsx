"use client";

import { useState, useEffect } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface XlsxViewerProps {
  fileData: ArrayBuffer;
}

export default function XlsxViewer({ fileData }: XlsxViewerProps) {
  const [rows, setRows] = useState<string[][]>([]);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheet, setActiveSheet] = useState("");
  const [loading, setLoading] = useState(true);
  const [workbookRef, setWorkbookRef] = useState<any>(null);

  const loadSheet = async (workbook: any, name: string) => {
    const worksheet = workbook.getWorksheet(name);
    if (!worksheet) return;
    const data: string[][] = [];
    worksheet.eachRow({ includeEmpty: true }, (row: any) => {
      const cells: string[] = [];
      row.eachCell({ includeEmpty: true }, (cell: any) => {
        cells.push(cell.text ?? String(cell.value ?? ""));
      });
      data.push(cells);
    });
    setRows(data);
    setActiveSheet(name);
  };

  useEffect(() => {
    if (!fileData) return;
    setLoading(true);

    import("exceljs").then(async (ExcelJS) => {
      const workbook = new ExcelJS.Workbook();
      await workbook.xlsx.load(fileData);
      setWorkbookRef(workbook);

      const names = workbook.worksheets.map((ws: any) => ws.name);
      setSheetNames(names);

      if (names.length > 0) {
        await loadSheet(workbook, names[0]);
      }
      setLoading(false);
    });
  }, [fileData]);

  const switchSheet = (name: string) => {
    if (workbookRef) loadSheet(workbookRef, name);
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

      <div className="flex-1 overflow-auto p-2">
        <table className="w-full border-collapse">
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {row.map((cell, ci) => {
                  const Tag = ri === 0 ? "th" : "td";
                  return (
                    <Tag
                      key={ci}
                      className={cn(
                        "border border-foreground/[0.08] px-2.5 py-1.5 text-[12px] font-mono text-left",
                        ri === 0 && "text-[11px] font-medium bg-foreground/[0.04]"
                      )}
                    >
                      {cell}
                    </Tag>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
