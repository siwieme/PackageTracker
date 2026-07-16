"""Cross-platform desktop GUI for PackageTracker.

Runs on Windows, Linux, and macOS via customtkinter. Tracking is performed in
a background thread so the UI stays responsive. Supports dark / light mode
and an optional postal-code field required by some couriers.

Launch:
    python ui.py
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

import customtkinter as ctk

from adapters.base import CourierError
from core.models import PackageStatus, StatusCode
from core.router import CourierRouter
from main import track

# Status → (label text, color)
_STATUS_STYLE: dict[StatusCode, tuple[str, str]] = {
    StatusCode.DELIVERED: ("✓  AFGELEVERD", "#2ecc71"),
    StatusCode.TRANSIT:   ("→  IN TRANSIT", "#3498db"),
    StatusCode.EXCEPTION: ("✕  PROBLEEM",   "#e74c3c"),
}

_FONT_FAMILY = "Helvetica"


class App(ctk.CTk):
    """Main application window for PackageTracker."""

    def __init__(self) -> None:
        super().__init__()

        self.title("PackageTracker")
        self.geometry("620x700")
        self.minsize(480, 500)
        self.resizable(True, True)

        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self._build_ui()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # ── Header ──────────────────────────────────────────────────────
        header = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 0))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="PackageTracker",
            font=ctk.CTkFont(family=_FONT_FAMILY, size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self._mode_btn = ctk.CTkButton(
            header,
            text="🌙 Donker",
            width=100,
            command=self._toggle_mode,
            fg_color="transparent",
            border_width=1,
        )
        self._mode_btn.grid(row=0, column=1, sticky="e")

        # ── Input form ──────────────────────────────────────────────────
        form = ctk.CTkFrame(self)
        form.grid(row=1, column=0, sticky="ew", padx=20, pady=16)
        form.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(form, text="Trackingnummer", anchor="w").grid(
            row=0, column=0, padx=(16, 8), pady=(16, 4), sticky="w"
        )
        self._entry_number = ctk.CTkEntry(
            form,
            placeholder_text="bijv. 323212345678901234567890",
            height=36,
        )
        self._entry_number.grid(
            row=0, column=1, columnspan=2, padx=(0, 16), pady=(16, 4), sticky="ew"
        )
        self._entry_number.bind("<Return>", lambda _: self._on_track())

        ctk.CTkLabel(form, text="Postcode", anchor="w").grid(
            row=1, column=0, padx=(16, 8), pady=(4, 16), sticky="w"
        )
        self._entry_postal = ctk.CTkEntry(
            form,
            placeholder_text="optioneel  —  bijv. 9000",
            height=36,
            width=140,
        )
        self._entry_postal.grid(row=1, column=1, padx=(0, 8), pady=(4, 16), sticky="w")
        self._entry_postal.bind("<Return>", lambda _: self._on_track())

        self._track_btn = ctk.CTkButton(
            form,
            text="Opzoeken",
            height=36,
            command=self._on_track,
        )
        self._track_btn.grid(row=1, column=2, padx=(0, 16), pady=(4, 16), sticky="e")

        # ── Results ─────────────────────────────────────────────────────
        self._results_frame = ctk.CTkScrollableFrame(self, label_text="Resultaat")
        self._results_frame.grid(
            row=2, column=0, sticky="nsew", padx=20, pady=(0, 20)
        )
        self._results_frame.grid_columnconfigure(0, weight=1)

        self._placeholder = ctk.CTkLabel(
            self._results_frame,
            text="Voer een trackingnummer in en druk op Opzoeken.",
            text_color="gray",
        )
        self._placeholder.grid(row=0, column=0, pady=40)

    # ------------------------------------------------------------------
    # Tracking logic
    # ------------------------------------------------------------------

    def _on_track(self) -> None:
        number = self._entry_number.get().strip()
        if not number:
            self._show_error("Voer een trackingnummer in.")
            return

        postal = self._entry_postal.get().strip() or None
        self._set_loading()
        threading.Thread(
            target=self._fetch_in_thread,
            args=(number, postal),
            daemon=True,
        ).start()

    def _fetch_in_thread(self, number: str, postal: Optional[str]) -> None:
        try:
            status = asyncio.run(track(number, postal_code=postal))
            self.after(0, self._show_result, status)
        except (ValueError, CourierError) as exc:
            self.after(0, self._show_error, str(exc))
        except Exception as exc:
            self.after(0, self._show_error, f"Onverwachte fout: {exc}")

    # ------------------------------------------------------------------
    # Result rendering
    # ------------------------------------------------------------------

    def _clear_results(self) -> None:
        for widget in self._results_frame.winfo_children():
            widget.destroy()

    def _set_loading(self) -> None:
        self._track_btn.configure(state="disabled", text="Bezig...")
        self._clear_results()
        ctk.CTkLabel(
            self._results_frame,
            text="Even geduld…",
            text_color="gray",
        ).grid(row=0, column=0, pady=40)

    def _show_error(self, message: str) -> None:
        self._track_btn.configure(state="normal", text="Opzoeken")
        self._clear_results()
        ctk.CTkLabel(
            self._results_frame,
            text=f"⚠  {message}",
            text_color="#e74c3c",
            wraplength=500,
            justify="left",
        ).grid(row=0, column=0, pady=40, padx=16, sticky="w")

    def _show_result(self, status: PackageStatus) -> None:
        self._track_btn.configure(state="normal", text="Opzoeken")
        self._clear_results()
        frame = self._results_frame
        row = 0

        # ── Summary bar ─────────────────────────────────────────────────
        summary = ctk.CTkFrame(frame, corner_radius=8)
        summary.grid(row=row, column=0, sticky="ew", pady=(8, 4), padx=4)
        summary.grid_columnconfigure(1, weight=1)
        row += 1

        label_text, label_color = _STATUS_STYLE[status.latest_event.status_code]
        ctk.CTkLabel(
            summary,
            text=label_text,
            font=ctk.CTkFont(family=_FONT_FAMILY, size=14, weight="bold"),
            text_color=label_color,
        ).grid(row=0, column=0, padx=16, pady=12, sticky="w")

        ctk.CTkLabel(
            summary,
            text=status.courier.upper(),
            font=ctk.CTkFont(family=_FONT_FAMILY, size=12),
            text_color="gray",
        ).grid(row=0, column=1, padx=16, pady=12, sticky="e")

        ctk.CTkLabel(
            summary,
            text=status.tracking_number,
            font=ctk.CTkFont(family=_FONT_FAMILY, size=11),
            text_color="gray",
        ).grid(row=1, column=0, columnspan=2, padx=16, pady=(0, 12), sticky="w")

        # ── Latest event ────────────────────────────────────────────────
        ctk.CTkLabel(
            frame,
            text="Laatste update",
            font=ctk.CTkFont(family=_FONT_FAMILY, size=13, weight="bold"),
            anchor="w",
        ).grid(row=row, column=0, sticky="w", padx=8, pady=(12, 2))
        row += 1

        latest = status.latest_event
        self._event_row(frame, row, latest, highlight=True)
        row += 1

        # ── History ─────────────────────────────────────────────────────
        if len(status.history) > 1:
            ctk.CTkLabel(
                frame,
                text="Geschiedenis",
                font=ctk.CTkFont(family=_FONT_FAMILY, size=13, weight="bold"),
                anchor="w",
            ).grid(row=row, column=0, sticky="w", padx=8, pady=(12, 2))
            row += 1

            for event in reversed(status.history[:-1]):
                self._event_row(frame, row, event, highlight=False)
                row += 1

    def _event_row(self, parent: ctk.CTkScrollableFrame, row: int, event, *, highlight: bool) -> None:
        from core.models import TrackingEvent
        ev: TrackingEvent = event

        _, color = _STATUS_STYLE[ev.status_code]
        bg = ("gray90", "gray20") if highlight else ("gray95", "gray17")

        card = ctk.CTkFrame(parent, corner_radius=6, fg_color=bg)
        card.grid(row=row, column=0, sticky="ew", padx=4, pady=2)
        card.grid_columnconfigure(1, weight=1)

        # Colored status dot
        ctk.CTkLabel(
            card,
            text="●",
            text_color=color,
            font=ctk.CTkFont(size=10),
            width=16,
        ).grid(row=0, column=0, padx=(10, 4), pady=8, sticky="n")

        # Timestamp + description
        ts = ev.timestamp.strftime("%d %b %Y  %H:%M")
        desc = ev.description or ev.status_code.value
        location_line = f"📍  {ev.location}" if ev.location else ""

        text_col = ctk.CTkFrame(card, fg_color="transparent")
        text_col.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=6)

        ctk.CTkLabel(
            text_col,
            text=ts,
            font=ctk.CTkFont(family=_FONT_FAMILY, size=11),
            text_color="gray",
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            text_col,
            text=desc,
            font=ctk.CTkFont(family=_FONT_FAMILY, size=12),
            anchor="w",
            wraplength=420,
            justify="left",
        ).grid(row=1, column=0, sticky="w")

        if location_line:
            ctk.CTkLabel(
                text_col,
                text=location_line,
                font=ctk.CTkFont(family=_FONT_FAMILY, size=11),
                text_color="gray",
                anchor="w",
            ).grid(row=2, column=0, sticky="w")

    # ------------------------------------------------------------------
    # Appearance toggle
    # ------------------------------------------------------------------

    def _toggle_mode(self) -> None:
        current = ctk.get_appearance_mode()
        if current == "Dark":
            ctk.set_appearance_mode("Light")
            self._mode_btn.configure(text="🌙 Donker")
        else:
            ctk.set_appearance_mode("Dark")
            self._mode_btn.configure(text="☀️  Licht")


def main() -> None:
    """Launch the desktop UI."""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
