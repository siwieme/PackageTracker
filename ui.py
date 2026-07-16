"""Cross-platform desktop GUI for PackageTracker.

Modern, card-based UI built with customtkinter. Runs on Windows, Linux, and
macOS (requires python-tk@3.11 via Homebrew on macOS — see README).

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
from main import track

# ── Design tokens ────────────────────────────────────────────────────────────
_ACCENT       = "#4F8EF7"
_GREEN        = "#34C759"
_ORANGE       = "#FF9500"
_RED          = "#FF3B30"
_GRAY         = "#8E8E93"

_STATUS_COLOR = {
    StatusCode.DELIVERED: _GREEN,
    StatusCode.TRANSIT:   _ACCENT,
    StatusCode.EXCEPTION: _RED,
}
_STATUS_LABEL = {
    StatusCode.DELIVERED: "Afgeleverd",
    StatusCode.TRANSIT:   "Onderweg",
    StatusCode.EXCEPTION: "Probleem",
}
_STATUS_ICON = {
    StatusCode.DELIVERED: "✓",
    StatusCode.TRANSIT:   "→",
    StatusCode.EXCEPTION: "✕",
}

_COURIER_ICON = {
    "bpost":  "📮",
    "postnl": "📦",
    "dhl":    "📫",
}

_FONT   = "Helvetica"
_RADIUS = 12


class _Timeline(ctk.CTkScrollableFrame):
    """Vertical timeline showing tracking events, newest on top."""

    def __init__(self, master: ctk.CTkFrame, events: list, **kw: object) -> None:
        super().__init__(master, label_text="", **kw)
        self.grid_columnconfigure(0, weight=1)
        for i, ev in enumerate(reversed(events)):
            is_latest = (i == 0)
            self._add_row(i, ev, is_latest=is_latest, is_last=(i == len(events) - 1))

    def _add_row(self, row: int, ev, *, is_latest: bool, is_last: bool) -> None:
        color  = _STATUS_COLOR[ev.status_code]
        icon   = _STATUS_ICON[ev.status_code]
        ts     = ev.timestamp.strftime("%d %b  %H:%M")

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.grid(row=row, column=0, sticky="ew", pady=(0, 2))
        outer.grid_columnconfigure(1, weight=1)

        # Left column: dot + vertical line
        spine = ctk.CTkFrame(outer, fg_color="transparent", width=32)
        spine.grid(row=0, column=0, sticky="ns", padx=(4, 0))

        dot = ctk.CTkLabel(
            spine, text=icon,
            width=26, height=26,
            corner_radius=13,
            fg_color=color if is_latest else ("gray80", "gray30"),
            text_color="white" if is_latest else color,
            font=ctk.CTkFont(family=_FONT, size=11, weight="bold"),
        )
        dot.pack(pady=(10, 0))

        if not is_last:
            line = ctk.CTkFrame(spine, width=2, fg_color=("gray80", "gray35"))
            line.pack(fill="y", expand=True, pady=(0, 0))

        # Right column: card
        alpha = 1.0 if is_latest else 0.0
        card_fg = ("gray96", "gray18") if is_latest else ("gray92", "gray15")

        card = ctk.CTkFrame(outer, corner_radius=_RADIUS, fg_color=card_fg)
        card.grid(row=0, column=1, sticky="ew", padx=(6, 8), pady=6)
        card.grid_columnconfigure(0, weight=1)

        # Timestamp + status badge
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        top.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            top, text=ts,
            font=ctk.CTkFont(family=_FONT, size=11),
            text_color=_GRAY, anchor="w",
        ).grid(row=0, column=0, sticky="w")

        badge_color = color if is_latest else ("gray75", "gray40")
        ctk.CTkLabel(
            top,
            text=f" {_STATUS_LABEL[ev.status_code]} ",
            font=ctk.CTkFont(family=_FONT, size=10, weight="bold"),
            corner_radius=6,
            fg_color=badge_color,
            text_color="white",
        ).grid(row=0, column=1, sticky="e")

        # Description
        ctk.CTkLabel(
            card,
            text=ev.description or ev.status_code.value,
            font=ctk.CTkFont(family=_FONT, size=13,
                             weight="bold" if is_latest else "normal"),
            anchor="w", justify="left", wraplength=380,
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 2))

        # Location
        if ev.location:
            ctk.CTkLabel(
                card,
                text=f"📍  {ev.location}",
                font=ctk.CTkFont(family=_FONT, size=11),
                text_color=_GRAY, anchor="w",
            ).grid(row=2, column=0, sticky="w", padx=12, pady=(0, 10))
        else:
            ctk.CTkFrame(card, height=8, fg_color="transparent").grid(row=2, column=0)


class _StatusCard(ctk.CTkFrame):
    """Hero card showing courier, delivery status, and tracking number."""

    def __init__(self, master: ctk.CTkFrame, status: PackageStatus, **kw: object) -> None:
        super().__init__(master, corner_radius=_RADIUS, **kw)
        self.grid_columnconfigure(0, weight=1)

        ev     = status.latest_event
        color  = _STATUS_COLOR[ev.status_code]
        icon   = _COURIER_ICON.get(status.courier, "📦")
        label  = _STATUS_LABEL[ev.status_code]
        ts     = ev.timestamp.strftime("%d %b %Y  –  %H:%M")

        # Coloured top bar
        bar = ctk.CTkFrame(self, height=6, corner_radius=0, fg_color=color)
        bar.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        # Round the top corners manually via outer radius
        bar.grid_propagate(False)

        # Courier icon + name
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=1, column=0, sticky="ew", padx=20, pady=(14, 0))
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text=icon,
                     font=ctk.CTkFont(size=28)).grid(row=0, column=0, padx=(0, 10))

        info = ctk.CTkFrame(top, fg_color="transparent")
        info.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(info, text=status.courier.upper(),
                     font=ctk.CTkFont(family=_FONT, size=11),
                     text_color=_GRAY, anchor="w").pack(anchor="w")
        ctk.CTkLabel(info, text=status.tracking_number,
                     font=ctk.CTkFont(family=_FONT, size=12, weight="bold"),
                     anchor="w").pack(anchor="w")

        # Big status label
        ctk.CTkLabel(
            self,
            text=f"{_STATUS_ICON[ev.status_code]}  {label}",
            font=ctk.CTkFont(family=_FONT, size=22, weight="bold"),
            text_color=color, anchor="w",
        ).grid(row=2, column=0, sticky="w", padx=20, pady=(10, 2))

        # Description + timestamp
        ctk.CTkLabel(
            self,
            text=ev.description or "",
            font=ctk.CTkFont(family=_FONT, size=13),
            anchor="w", wraplength=500, justify="left",
        ).grid(row=3, column=0, sticky="w", padx=20)

        ctk.CTkLabel(
            self,
            text=ts + (f"  ·  {ev.location}" if ev.location else ""),
            font=ctk.CTkFont(family=_FONT, size=11),
            text_color=_GRAY, anchor="w",
        ).grid(row=4, column=0, sticky="w", padx=20, pady=(4, 16))


class App(ctk.CTk):
    """Main application window for PackageTracker."""

    def __init__(self) -> None:
        super().__init__()
        self.title("PackageTracker")
        self.geometry("600x720")
        self.minsize(480, 520)
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        self._build()

    # ── Layout ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Top bar ──────────────────────────────────────────────────────────
        bar = ctk.CTkFrame(self, corner_radius=0, height=56,
                           fg_color=(_ACCENT, "#1A3A6B"))
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_propagate(False)

        ctk.CTkLabel(
            bar, text="  📬  PackageTracker",
            font=ctk.CTkFont(family=_FONT, size=16, weight="bold"),
            text_color="white", anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16)

        self._mode_btn = ctk.CTkButton(
            bar, text="🌙", width=36, height=36,
            fg_color="transparent", hover_color=("#3A7AE4", "#0F2456"),
            border_width=0, command=self._toggle_mode,
            font=ctk.CTkFont(size=16),
        )
        self._mode_btn.grid(row=0, column=1, padx=10)

        # ── Scroll container ─────────────────────────────────────────────────
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent",
                                        scrollbar_button_color=(_ACCENT, "#1A3A6B"))
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)
        self._scroll = scroll

        # ── Input card ───────────────────────────────────────────────────────
        form = ctk.CTkFrame(scroll, corner_radius=_RADIUS)
        form.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        form.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(form, text="Trackingnummer",
                     font=ctk.CTkFont(family=_FONT, size=12),
                     text_color=_GRAY, anchor="w",
                     ).grid(row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 2))

        self._entry_number = ctk.CTkEntry(
            form, placeholder_text="bijv. 323204736100000008192030",
            height=40, font=ctk.CTkFont(family=_FONT, size=13),
            corner_radius=8,
        )
        self._entry_number.grid(row=1, column=0, columnspan=2, sticky="ew",
                                padx=16, pady=(0, 10))
        self._entry_number.bind("<Return>", lambda _: self._on_track())

        ctk.CTkLabel(form, text="Postcode  (optioneel)",
                     font=ctk.CTkFont(family=_FONT, size=12),
                     text_color=_GRAY, anchor="w",
                     ).grid(row=2, column=0, sticky="w", padx=16, pady=(0, 2))

        self._entry_postal = ctk.CTkEntry(
            form, placeholder_text="bijv. 9160",
            height=40, width=140, font=ctk.CTkFont(family=_FONT, size=13),
            corner_radius=8,
        )
        self._entry_postal.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 14))
        self._entry_postal.bind("<Return>", lambda _: self._on_track())

        self._btn = ctk.CTkButton(
            form, text="Opzoeken  →",
            height=40, corner_radius=8,
            font=ctk.CTkFont(family=_FONT, size=13, weight="bold"),
            fg_color=_ACCENT, hover_color="#3A7AE4",
            command=self._on_track,
        )
        self._btn.grid(row=3, column=1, sticky="e", padx=16, pady=(0, 14))

        # ── Results placeholder ───────────────────────────────────────────────
        self._results_row = 1
        self._placeholder = ctk.CTkLabel(
            scroll,
            text="Voer een trackingnummer in om te beginnen.",
            font=ctk.CTkFont(family=_FONT, size=13),
            text_color=_GRAY,
        )
        self._placeholder.grid(row=self._results_row, column=0, pady=40)

    # ── Tracking ─────────────────────────────────────────────────────────────

    def _on_track(self) -> None:
        number = self._entry_number.get().strip()
        if not number:
            self._show_error("Voer een trackingnummer in.")
            return
        postal = self._entry_postal.get().strip() or None
        self._set_loading()
        threading.Thread(
            target=self._fetch,
            args=(number, postal),
            daemon=True,
        ).start()

    def _fetch(self, number: str, postal: Optional[str]) -> None:
        try:
            status = asyncio.run(track(number, postal_code=postal))
            self.after(0, self._show_result, status)
        except (ValueError, CourierError) as exc:
            self.after(0, self._show_error, str(exc))
        except Exception as exc:
            self.after(0, self._show_error, f"Onverwachte fout: {exc}")

    # ── Render helpers ────────────────────────────────────────────────────────

    def _clear_results(self) -> None:
        for w in self._scroll.winfo_children():
            if w is not self._scroll.winfo_children()[0]:  # keep form card
                w.destroy()

    def _set_loading(self) -> None:
        self._btn.configure(state="disabled", text="Bezig…")
        self._clear_results()
        spin = ctk.CTkLabel(
            self._scroll, text="⏳  Even geduld…",
            font=ctk.CTkFont(family=_FONT, size=13), text_color=_GRAY,
        )
        spin.grid(row=self._results_row, column=0, pady=40)

    def _show_error(self, msg: str) -> None:
        self._btn.configure(state="normal", text="Opzoeken  →")
        self._clear_results()
        err = ctk.CTkFrame(self._scroll, corner_radius=_RADIUS,
                           fg_color=("#FFF0EE", "#3A1A18"))
        err.grid(row=self._results_row, column=0, sticky="ew", padx=16, pady=8)
        err.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            err, text=f"⚠  {msg}",
            font=ctk.CTkFont(family=_FONT, size=13),
            text_color=_RED, wraplength=500, justify="left", anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=14)

    def _show_result(self, status: PackageStatus) -> None:
        self._btn.configure(state="normal", text="Opzoeken  →")
        self._clear_results()

        # Hero status card
        _StatusCard(
            self._scroll, status,
            fg_color=("white", "gray17"),
        ).grid(row=self._results_row, column=0,
               sticky="ew", padx=16, pady=(8, 4))

        # Section header
        ctk.CTkLabel(
            self._scroll, text="  Geschiedenis",
            font=ctk.CTkFont(family=_FONT, size=12, weight="bold"),
            text_color=_GRAY, anchor="w",
        ).grid(row=self._results_row + 1, column=0,
               sticky="w", padx=16, pady=(12, 2))

        # Timeline
        _Timeline(
            self._scroll, status.history,
            fg_color="transparent",
            scrollbar_button_color=(_ACCENT, "#1A3A6B"),
        ).grid(row=self._results_row + 2, column=0,
               sticky="ew", padx=8, pady=(0, 16))

    # ── Appearance ───────────────────────────────────────────────────────────

    def _toggle_mode(self) -> None:
        if ctk.get_appearance_mode() == "Dark":
            ctk.set_appearance_mode("Light")
            self._mode_btn.configure(text="🌙")
        else:
            ctk.set_appearance_mode("Dark")
            self._mode_btn.configure(text="☀️")


def main() -> None:
    """Launch the desktop UI."""
    App().mainloop()


if __name__ == "__main__":
    main()
