# -*- coding: utf-8 -*-
"""
Claims wizard step widgets.

Each step is a separate widget that handles one phase of the claims workflow.

Steps:
    1. Project Setup - Configure claimant info, CRS, GeoPackage
    2. Claim Layout - Create/import claim polygons, generate grid
    3. Reference Point - Add optional reference points
    4. Monument Settings - Configure monument inset, LM corner default
    5. Adjust - Manipulate monument positions and LM corners (generates layers)
    6. Finalize - Process claims on server, generate documents
    7. Export - Export GPX, push to server
"""
from .step_base import ClaimsStepBase
from .step1_project_setup import ClaimsStep1Widget
from .step2_claim_layout import ClaimsStep2Widget
from .step3_reference_point import ClaimsStep3Widget
from .step4_monument import ClaimsStep4Widget
from .step5_adjust import ClaimsStep5AdjustWidget
from .step6_finalize import ClaimsStep6Widget
from .step7_export import ClaimsStep7Widget

__all__ = [
    'ClaimsStepBase',
    'ClaimsStep1Widget',
    'ClaimsStep2Widget',
    'ClaimsStep3Widget',
    'ClaimsStep4Widget',
    'ClaimsStep5AdjustWidget',
    'ClaimsStep6Widget',
    'ClaimsStep7Widget',
]
