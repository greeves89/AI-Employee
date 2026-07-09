"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/** Renders assistant markdown (GFM) as clean prose. Shared by the agent chat and
 *  the task live/replay output so tokenized streams read as formatted text — lists,
 *  code, tables — instead of raw markdown with visible backticks and dashes. */
export const MarkdownContent = memo(function MarkdownContent({
  content,
  className,
}: {
  content: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "prose prose-sm dark:prose-invert max-w-none break-words leading-relaxed",
        "text-foreground/80",
        "[&_h1]:text-base [&_h1]:font-bold [&_h1]:mt-3 [&_h1]:mb-1.5 [&_h1]:text-foreground",
        "[&_h2]:text-sm [&_h2]:font-bold [&_h2]:mt-2.5 [&_h2]:mb-1 [&_h2]:text-foreground",
        "[&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:text-foreground/90",
        "[&_p]:my-1.5 [&_p]:leading-relaxed",
        "[&_p:first-child]:mt-0 [&_p:last-child]:mb-0",
        "[&_ul]:my-1.5 [&_ul]:pl-4 [&_ul]:space-y-0.5",
        "[&_ol]:my-1.5 [&_ol]:pl-4 [&_ol]:space-y-0.5",
        "[&_li]:text-sm [&_li]:text-foreground/80",
        "[&_strong]:font-semibold [&_strong]:text-foreground",
        "[&_code]:rounded [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-xs [&_code]:font-mono [&_code]:bg-muted [&_code]:text-amber-600 dark:[&_code]:text-amber-300",
        "[&_pre]:rounded-md [&_pre]:p-3 [&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:text-xs [&_pre]:bg-muted/80 dark:[&_pre]:bg-muted/40 [&_pre]:border [&_pre]:border-border",
        "[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-muted-foreground",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:my-2 [&_blockquote]:text-muted-foreground",
        "[&_hr]:my-3 [&_hr]:border-border",
        "[&_table]:my-2 [&_table]:text-xs",
        "[&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold [&_th]:border-b [&_th]:border-border [&_th]:text-foreground/80",
        "[&_td]:px-2 [&_td]:py-1 [&_td]:border-b [&_td]:border-border [&_td]:text-muted-foreground",
        "[&_a]:text-blue-500 dark:[&_a]:text-blue-400 [&_a]:underline [&_a]:underline-offset-2",
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
});
