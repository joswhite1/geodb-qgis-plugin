# -*- coding: utf-8 -*-
"""
Step 3 (Pay-per-claim): Order & Payment

Shows price summary, claimant info review, and purchase button.
Opens Stripe checkout in browser for pay-per-claim users.

This step replaces steps 3-7 of the enterprise/staff wizard flow
for users who do not have immediate processing access.
"""
from typing import List

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QMessageBox, QFrame, QScrollArea
)
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import QgsProject, QgsVectorLayer

from .step_base import ClaimsStepBase
from ...utils.compat import QFrame_NoFrame


class ClaimsStep3OrderWidget(ClaimsStepBase):
    """
    Order and payment step for pay-per-claim users.

    Shows price summary, claimant info review, and purchase button.
    Opens Stripe checkout in browser.
    """

    def get_step_title(self) -> str:
        return "Order & Payment"

    def get_step_description(self) -> str:
        return (
            "Review your order and complete payment. After payment, our team will "
            "process your claims and deliver documents by email."
        )

    def __init__(self, state, claims_manager, parent=None):
        super().__init__(state, claims_manager, parent)
        self._checkout_in_progress = False
        self._setup_ui()

    def _setup_ui(self):
        """Set up the step UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame_NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        layout.addWidget(self._create_header())

        # Order Summary group
        layout.addWidget(self._create_order_summary_group())

        # Claimant Info group (read-only review)
        layout.addWidget(self._create_claimant_review_group())

        # Purchase section
        layout.addWidget(self._create_purchase_group())

        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _create_order_summary_group(self) -> QGroupBox:
        """Create the order summary group box."""
        group = QGroupBox("Order Summary")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Summary table using form layout
        form = QFormLayout()
        form.setSpacing(8)

        self.claim_count_label = QLabel("--")
        self.claim_count_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        form.addRow("Number of Claims:", self.claim_count_label)

        self.price_per_claim_label = QLabel("--")
        self.price_per_claim_label.setStyleSheet("font-size: 14px;")
        form.addRow("Price per Claim:", self.price_per_claim_label)

        # Separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        separator.setStyleSheet("color: #e5e7eb;")

        self.total_price_label = QLabel("--")
        self.total_price_label.setStyleSheet(
            "font-weight: bold; font-size: 18px; color: #059669;"
        )
        form.addRow("Total:", self.total_price_label)

        layout.addLayout(form)
        layout.addWidget(separator)

        # Info note
        info_label = QLabel(
            "After payment, your claims will be queued for processing. "
            "Our team will generate location notices, corner certificates, "
            "and all required documents. You will receive them by email."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        return group

    def _create_claimant_review_group(self) -> QGroupBox:
        """Create the claimant info review group (read-only)."""
        group = QGroupBox("Claimant Information")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        form = QFormLayout()
        form.setSpacing(6)

        self.review_name_label = QLabel("--")
        form.addRow("Name:", self.review_name_label)

        self.review_address_label = QLabel("--")
        self.review_address_label.setWordWrap(True)
        form.addRow("Address:", self.review_address_label)

        self.review_district_label = QLabel("--")
        form.addRow("Mining District:", self.review_district_label)

        layout.addLayout(form)

        # Edit button to go back to Step 1
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.edit_claimant_btn = QPushButton("Edit Claimant Info")
        self.edit_claimant_btn.setStyleSheet(self._get_secondary_button_style())
        self.edit_claimant_btn.clicked.connect(self._on_edit_claimant)
        btn_layout.addWidget(self.edit_claimant_btn)

        layout.addLayout(btn_layout)

        return group

    def _create_purchase_group(self) -> QGroupBox:
        """Create the purchase button group."""
        group = QGroupBox("Complete Purchase")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(12)

        # Purchase button (large, prominent)
        self.purchase_btn = QPushButton("Purchase Claims")
        self.purchase_btn.setStyleSheet("""
            QPushButton {
                padding: 14px 32px;
                background-color: #059669;
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 16px;
                min-width: 200px;
                min-height: 48px;
            }
            QPushButton:hover {
                background-color: #047857;
            }
            QPushButton:pressed {
                background-color: #065f46;
            }
            QPushButton:disabled {
                background-color: #a7f3d0;
                color: #6b7280;
            }
        """)
        self.purchase_btn.clicked.connect(self._on_purchase_clicked)
        layout.addWidget(self.purchase_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(self.status_label)

        return group

    # =========================================================================
    # Data Methods
    # =========================================================================

    def _get_claim_count(self) -> int:
        """Get the number of claims from the layout layer."""
        if self.state.claims_layer_id:
            layer = QgsProject.instance().mapLayer(self.state.claims_layer_id)
            if layer and isinstance(layer, QgsVectorLayer) and layer.isValid():
                return layer.featureCount()
        return 0

    def _get_claims_from_layer(self) -> list:
        """Extract claim name and geometry from the layout layer."""
        claims = []
        if not self.state.claims_layer_id:
            return claims

        layer = QgsProject.instance().mapLayer(self.state.claims_layer_id)
        if not layer or not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            return claims

        for feature in layer.getFeatures():
            name = feature.attribute('name') if feature.fieldNameIndex('name') >= 0 else f"Claim {feature.id()}"
            geom = feature.geometry()
            if geom and not geom.isEmpty():
                claims.append({
                    'name': str(name) if name else f"Claim {feature.id()}",
                    'geometry': geom.asWkt()
                })

        return claims

    def _get_price_per_claim_cents(self) -> int:
        """Get the price per claim in cents from access_info."""
        if self.state.access_info:
            pricing = self.state.access_info.get('pricing', {})
            return pricing.get('price_per_claim_cents', 0)
        return 0

    def _update_summary(self):
        """Update the order summary display."""
        claim_count = self._get_claim_count()
        price_cents = self._get_price_per_claim_cents()
        price_dollars = price_cents / 100.0 if price_cents else 0
        total_cents = price_cents * claim_count
        total_dollars = total_cents / 100.0

        self.claim_count_label.setText(str(claim_count))

        if price_dollars > 0:
            self.price_per_claim_label.setText(f"${price_dollars:.2f}")
            self.total_price_label.setText(f"${total_dollars:.2f}")
            self.purchase_btn.setText(f"Purchase Claims \u2014 ${total_dollars:.2f}")
        else:
            self.price_per_claim_label.setText("Pricing not available")
            self.total_price_label.setText("--")
            self.purchase_btn.setText("Purchase Claims")

        self.purchase_btn.setEnabled(claim_count > 0 and price_cents > 0)

    def _update_claimant_review(self):
        """Update the claimant info review section."""
        self.review_name_label.setText(self.state.claimant_name or "--")

        # Build address display
        address_parts = []
        if self.state.address_line1:
            address_parts.append(self.state.address_line1)
        if self.state.address_line2:
            address_parts.append(self.state.address_line2)
        if self.state.address_line3:
            address_parts.append(self.state.address_line3)
        self.review_address_label.setText("\n".join(address_parts) if address_parts else "--")

        self.review_district_label.setText(self.state.mining_district or "--")

    # =========================================================================
    # Action Handlers
    # =========================================================================

    def _on_edit_claimant(self):
        """Navigate back to Step 1 to edit claimant info."""
        # Find the parent wizard and navigate to step 0
        wizard = self.parent()
        while wizard and not hasattr(wizard, 'go_to_step'):
            wizard = wizard.parent()

        if wizard and hasattr(wizard, 'go_to_step'):
            wizard.go_to_step(0)
        else:
            self.emit_status("Unable to navigate back - please use the Back button", "warning")

    def _on_purchase_clicked(self):
        """Handle the purchase button click."""
        if self._checkout_in_progress:
            return

        claims = self._get_claims_from_layer()
        if not claims:
            QMessageBox.warning(
                self,
                "No Claims",
                "No claims found in the layout layer. Please go back to Step 2 "
                "and create your claim layout."
            )
            return

        if not self.state.project_id:
            QMessageBox.warning(
                self,
                "No Project",
                "No project selected. Please ensure a project is selected."
            )
            return

        if not self.state.company_id:
            QMessageBox.warning(
                self,
                "No Company",
                "No company selected. Please ensure a company is selected."
            )
            return

        # Build claimant info dict
        claimant_info = None
        if self.state.claimant_name:
            claimant_info = {
                'claimant_name': self.state.claimant_name,
                'address_1': self.state.address_line1,
                'address_2': self.state.address_line2,
                'address_3': self.state.address_line3,
                'district': self.state.mining_district,
                'monument_type': self.state.monument_type,
            }

        self._checkout_in_progress = True
        self.purchase_btn.setEnabled(False)
        self.purchase_btn.setText("Creating checkout...")
        self.status_label.setText("Connecting to payment server...")
        self.status_label.setStyleSheet("font-size: 13px; color: #6b7280;")

        try:
            result = self.claims_manager.create_checkout_session(
                claims=claims,
                project_id=self.state.project_id,
                company_id=self.state.company_id,
                claimant_info=claimant_info,
                epsg=self.state.project_epsg or 4326
            )

            checkout_url = result.get('checkout_url')
            session_id = result.get('session_id', 'unknown')
            if not checkout_url:
                raise ValueError("No checkout URL returned from server")

            # Log checkout details for support debugging
            self.claims_manager.logger.info(
                f"[QCLAIMS] Checkout created: session_id={session_id}, "
                f"claim_count={len(claims)}, project={self.state.project_id}, "
                f"company={self.state.company_id}"
            )

            # Open checkout in browser
            QDesktopServices.openUrl(QUrl(checkout_url))

            # Show success message
            self.status_label.setText(
                "Payment page opened in your browser."
            )
            self.status_label.setStyleSheet("font-size: 13px; color: #059669;")

            QMessageBox.information(
                self,
                "Payment Opened",
                "Payment has been opened in your browser.\n\n"
                "After completing payment, your claims will be processed by our "
                "team and you'll receive your documents by email.\n\n"
                "You can close this wizard or start a new claims project."
            )

            self.emit_status("Checkout session created - payment opened in browser", "success")

        except Exception as e:
            error_msg = str(e)
            self.status_label.setText(f"Error: {error_msg}")
            self.status_label.setStyleSheet("font-size: 13px; color: #dc2626;")
            self.emit_status(f"Checkout failed: {error_msg}", "error")

            QMessageBox.critical(
                self,
                "Checkout Error",
                f"Failed to create checkout session:\n\n{error_msg}"
            )

        finally:
            self._checkout_in_progress = False
            self._update_summary()  # Re-enable button with proper text

    # =========================================================================
    # ClaimsStepBase Implementation
    # =========================================================================

    def validate(self) -> List[str]:
        """Validate the step including geometry and CRS checks."""
        errors = []

        claim_count = self._get_claim_count()
        if claim_count == 0:
            errors.append("No claims found - go back to Step 2 to create claims")
        else:
            # Validate that claims have valid geometry
            claims = self._get_claims_from_layer()
            empty_geom_count = sum(1 for c in claims if not c.get('geometry'))
            if empty_geom_count > 0:
                errors.append(
                    f"{empty_geom_count} claim(s) have empty geometry - "
                    f"check your layout layer in Step 2"
                )

        # Validate CRS is set and reasonable
        epsg = self.state.project_epsg
        if not epsg or epsg == 0:
            errors.append(
                "Project CRS (EPSG) is not set. Claim coordinates may be incorrect. "
                "Set the project CRS in Step 1 or ensure your QGIS project has a valid CRS."
            )

        price_cents = self._get_price_per_claim_cents()
        if price_cents <= 0:
            errors.append("Pricing information not available - refresh license in Step 1")

        if not self.state.project_id:
            errors.append("No project selected")

        if not self.state.company_id:
            errors.append("No company selected")

        return errors

    def on_enter(self):
        """Called when step becomes active."""
        self._update_summary()
        self._update_claimant_review()

    def on_leave(self):
        """Called when leaving step."""
        pass  # Nothing to save - this is a review/action step

    def save_state(self):
        """Save widget state to shared state."""
        pass  # No editable state in this step

    def load_state(self):
        """Load widget state from shared state."""
        self._update_summary()
        self._update_claimant_review()
