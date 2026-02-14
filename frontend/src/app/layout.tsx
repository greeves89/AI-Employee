import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Sidebar } from "@/components/layout/sidebar";
import { ThemeProvider } from "@/components/theme-provider";
import { cn } from "@/lib/utils";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Employee",
  description: "Autonomous Claude Code Agents in Docker",
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
        <ThemeProvider>
          <div className="flex min-h-screen">
            <Sidebar />
            <main className="ml-[260px] flex-1 min-h-screen">{children}</main>
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
