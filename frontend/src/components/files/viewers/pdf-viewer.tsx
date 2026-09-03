"use client";

import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { ChevronLeft, ChevronRight, FileWarning, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

// Der Arbeiter kam bis 1.228.0 von unpkg.com. Ein Browser darf einen Worker
// aber nicht von einem fremden Ursprung starten — schlaegt das fehl, steht
// pdf.js ohne `messageHandler` da und wirft beim ersten Zugriff. Das riss die
// GANZE Agentenseite mit („This page couldn't load"), gemeldet am 18.08.2026.
// `new URL(..., import.meta.url)` laesst den Bundler die Datei aus
// node_modules mitliefern: eigener Ursprung, und die Fassung passt immer zur
// eingebauten Bibliothek.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

//: Ausserhalb der Komponente, sonst entsteht bei jedem Zeichnen ein neues
//: Objekt und react-pdf laedt das Dokument erneut. Die Pfade zeigen auf den
//: eigenen Ursprung, siehe scripts/copy-pdf-assets.mjs.
const PDF_OPTIONEN = {
  cMapUrl: "/pdfjs/cmaps/",
  cMapPacked: true,
  standardFontDataUrl: "/pdfjs/standard_fonts/",
} as const;

interface PdfViewerProps {
  fileUrl: string;
}

export default function PdfViewer({ fileUrl }: PdfViewerProps) {
  const [numPages, setNumPages] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [loading, setLoading] = useState(true);
  //: Ein PDF kann beschaedigt oder passwortgeschuetzt sein. Bisher endete das
  //: in einer leeren Flaeche ohne jede Erklaerung.
  const [fehler, setFehler] = useState<string | null>(null);

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
        {fehler ? (
          <div className="flex flex-col items-center justify-center gap-1 py-10 text-center">
            <FileWarning className="h-6 w-6 text-muted-foreground/50" />
            <p className="text-sm font-medium">PDF kann nicht angezeigt werden</p>
            <p className="text-[11px] text-muted-foreground/60">{fehler}</p>
            <a
              href={fileUrl}
              className="mt-2 text-[11px] text-primary hover:underline"
              target="_blank"
              rel="noreferrer"
            >
              Stattdessen herunterladen
            </a>
          </div>
        ) : (
        <Document
          file={fileUrl}
          onLoadSuccess={({ numPages: n }) => {
            setNumPages(n);
            setLoading(false);
          }}
          onLoadError={(e) => {
            setLoading(false);
            setFehler(e instanceof Error ? e.message : "Unbekannter Fehler");
          }}
          loading=""
          options={PDF_OPTIONEN}
        >
          <Page
            pageNumber={currentPage}
            width={700}
            className="shadow-lg rounded-lg overflow-hidden"
          />
        </Document>
        )}
      </div>
    </div>
  );
}
