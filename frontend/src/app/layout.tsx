import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { ThemeProvider } from "@/components/theme-provider";
import { AuthGuard } from "@/components/auth/auth-guard";
import { DialogProvider } from "@/components/ui/dialog-provider";
import { PwaRegistrar } from "@/components/pwa-registrar";
import { cn } from "@/lib/utils";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Employee",
  description: "Autonomous Claude Code Agents in Docker",
  // PWA: installierbar auf iOS/Android und als eigenes Fenster am Rechner.
  manifest: "/manifest.json",
  appleWebApp: { capable: true, statusBarStyle: "black-translucent", title: "AI Employee" },
  icons: { icon: "/favicon.png", apple: "/favicon.png" },
};

export const viewport: Viewport = {
  themeColor: "#0a0a0a",
  // Ohne viewport-fit deckt die installierte App auf iPhones die Aussparung nicht ab.
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem("theme");if(t==="light"){document.documentElement.classList.remove("dark")}else{document.documentElement.classList.add("dark")}}catch(e){}})()`,
          }}
        />
      </head>
      <body
        className={cn(
          GeistSans.variable,
          GeistMono.variable,
          "min-h-screen bg-background font-sans antialiased"
        )}
      >
        <PwaRegistrar />
        <ThemeProvider>
          <DialogProvider>
            <AuthGuard>{children}</AuthGuard>
          </DialogProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
