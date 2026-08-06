"""
Modal prompts a worker thread can raise on the Tk main loop.

Both test sequences run on a background thread but have to stop and ask the
operator something - a scale reading, or confirmation that the next object is
in place. Tk widgets may only be touched from the main loop, so the worker
hands the request over with root.after() and blocks on an Event until the
dialog answers it.

Each dialog carries its own abort button. A grab_set() dialog makes the main
window's Abort unreachable, so without one an operator who wants to stop mid
run has nothing to click.
"""

import threading
import tkinter as tk
from tkinter import ttk

from .motion import AbortedError

# How often the blocked worker re-checks for an abort raised elsewhere, e.g.
# by the emergency stop button.
ABORT_CHECK_INTERVAL = 0.2


class WorkerPrompt:
    """Owns the worker <-> UI handshake for one window's modal dialogs."""

    def __init__(self, root, abort_event):
        self.root = root
        self.abort_event = abort_event
        self._answered = threading.Event()
        self._result = {}
        self._dialog = None

    def ask(self, builder, *args):
        """
        Called from the worker thread. Blocks until the dialog is answered.

        `builder(dialog, submit, abort, *args)` populates the Toplevel and is
        run on the UI thread; it calls submit(value) or abort().
        """
        self._answered.clear()
        self._result.clear()
        self.root.after(0, self._open, builder, args)

        while not self._answered.wait(timeout=ABORT_CHECK_INTERVAL):
            if self.abort_event.is_set():
                # Aborted from somewhere else while this dialog was up.
                self.root.after(0, self.close)
                raise AbortedError()

        if self._result.get("aborted"):
            raise AbortedError()
        return self._result.get("value")

    def release(self):
        """Unblock the worker without an answer, for shutdown."""
        self._answered.set()

    # ------------------------------------------------------------------
    # UI thread only
    # ------------------------------------------------------------------
    def _open(self, builder, args):
        dialog = tk.Toplevel(self.root)
        self._dialog = dialog
        dialog.transient(self.root)
        dialog.resizable(False, False)
        builder(dialog, self._submit, self._abort, *args)
        dialog.protocol("WM_DELETE_WINDOW", self._abort)
        dialog.grab_set()

    def _submit(self, value):
        self._result["value"] = value
        self._result["aborted"] = False
        self.close()
        self._answered.set()

    def _abort(self):
        self._result["aborted"] = True
        self.abort_event.set()
        self.close()
        self._answered.set()

    def close(self):
        dialog = self._dialog
        if dialog is not None:
            try:
                dialog.grab_release()
                dialog.destroy()
            except tk.TclError:
                pass
            self._dialog = None


# ----------------------------------------------------------------------------
# Dialog builders
# ----------------------------------------------------------------------------
def _header(dialog, finger, padding, goal_current, repeat, current_unit_ma, repeats):
    ttk.Label(
        dialog,
        text=(
            f"{finger}  /  {padding}\n"
            f"Goal current: {goal_current} raw "
            f"({goal_current * current_unit_ma:.0f} mA)\n"
            f"Repeat {repeat} of {repeats}"
        ),
        justify="left",
    ).grid(row=0, column=0, columnspan=2, padx=14, pady=(14, 8), sticky="w")


def _buttons(dialog, row, submit_text, submit_command, abort):
    frame = ttk.Frame(dialog)
    frame.grid(row=row, column=0, columnspan=2, padx=14, pady=(8, 14), sticky="ew")
    ttk.Button(frame, text=submit_text, command=submit_command).grid(row=0, column=0, padx=(0, 8))
    ttk.Button(frame, text="Abort run", command=abort).grid(row=0, column=1)


def weight_dialog(dialog, submit, abort, finger, padding, goal_current, repeat,
                  current_unit_ma, repeats):
    """Ask for the weight the kitchen scale showed under the closed gripper."""
    dialog.title("Scale reading")
    _header(dialog, finger, padding, goal_current, repeat, current_unit_ma, repeats)

    ttk.Label(dialog, text="Weight observed on scale (g):").grid(
        row=1, column=0, padx=14, pady=4, sticky="w"
    )

    entry_var = tk.StringVar()
    entry = ttk.Entry(dialog, textvariable=entry_var, width=12)
    entry.grid(row=1, column=1, padx=14, pady=4, sticky="w")
    entry.focus_set()

    error_var = tk.StringVar()
    ttk.Label(dialog, textvariable=error_var, foreground="red").grid(
        row=2, column=0, columnspan=2, padx=14, sticky="w"
    )

    def on_submit(_event=None):
        try:
            weight = float(entry_var.get().strip())
        except ValueError:
            error_var.set("Enter a number, e.g. 412.5")
            return
        submit(weight)

    entry.bind("<Return>", on_submit)
    _buttons(dialog, 3, "Submit", on_submit, abort)


def ready_dialog(dialog, submit, abort, finger, padding, goal_current, repeat,
                 current_unit_ma, repeats):
    """Wait for the operator to seat the object before the gripper closes."""
    dialog.title("Ready for next grip")
    _header(dialog, finger, padding, goal_current, repeat, current_unit_ma, repeats)

    ttk.Label(
        dialog,
        text=(
            "Place the test object between the fingers, then continue.\n\n"
            "The gripper will close and hold. When the status line says so,\n"
            "pull the object steadily until it slips out - the drop in\n"
            "present current is what gets timed."
        ),
        justify="left",
    ).grid(row=1, column=0, columnspan=2, padx=14, pady=4, sticky="w")

    def on_submit(_event=None):
        submit(True)

    button_frame = ttk.Frame(dialog)
    button_frame.grid(row=3, column=0, columnspan=2, padx=14, pady=(8, 14), sticky="ew")
    continue_button = ttk.Button(button_frame, text="Object in place - grip it",
                                 command=on_submit)
    continue_button.grid(row=0, column=0, padx=(0, 8))
    ttk.Button(button_frame, text="Abort run", command=abort).grid(row=0, column=1)
    continue_button.focus_set()
    dialog.bind("<Return>", on_submit)
