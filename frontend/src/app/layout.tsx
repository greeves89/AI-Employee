import type { Metadata, Viewport } from "next";
import { Space_Grotesk, JetBrains_Mono } from "next/font/google";
import { ThemeProvider } from "@/components/theme-provider";
import { AuthGuard } from "@/components/auth/auth-guard";
import { DialogProvider } from "@/components/ui/dialog-provider";
import { PwaRegistrar } from "@/components/pwa-registrar";
import { ConciergeWidget } from "@/components/concierge/concierge-widget";
import { TutorialFenster } from "@/components/tutorials/tutorial-fenster";
import { FeedbackWidget } from "@/components/feedback/feedback-widget";
import { cn } from "@/lib/utils";
import "./globals.css";

// Schriften der lavalake-Konsole: Space Grotesk für Überschriften, JetBrains Mono
// für Daten. Next lädt sie beim Bauen herunter und liefert sie selbst aus —
// zur Laufzeit geht keine Anfrage an Google.
const display = Space_Grotesk({ subsets: ["latin"], weight: ["500", "600", "700"], variable: "--font-space-grotesk", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-jetbrains-mono", display: "swap" });

export const metadata: Metadata = {
  title: "AI Employee",
  description: "KI-Mitarbeiter, die selbstständig in eigenen Containern arbeiten",
  // PWA: installierbar auf iOS/Android und als eigenes Fenster am Rechner.
  manifest: "/manifest.json",
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "AI Employee" },
  icons: { icon: "/favicon.png", apple: "/favicon.png" },
};

export const viewport: Viewport = {
  themeColor: "#0e1a40",
  // Ohne viewport-fit deckt die installierte App auf iPhones die Aussparung nicht ab.
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="de" className="dark" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("theme");if(!t){t=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}if(t==="light"){document.documentElement.classList.remove("dark")}else{document.documentElement.classList.add("dark")}}catch(e){}})()`,
          }}
        />
      </head>
      <body
        className={cn(
          display.variable,
          mono.variable,
          "min-h-screen bg-background font-sans antialiased"
        )}
      >
        <PwaRegistrar />
        <ThemeProvider>
          <DialogProvider>
            <AuthGuard>
              {children}
              {/* Nur für Administratoren; blendet sich sonst selbst aus. */}
              <ConciergeWidget />
              {/* Klick-Tutorials: beim ersten Start von selbst, sonst aus Hilfe & FAQ. */}
              <TutorialFenster />
              {/* Feedback-Gedöns: auf jeder Route, blendet sich ohne Session aus. */}
              <FeedbackWidget />
            </AuthGuard>
          </DialogProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
