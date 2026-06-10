# -*- coding: utf-8 -*-
"""
Progress dialog for long-running claims operations (preview-layer generation,
LM corner adjustment, claim processing, etc.).

The async claims endpoints (POST + 202 + poll) take 1-3 minutes server-side
on a 700-claim block. The polling helper in `claims_manager.py` blocks the
calling thread for the duration, which would otherwise look like a frozen UI
to the user. This dialog gives them:

  - the operation name (e.g. "Generating preview layers")
  - the current stage ("Processing claims", "Finalizing")
  - the current claim name and 0-100% progress bar driven by per-claim ticks
    that the server reports via the status-poll response
  - elapsed time
  - a Cancel button that abandons polling cleanly (the server-side worker
    finishes and its result expires from cache; nothing to roll back)

The owning thread drives the dialog by calling `set_status(...)` on each
poll. The dialog must be created and updated from the same thread (the QGIS
main thread for the synchronous `_post_preview_layers_async` callers).
"""
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QPushButton,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont
from ..utils.compat import QSizePolicy_Expanding, QSizePolicy_Preferred


class ClaimsProgressDialog(QDialog):
    """
    Modal progress dialog for async claims operations.

    Usage:
        dlg = ClaimsProgressDialog("Generating preview layers", parent=self)
        dlg.show()
        for poll_result in poll_loop():
            dlg.set_status(
                stage=poll_result['stage'],
                progress_pct=poll_result['progress_pct'],
                current_claim_name=poll_result.get('current_claim_name'),
                current_claim_index=poll_result.get('current_claim_index'),
                total_claims=poll_result.get('claim_count'),
                elapsed_seconds=poll_result.get('elapsed_seconds'),
            )
            if dlg.was_canceled():
                raise PreviewLayersCanceled()
        dlg.close()
    """

    # User-visible label for each `stage` value the server emits. Keep these
    # short so they fit on one line.
    _STAGE_LABELS = {
        'queued': 'Queued',
        'preparing': 'Preparing',
        'processing': 'Processing claims',
        'finalizing': 'Finalizing',
    }

    def __init__(self, title: str, parent=None):
        """
        Args:
            title: Operation name shown in the header (e.g. "Generating
                preview layers", "Updating LM corners").
            parent: Parent widget — pass the wizard / main dialog so the
                modal stays attached to it.
        """
        super().__init__(parent)
        self._canceled = False
        self._setup_ui(title)
        # Geometry: keep the user's window-manager defaults but suggest a
        # reasonable size. ~480 wide is enough for "Processing claim 412 of
        # 714 (GE 412)" on one line.
        self.resize(520, 200)

    def _setup_ui(self, title: str):
        self.setWindowTitle(title)
        # Modal so input goes to this dialog. We don't use Qt.WindowModal
        # because our caller is blocked in a polling loop on the main thread —
        # ApplicationModal is the right semantic but the caller still needs
        # to processEvents for the cancel button to work.
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Title.
        self._title_label = QLabel(title)
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(11)
        self._title_label.setFont(title_font)
        layout.addWidget(self._title_label)

        # Stage line — e.g. "Processing claims".
        self._stage_label = QLabel("Queued")
        layout.addWidget(self._stage_label)

        # Per-claim detail line (varies by stage; hidden when irrelevant).
        self._detail_label = QLabel("")
        self._detail_label.setSizePolicy(QSizePolicy_Expanding, QSizePolicy_Preferred)
        layout.addWidget(self._detail_label)

        # Progress bar.
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        layout.addWidget(self._progress)

        # Elapsed-time line.
        self._elapsed_label = QLabel("Elapsed: 0s")
        layout.addWidget(self._elapsed_label)

        # Cancel button (right-aligned).
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._cancel_button = QPushButton("Cancel")
        self._cancel_button.clicked.connect(self._on_cancel)
        button_row.addWidget(self._cancel_button)
        layout.addLayout(button_row)

    def _on_cancel(self):
        self._canceled = True
        self._cancel_button.setEnabled(False)
        self._stage_label.setText("Cancelling…")

    def was_canceled(self) -> bool:
        return self._canceled

    def set_status(
        self,
        stage: str = 'processing',
        progress_pct: int = 0,
        current_claim_name: str = '',
        current_claim_index: int = 0,
        total_claims: int = 0,
        elapsed_seconds: int = 0,
    ):
        """
        Update the dialog with a snapshot from the latest poll response.

        Pass whatever fields are present in the poll body; missing fields fall
        back to their defaults. Always calls QApplication.processEvents() so
        the Cancel button stays responsive even though the caller is blocked
        in a polling loop on the same thread.
        """
        self._stage_label.setText(self._STAGE_LABELS.get(stage, stage or '...'))

        # Detail line content depends on stage. During 'processing' show the
        # current claim. During 'preparing'/'finalizing' show a generic
        # message. During 'queued' (no progress yet) show waiting text.
        if stage == 'processing' and total_claims:
            name_part = f" ({current_claim_name})" if current_claim_name else ''
            self._detail_label.setText(
                f"Claim {current_claim_index} of {total_claims}{name_part}"
            )
        elif stage == 'preparing':
            self._detail_label.setText("Loading reference data…")
        elif stage == 'finalizing':
            self._detail_label.setText("Deduplicating waypoints, computing neighbors…")
        elif stage == 'queued':
            self._detail_label.setText("Waiting for the server to start the job…")
        else:
            self._detail_label.setText("")

        # Bound the progress value to [0, 100] so unexpected server values
        # don't crash the bar.
        pct = max(0, min(100, int(progress_pct or 0)))
        self._progress.setValue(pct)

        # Elapsed time — server-reported seconds is authoritative; we don't
        # second-guess with a local clock.
        self._elapsed_label.setText(f"Elapsed: {int(elapsed_seconds or 0)}s")

        # Pump the event loop so paint events apply and the Cancel button
        # registers clicks. The caller is in a blocking polling loop, so
        # without this the dialog freezes between updates.
        QApplication.processEvents()


class PreviewLayersCanceled(Exception):
    """Raised when the user cancels via the progress dialog."""
    pass
