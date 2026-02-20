"use client";

import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

pdfjs.GlobalWorkerOptions.workerSrc = `//unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

interface PdfViewerProps {
  fileUrl: string;
}

export default function PdfViewer({ fileUrl }: PdfViewerProps) {
  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [loading, setLoading] = useState(true);

  return (
    <div className="flex flex-col h-full">
      {/* Page controls */}
      {numPages > 1 && (
        <div className="flex items-center justify-center gap-3 py-2 border-b border-foreground/[0.06] shrink-0">
          <button
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            disabled={currentPage <= 1}
            className="flex h-6 w-6 items-center justify-center rounded-md hover:bg-foreground/[0.06] disabled:opacity-30 transition-colors"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <span className="text-[11px] tabular-nums text-muted-foreground">
            {currentPage} / {numPages}
          </span>
          <button
            onClick={() => setCurrentPage((p) => Math.min(numPages, p + 1))}
            disabled={currentPage >= numPages}
            className="flex h-6 w-6 items-center justify-center rounded-md hover:bg-foreground/[0.06] disabled:opacity-30 transition-colors"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* PDF content */}
      <div className="flex-1 overflow-auto flex justify-center p-4 bg-foreground/[0.02]">
        {loading && (
          <div className="flex items-center justify-center absolute inset-0">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        <Document
          file={fileUrl}
          onLoadSuccess={({ numPages: n }) => {
            setNumPages(n);
            setLoading(false);
          }}
          onLoadError={() => setLoading(false)}
          loading=""
          options={{
            cMapUrl: `https://unpkg.com/pdfjs-dist@${pdfjs.version}/cmaps/`,
            cMapPacked: true,
          }}
        >
          <Page
            pageNumber={currentPage}
            width={700}
            className="shadow-lg rounded-lg overflow-hidden"
          />
        </Document>
      </div>
    </div>
  );
}
