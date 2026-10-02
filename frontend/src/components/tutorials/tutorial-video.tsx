"use client";

import { cn } from "@/lib/utils";
import { tutorialPoster, tutorialUntertitel, tutorialVideo } from "@/lib/tutorials";

/** Ein Klick-Tutorial: Video mit Sprecher, Vorschaubild und zuschaltbaren Untertiteln. */
export function TutorialVideo({ id, preload = "metadata", className }: {
  id: string;
  preload?: "none" | "metadata";
  className?: string;
}) {
  return (
    <video
      key={id}
      src={tutorialVideo(id)}
      poster={tutorialPoster(id)}
      controls
      playsInline
      preload={preload}
      className={cn("aspect-video w-full border border-foreground/[0.08] bg-black", className)}
    >
      <track kind="subtitles" srcLang="de" label="Deutsch" src={tutorialUntertitel(id)} />
    </video>
  );
}
