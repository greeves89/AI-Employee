/** Nutzer löschen mit Rückfrage zu seinen Agenten (#892).
 *
 * Besitzt der Nutzer noch Agenten, lehnt der Server das einfache Löschen ab
 * (409) — vorher gab es an dieser Stelle einen HTTP 500. Hier wird dann
 * nachgefragt: Agenten auf den Admin übertragen oder samt Daten löschen.
 */

import * as api from "@/lib/api";
import type { ConfirmOptions } from "@/components/ui/dialog-provider";

type Confirm = (opts: ConfirmOptions) => Promise<boolean>;

/** `true`, wenn der Nutzer gelöscht wurde; `false` bei Abbruch. */
export async function nutzerLoeschenMitRueckfrage(
  user: { id: string; name: string; email: string },
  confirm: Confirm,
): Promise<boolean> {
  const ok = await confirm({
    title: `Nutzer „${user.name}“ löschen?`,
    message: `${user.email} — das lässt sich nicht rückgängig machen.`,
    variant: "destructive",
    confirmLabel: "Löschen",
    cancelLabel: "Abbrechen",
  });
  if (!ok) return false;

  try {
    await api.deleteUser(user.id);
    return true;
  } catch (e) {
    if (!(e instanceof api.NutzerHatAgentenError)) throw e;
    const namen = e.agenten.map((a) => a.name).join(", ");

    const uebertragen = await confirm({
      title: `${user.name} besitzt noch ${e.agenten.length} Agent(en)`,
      message: `${namen}. Auf dich übertragen? Chats, Gedächtnis und Arbeitsordner bleiben dann erhalten.`,
      confirmLabel: "Auf mich übertragen",
      cancelLabel: "Nein, Agenten löschen …",
    });
    if (uebertragen) {
      await api.deleteUser(user.id, "uebertragen");
      return true;
    }

    const loeschen = await confirm({
      title: "Agenten samt Daten löschen?",
      message: `${namen} werden mit allen Chats, Erinnerungen und Arbeitsordnern endgültig gelöscht.`,
      variant: "destructive",
      confirmLabel: "Endgültig löschen",
      cancelLabel: "Abbrechen",
    });
    if (!loeschen) return false;
    await api.deleteUser(user.id, "loeschen");
    return true;
  }
}
