"""Tkinter desktop application for PlaylistBridge."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .auth_store import AuthStore, build_browser_auth
from .models import MatchStatus
from .spotify import parse_clipboard
from .storage import SessionRepository, TransferSession, default_data_dir
from .workflow import (
    WorkflowCancelled,
    hydrate_playlist,
    match_playlist,
    transfer_session,
    write_session_report,
)
from .youtube import YouTubeMusicAdapter


class PlaylistBridgeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("PlaylistBridge")
        self.geometry("1120x720")
        self.minsize(900, 560)

        self.data_dir = default_data_dir()
        self.repository = SessionRepository(self.data_dir / "sessions")
        self.auth_store = AuthStore(self.data_dir / "youtube_auth.bin")
        self.session: TransferSession | None = None
        self._events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._cancel = threading.Event()
        self._busy = False

        self.playlist_name = tk.StringVar(value="Brani che ti piacciono - Spotify")
        self.status_text = tk.StringVar(value="Incolla una playlist da Spotify per iniziare.")
        self.account_text = tk.StringVar()
        self.progress_value = tk.DoubleVar(value=0)
        self._build_ui()
        self._refresh_account_label()
        self.after(100, self._poll_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text="PlaylistBridge", font=("Segoe UI", 20, "bold"))
        title.pack(anchor="w")
        ttk.Label(
            outer,
            text="Trasferimento locale da Spotify a YouTube Music — nessun audio viene scaricato.",
        ).pack(anchor="w", pady=(0, 12))

        source = ttk.LabelFrame(outer, text="1. Importazione da Spotify", padding=10)
        source.pack(fill="x")
        ttk.Label(source, text="Nome playlist:").grid(row=0, column=0, sticky="w")
        ttk.Entry(source, textvariable=self.playlist_name, width=48).grid(
            row=0, column=1, sticky="ew", padx=8
        )
        self.import_button = ttk.Button(
            source, text="Importa dagli appunti", command=self.import_from_clipboard
        )
        self.import_button.grid(row=0, column=2, padx=4)
        self.open_session_button = ttk.Button(
            source, text="Apri sessione", command=self.open_session
        )
        self.open_session_button.grid(
            row=0, column=3, padx=4
        )
        source.columnconfigure(1, weight=1)
        ttk.Label(
            source,
            text="In Spotify Desktop: apri la playlist, clicca un brano, premi Ctrl+A e Ctrl+C.",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        auth = ttk.LabelFrame(outer, text="2. Collegamento a YouTube Music", padding=10)
        auth.pack(fill="x", pady=10)
        ttk.Label(auth, textvariable=self.account_text).pack(side="left")
        self.configure_auth_button = ttk.Button(
            auth, text="Configura accesso", command=self.show_auth_dialog
        )
        self.configure_auth_button.pack(
            side="right", padx=4
        )
        self.disconnect_auth_button = ttk.Button(
            auth, text="Disconnetti", command=self.disconnect_auth
        )
        self.disconnect_auth_button.pack(
            side="right", padx=4
        )

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 8))
        self.match_button = ttk.Button(
            actions, text="3. Analizza e abbina", command=self.start_matching
        )
        self.match_button.pack(side="left")
        self.transfer_button = ttk.Button(
            actions, text="4. Trasferisci playlist", command=self.start_transfer
        )
        self.transfer_button.pack(side="left", padx=8)
        self.report_button = ttk.Button(actions, text="Esporta report", command=self.export_report)
        self.report_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="Annulla", command=self.cancel_operation)
        self.cancel_button.pack(side="right")

        table_frame = ttk.Frame(outer)
        table_frame.pack(fill="both", expand=True)
        columns = ("position", "title", "artists", "confidence", "status", "youtube")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "position": "#",
            "title": "Titolo Spotify",
            "artists": "Artista",
            "confidence": "Confidenza",
            "status": "Stato",
            "youtube": "Scelta YouTube Music",
        }
        widths = {
            "position": 48,
            "title": 280,
            "artists": 210,
            "confidence": 90,
            "status": 100,
            "youtube": 280,
        }
        for key in columns:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], anchor="w")
        self.tree.column("position", anchor="center", stretch=False)
        self.tree.column("confidence", anchor="center", stretch=False)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.review_selected)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Progressbar(footer, variable=self.progress_value, maximum=100).pack(
            side="left", fill="x", expand=True
        )
        ttk.Label(footer, textvariable=self.status_text).pack(side="left", padx=(10, 0))

    def import_from_clipboard(self) -> None:
        if self._busy:
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showerror("Appunti vuoti", "Copia prima i brani da Spotify Desktop.")
            return
        playlist = parse_clipboard(text, self.playlist_name.get())
        if not playlist.tracks:
            messagebox.showerror(
                "Nessun brano rilevato",
                "Gli appunti non contengono URI, link o righe Spotify riconoscibili. "
                "Consulta la guida per il metodo alternativo.",
            )
            return
        self.session = self.repository.create(playlist)
        self._refresh_table()
        self.status_text.set(f"Importati {len(playlist.tracks)} brani. Sessione salvata.")

    def open_session(self) -> None:
        path = filedialog.askopenfilename(
            title="Apri una sessione PlaylistBridge",
            initialdir=self.repository.root,
            filetypes=(("Sessioni PlaylistBridge", "*.json"),),
        )
        if not path:
            return
        try:
            self.session = self.repository.load(Path(path))
        except Exception as exc:
            messagebox.showerror("Sessione non valida", str(exc))
            return
        self.playlist_name.set(self.session.source.name)
        self._refresh_table()
        self.status_text.set(
            f"Sessione caricata: {self.session.completed_count} brani già trasferiti."
        )

    def show_auth_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Configura YouTube Music")
        dialog.geometry("780x570")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        instructions = (
            "1. Apri music.youtube.com in Edge o Chrome ed effettua l'accesso.\n"
            "2. Premi F12, scegli Network/Rete e filtra per ‘browse’.\n"
            "3. Seleziona una richiesta POST a music.youtube.com.\n"
            "4. Copia i Request Headers oppure usa Copy as cURL (bash).\n"
            "5. Incolla qui sotto. I dati restano su questo PC e sono sensibili."
        )
        ttk.Label(frame, text=instructions, justify="left").pack(anchor="w")
        ttk.Button(
            frame,
            text="Apri YouTube Music",
            command=lambda: webbrowser.open("https://music.youtube.com"),
        ).pack(anchor="w", pady=8)
        text_box = tk.Text(frame, height=18, wrap="none")
        text_box.pack(fill="both", expand=True)

        def save_auth() -> None:
            raw = text_box.get("1.0", "end").strip()
            try:
                auth = build_browser_auth(raw)
                account = YouTubeMusicAdapter(auth).get_account_info()
                self.auth_store.save(auth)
            except Exception as exc:
                messagebox.showerror(
                    "Accesso non riuscito", AuthStore.redact(str(exc)), parent=dialog
                )
                return
            text_box.delete("1.0", "end")
            dialog.destroy()
            account_name = str(account.get("accountName") or "").strip()
            self._refresh_account_label(account_name or None)
            detail = f"Account: {account_name}" if account_name else "Account verificato."
            messagebox.showinfo("Accesso verificato", f"YouTube Music è collegato.\n{detail}")

        ttk.Button(frame, text="Verifica e salva", command=save_auth).pack(anchor="e", pady=(10, 0))

    def disconnect_auth(self) -> None:
        if not self.auth_store.exists():
            return
        if messagebox.askyesno(
            "Disconnetti YouTube Music",
            "Eliminare dal PC i dati di accesso salvati? "
            "Le sessioni di trasferimento restano intatte.",
        ):
            self.auth_store.delete()
            self._refresh_account_label()

    def _refresh_account_label(self, account_name: str | None = None) -> None:
        if account_name:
            self.account_text.set(f"YouTube Music: {account_name} (verificato)")
            return
        self.account_text.set(
            "YouTube Music: accesso salvato (da verificare)"
            if self.auth_store.exists()
            else "YouTube Music: non collegato"
        )

    def _youtube(self) -> YouTubeMusicAdapter:
        if not self.auth_store.exists():
            raise RuntimeError("Configura prima l'accesso a YouTube Music")
        return YouTubeMusicAdapter(self.auth_store.load())

    def start_matching(self) -> None:
        if self.session is None:
            messagebox.showwarning("Playlist mancante", "Importa prima una playlist da Spotify.")
            return
        if not self.auth_store.exists():
            messagebox.showwarning("Accesso mancante", "Configura prima YouTube Music.")
            return
        session = self.session

        def worker() -> TransferSession:
            youtube = self._youtube()
            youtube.test_auth()
            hydrated = hydrate_playlist(
                session.source,
                repository=self.repository,
                session=session,
                progress=self._queue_progress,
                cancel=self._cancel,
            )
            session.source = hydrated
            session.matches = match_playlist(
                hydrated,
                youtube,
                repository=self.repository,
                session=session,
                progress=self._queue_progress,
                cancel=self._cancel,
            )
            self.repository.save(session)
            return session

        self._run_worker(worker, self._matching_done)

    def _matching_done(self, session: TransferSession) -> None:
        self.session = session
        self._refresh_table()
        automatic = sum(match.status is MatchStatus.MATCHED for match in session.matches)
        review = sum(match.status is MatchStatus.REVIEW for match in session.matches)
        errors = sum(match.status is MatchStatus.ERROR for match in session.matches)
        self.status_text.set(
            f"Abbinamento concluso: {automatic} automatici, {review} da rivedere, {errors} errori."
        )

    def review_selected(self, _event: tk.Event | None = None) -> None:
        if self.session is None or self._busy:
            return
        selected = self.tree.selection()
        if not selected:
            return
        position = int(selected[0])
        match = next((m for m in self.session.matches if m.source.position == position), None)
        if match is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title(f"Rivedi #{position}: {match.source.title}")
        dialog.geometry("760x380")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text=f"Spotify: {match.source.title} — {', '.join(match.source.artists)}",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        options = tk.Listbox(frame, height=12)
        options.pack(fill="both", expand=True)
        for candidate in match.candidates:
            duration = (
                f"{candidate.duration_seconds // 60}:{candidate.duration_seconds % 60:02d}"
                if candidate.duration_seconds is not None
                else "?:??"
            )
            options.insert(
                "end",
                f"{candidate.score:.0%}  {candidate.title} — "
                f"{', '.join(candidate.artists)}  [{duration}]",
            )
        if match.candidates:
            options.selection_set(0)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))

        def choose() -> None:
            selection = options.curselection()
            if not selection:
                messagebox.showwarning("Nessuna scelta", "Seleziona un risultato.", parent=dialog)
                return
            candidate = match.candidates[selection[0]]
            match.selected_video_id = candidate.video_id
            match.confidence = candidate.score
            match.status = MatchStatus.MATCHED
            match.error = None
            self.repository.save(self.session)
            dialog.destroy()
            self._refresh_table()

        def skip() -> None:
            match.selected_video_id = None
            match.status = MatchStatus.SKIPPED
            self.repository.save(self.session)
            dialog.destroy()
            self._refresh_table()

        ttk.Button(buttons, text="Usa risultato", command=choose).pack(side="right")
        ttk.Button(buttons, text="Salta brano", command=skip).pack(side="right", padx=8)

    def start_transfer(self) -> None:
        if self.session is None or not self.session.matches:
            messagebox.showwarning("Abbinamento mancante", "Esegui prima Analizza e abbina.")
            return
        unresolved = [
            match
            for match in self.session.matches
            if match.status in {MatchStatus.REVIEW, MatchStatus.ERROR}
            and not match.selected_video_id
        ]
        if unresolved:
            if not messagebox.askyesno(
                "Brani non risolti",
                f"Ci sono {len(unresolved)} brani da rivedere o con errore. "
                "Vuoi contrassegnarli come saltati e continuare?",
            ):
                return
            for match in unresolved:
                match.status = MatchStatus.SKIPPED
            self.repository.save(self.session)
        session = self.session

        def worker() -> tuple[int, Path]:
            youtube = self._youtube()
            youtube.test_auth()
            count = transfer_session(
                session,
                youtube,
                self.repository,
                progress=self._queue_progress,
                cancel=self._cancel,
            )
            report = write_session_report(session, self.data_dir / "reports")
            return count, report

        self._run_worker(worker, self._transfer_done)

    def _transfer_done(self, result: tuple[int, Path]) -> None:
        count, report = result
        self._refresh_table()
        self.status_text.set(f"Trasferimento completato: {count} nuovi brani aggiunti.")
        messagebox.showinfo(
            "Trasferimento completato",
            f"Brani aggiunti in questa esecuzione: {count}\nReport: {report}",
        )

    def export_report(self) -> None:
        if self.session is None:
            return
        destination = filedialog.asksaveasfilename(
            title="Salva report",
            defaultextension=".csv",
            filetypes=(("CSV", "*.csv"),),
            initialfile=f"report-{self.session.source.name}.csv",
        )
        if destination:
            from .storage import export_report

            export_report(self.session, Path(destination))
            self.status_text.set(f"Report salvato in {destination}")

    def cancel_operation(self) -> None:
        if self._busy:
            self._cancel.set()
            self.status_text.set("Annullamento in corso dopo l'operazione corrente…")

    def _run_worker(self, worker: Callable[[], Any], done: Callable[[Any], None]) -> None:
        if self._busy:
            return
        self._busy = True
        self._cancel.clear()
        self.progress_value.set(0)
        self._set_buttons_enabled(False)

        def target() -> None:
            try:
                result = worker()
            except Exception as exc:
                self._events.put(("error", exc))
            else:
                self._events.put(("done", result, done))

        threading.Thread(target=target, daemon=True).start()

    def _queue_progress(self, current: int, total: int, label: str) -> None:
        self._events.put(("progress", current, total, label))

    def _poll_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                if event[0] == "progress":
                    _, current, total, label = event
                    self.progress_value.set(100 * current / total if total else 0)
                    self.status_text.set(f"{label}: {current}/{total}")
                elif event[0] == "done":
                    _, result, callback = event
                    self._busy = False
                    self._set_buttons_enabled(True)
                    callback(result)
                elif event[0] == "error":
                    _, exc = event
                    self._busy = False
                    self._set_buttons_enabled(True)
                    if isinstance(exc, WorkflowCancelled):
                        self.status_text.set(
                            "Operazione annullata. I progressi salvati restano disponibili."
                        )
                    else:
                        messagebox.showerror("Operazione non riuscita", AuthStore.redact(str(exc)))
                        self.status_text.set(
                            "Operazione non riuscita; la sessione salvata non è stata persa."
                        )
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (
            self.import_button,
            self.open_session_button,
            self.configure_auth_button,
            self.disconnect_auth_button,
            self.match_button,
            self.transfer_button,
            self.report_button,
        ):
            button.configure(state=state)
        self.cancel_button.configure(state="normal" if not enabled else "disabled")

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.session is None:
            return
        match_by_position = {m.source.position: m for m in self.session.matches}
        for track in self.session.source.tracks:
            match = match_by_position.get(track.position)
            candidate_name = ""
            if match and match.selected_video_id:
                candidate = next(
                    (c for c in match.candidates if c.video_id == match.selected_video_id), None
                )
                candidate_name = candidate.title if candidate else match.selected_video_id
            self.tree.insert(
                "",
                "end",
                iid=str(track.position),
                values=(
                    track.position,
                    track.title or track.spotify_id or "(metadati mancanti)",
                    ", ".join(track.artists),
                    f"{match.confidence:.0%}" if match else "",
                    match.status.value if match else MatchStatus.PENDING.value,
                    candidate_name,
                ),
            )


def main() -> None:
    app = PlaylistBridgeApp()
    app._set_buttons_enabled(True)
    app.mainloop()


if __name__ == "__main__":
    main()
