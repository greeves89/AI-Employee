import { redirect } from "next/navigation";

/** Früher eine zweite, teils englische Zeitplan-Oberfläche neben der unter
 *  „Aufgaben → Zeitpläne“. Es gibt nur noch eine (#901) — alte Links und
 *  der Sprung aus dem Kalender (``?schedule=<id>``) landen dort. */
export default async function SchedulesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const id = typeof params.schedule === "string" ? params.schedule : "";
  redirect(
    id ? `/tasks?ansicht=zeitplaene&schedule=${encodeURIComponent(id)}` : "/tasks?ansicht=zeitplaene"
  );
}
