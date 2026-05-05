# -*- coding: utf-8 -*-
"""
Claims management for GeodbIO plugin.

Handles QClaims API interactions for:
- License checking (access level, pricing)
- Terms of Service acceptance
- Claims processing (for Enterprise/Staff users)
- Order submission (for Pay-per-claim users)
- Document generation and download
- Push to server (LandHoldings + ClaimStakes)
"""
from typing import Dict, Any, List, Optional
import json
import time

from ..api.client import APIClient
from ..api.exceptions import APIException, APIPermissionError
from ..utils.config import Config
from ..utils.logger import PluginLogger


class ClaimsManager:
    """
    Manages QClaims API interactions for the QGIS plugin.

    Supports two user paths:
    1. Enterprise/Staff: Immediate processing via process_claims()
    2. Pay-per-claim: Checkout via create_checkout_session()
    """

    def __init__(self, api_client: APIClient, config: Config):
        """
        Initialize claims manager.

        Args:
            api_client: API client instance
            config: Configuration instance
        """
        self.api = api_client
        self.config = config
        self.logger = PluginLogger.get_logger()

        # Cache license info to avoid repeated API calls
        self._cached_license: Optional[Dict[str, Any]] = None
        self._cached_tos: Optional[Dict[str, Any]] = None
        self._last_access_check: float = 0  # Timestamp of last access check
        self._access_check_cooldown: float = 2.0  # Minimum seconds between checks

    # =========================================================================
    # License & Access
    # =========================================================================

    def check_access(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Check user's QClaims access level.

        Returns access type (staff, enterprise_api, enterprise_integrated,
        pay_per_claim) and associated permissions/limits.

        Args:
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Dict with:
                - access_type: 'staff', 'enterprise_api', 'enterprise_integrated', 'pay_per_claim'
                - can_process_immediately: bool (True for staff/enterprise)
                - monthly_limit: int or None
                - claims_used_this_month: int
                - include_documents: bool
                - include_geodb_storage: bool
                - is_staff: bool
                - pricing: dict (for pay_per_claim users)

        Raises:
            APIException: If API call fails (including not authenticated)
        """
        # Skip API call if not authenticated - avoids spamming error logs
        if not self.api.token:
            raise APIException("Not authenticated - please login first")

        if self._cached_license and not force_refresh:
            return self._cached_license

        # Throttle rapid repeated calls even with force_refresh
        # This prevents UI event loops from flooding the server
        current_time = time.time()
        if force_refresh and (current_time - self._last_access_check) < self._access_check_cooldown:
            if self._cached_license:
                return self._cached_license
            # If no cache and still in cooldown, wait a tiny bit for any in-flight request
            # but don't block indefinitely
        self._last_access_check = current_time

        try:
            url = self.config.get_claims_url('check-access/')
            self.logger.info(f"[QCLAIMS] Checking access at URL: {url}")
            result = self.api._make_request('GET', url)
            self._cached_license = result
            self.logger.info(f"[QCLAIMS] Access check: {result.get('access_type', 'unknown')}")
            return result
        except APIException as e:
            self.logger.error(f"[QCLAIMS] Access check failed: {e}")
            raise

    def has_claims_access(self) -> bool:
        """Check if user has any QClaims access (licensed or staff)."""
        try:
            access = self.check_access()
            # All access types can use QClaims (even pay_per_claim, they just pay)
            return access.get('access_type') is not None
        except Exception:
            return False

    def can_process_immediately(self) -> bool:
        """Check if user can process claims without payment (staff/enterprise)."""
        try:
            access = self.check_access()
            return access.get('can_process_immediately', False)
        except Exception:
            return False

    def get_pricing(self) -> Dict[str, Any]:
        """
        Get current per-claim pricing for pay-per-claim users.

        Returns:
            Dict with:
                - self_service_per_claim_cents: int
                - full_service_per_claim_cents: int (if available)
        """
        try:
            access = self.check_access()
            return access.get('pricing', {})
        except Exception:
            return {}

    # =========================================================================
    # Terms of Service
    # =========================================================================

    def check_tos(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Check TOS acceptance status.

        Args:
            force_refresh: If True, bypass cache

        Returns:
            Dict with:
                - accepted: bool
                - current_version: str
                - accepted_version: str or None
                - accepted_at: str (ISO timestamp) or None

        Raises:
            APIException: If API call fails (including not authenticated)
        """
        # Skip API call if not authenticated - avoids spamming error logs
        if not self.api.token:
            raise APIException("Not authenticated - please login first")

        if self._cached_tos and not force_refresh:
            return self._cached_tos

        try:
            url = self.config.get_claims_url('tos-status/')
            result = self.api._make_request('GET', url)
            self._cached_tos = result
            return result
        except APIException as e:
            self.logger.error(f"[QCLAIMS] TOS check failed: {e}")
            raise

    def has_accepted_tos(self) -> bool:
        """Check if user has accepted current TOS version."""
        try:
            tos = self.check_tos()
            return tos.get('accepted', False)
        except Exception:
            return False

    def accept_tos(self) -> Dict[str, Any]:
        """
        Accept the QClaims Terms of Service.

        Returns:
            Dict with:
                - accepted: True
                - version: str
                - accepted_at: str (ISO timestamp)
        """
        try:
            url = self.config.get_claims_url('accept-tos/')
            result = self.api._make_request('POST', url, data={
                'accepted': True,
                'accepted_via': 'plugin'
            })

            # Update cache
            self._cached_tos = {
                'accepted': True,
                'current_version': result.get('version'),
                'accepted_version': result.get('version'),
                'accepted_at': result.get('accepted_at')
            }

            self.logger.info(f"[QCLAIMS] TOS accepted: v{result.get('version')}")
            return result
        except APIException as e:
            self.logger.error(f"[QCLAIMS] TOS acceptance failed: {e}")
            raise

    def get_tos_content(self) -> Dict[str, Any]:
        """
        Get TOS content for display.

        Returns:
            Dict with:
                - version: str
                - title: str
                - sections: list of {title, content}
        """
        try:
            url = self.config.get_claims_url('tos-content/')
            return self.api._make_request('GET', url)
        except APIException as e:
            self.logger.error(f"[QCLAIMS] Get TOS content failed: {e}")
            raise

    # =========================================================================
    # State Requirements
    # =========================================================================

    def get_state_info(self, state: str) -> Dict[str, Any]:
        """
        Get state-specific claim requirements.

        Args:
            state: Two-letter state abbreviation (e.g., 'NV', 'AZ')

        Returns:
            Dict with:
                - state: str
                - state_name: str
                - requirements: dict (monuments, deadlines, etc.)
                - fee_schedule: dict (BLM fees, county fees)
                - last_verified: str (ISO date)
        """
        try:
            url = self.config.get_claims_url(f'state-info/{state.upper()}/')
            return self.api._make_request('GET', url)
        except APIException as e:
            self.logger.error(f"[QCLAIMS] Get state info failed for {state}: {e}")
            raise

    # =========================================================================
    # Claims Processing (Enterprise/Staff)
    # =========================================================================

    def process_claims(
        self,
        claims: List[Dict[str, Any]],
        project_id: int,
        epsg: int = None,
        progress_parent=None,
    ) -> Dict[str, Any]:
        """
        Send claims to server for processing (Enterprise/Staff only).

        Pay-per-claim users will receive 403 with redirect to submit_order.

        Args:
            claims: List of claim dicts (see existing docstring).
            project_id: Target project ID.
            epsg: EPSG code for claim geometries (e.g., 26911 for UTM Zone 11N).
            progress_parent: Optional parent widget for the progress dialog.

        Returns:
            Same as before. Routes through the shared async-capable helper so
            blocks of 200+ claims don't trip Cloudflare's origin timeout.

        Raises:
            PermissionError: If user is pay-per-claim (must use submit_order)
        """
        # If no top-level EPSG provided, try to get it from the first claim
        if epsg is None and claims:
            epsg = claims[0].get('epsg')

        try:
            url = self.config.get_claims_url('process/')
            data = {
                'claims': claims,
                'project_id': project_id,
            }
            if epsg:
                data['epsg'] = epsg

            result = self.api.post_async_capable(
                url, data,
                progress_title="Processing claims",
                progress_parent=progress_parent,
            )

            self.logger.info(
                f"[QCLAIMS] Processed {len(result.get('claims', []))} claims "
                f"(session: {result.get('session_id', 'unknown')})"
            )
            return result

        except APIPermissionError:
            # Pay-per-claim users get 403 - this is expected
            self.logger.info("[QCLAIMS] Pay-per-claim user - use submit_order instead")
            raise
        except APIException as e:
            self.logger.error(f"[QCLAIMS] Process claims failed: {e}")
            raise

    # =========================================================================
    # Staff Order Fulfillment
    # =========================================================================

    def get_staff_pending_orders(self) -> Dict[str, Any]:
        """
        Get pending orders for staff fulfillment.

        Staff-only endpoint that returns orders needing processing:
        - ClaimPurchaseOrder (BLM Map): status in ['processing', 'pending']
        - ClaimOrder (QPlugin): status in ['paid', 'approved']

        Returns:
            Dict with:
                - orders: list of order dicts with:
                    - id: int
                    - order_type: 'claim_purchase' or 'claim_order'
                    - order_number: str
                    - status: str
                    - status_display: str
                    - created_at: str
                    - customer_email: str
                    - company_name: str
                    - project_id: int
                    - project_name: str
                    - claim_count: int
                    - claim_polygons: dict (GeoJSON)
                    - claimant_info: dict or None
                    - claim_package_id: int or None
                    - staking_service: bool
                    - expedited_delivery: bool
                - count: int

        Raises:
            APIException: If not staff or API call fails
        """
        try:
            url = self.config.get_claims_url('staff/pending-orders/')
            result = self.api._make_request('GET', url)

            count = result.get('count', len(result.get('orders', [])))
            self.logger.info(f"[QCLAIMS] Retrieved {count} pending orders for staff")
            return result

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Get staff pending orders failed: {e}")
            raise

    # =========================================================================
    # Proposed Claims (Admin-uploaded claims for staff workflow)
    # =========================================================================

    def get_projects_with_proposed_claims(
        self,
        approved_only: bool = False,
        pending_only: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Get list of projects that have proposed claims (staff only).

        Returns projects with admin-uploaded ProposedMiningClaim records
        that need processing by staff.

        Args:
            approved_only: If True, only include claims that have been user-approved
            pending_only: If True, only include claims that are pending approval

        Returns:
            List of project dicts with:
                - id: int
                - name: str
                - company_id: int
                - company_name: str
                - total_claims: int
                - approved_claims: int
                - pending_claims: int

        Raises:
            PermissionError: If user is not staff
            APIException: If API call fails
        """
        # Check if user is staff
        access = self.check_access()
        if not access.get('is_staff'):
            raise APIPermissionError("Staff access required to view proposed claims projects")

        try:
            # Build URL with query parameters
            params = []
            if approved_only:
                params.append('approved_only=true')
            elif pending_only:
                params.append('pending_only=true')

            # Use the v2 API endpoint for proposed claims (Knox token auth)
            # base_url is like https://api.geodb.io/api/v2
            base_url = self.config.base_url
            if not base_url:
                base_url = "https://api.geodb.io/api/v2"
                self.logger.warning("[QCLAIMS] base_url was empty, using default")
            # Build the proposed claims endpoint URL
            url = f"{base_url.rstrip('/')}/proposed-claims/projects/"
            if params:
                url += '?' + '&'.join(params)
            self.logger.info(f"[QCLAIMS] Fetching proposed claims projects from: {url}")

            result = self.api._make_request('GET', url)

            if result.get('success'):
                projects = result.get('projects', [])
                self.logger.info(
                    f"[QCLAIMS] Found {len(projects)} projects with proposed claims"
                )
                return projects
            else:
                raise APIException(result.get('error', 'Unknown error'))

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Get projects with proposed claims failed: {e}")
            raise

    def get_proposed_claims(self, project_id: int) -> Dict[str, Any]:
        """
        Get proposed claims for a specific project (staff only).

        Returns ProposedMiningClaim records as GeoJSON features.

        Args:
            project_id: Project ID to get proposed claims for

        Returns:
            Dict with:
                - project: dict with id, name, company_id, company_name
                - proposed_claims: list of GeoJSON Feature dicts with:
                    - type: 'Feature'
                    - id: int
                    - properties: dict with claim_name, claim_type, acreage, etc.
                    - geometry: GeoJSON geometry
                - counts: dict with total, approved, pending

        Raises:
            PermissionError: If user is not staff and doesn't have project access
            APIException: If API call fails
        """
        try:
            # Use the v2 API endpoint for proposed claims (Knox token auth)
            # base_url is like https://api.geodb.io/api/v2
            base_url = self.config.base_url
            if not base_url:
                base_url = "https://api.geodb.io/api/v2"
            url = f"{base_url.rstrip('/')}/proposed-claims/project/{project_id}/"
            self.logger.info(f"[QCLAIMS] Fetching proposed claims for project {project_id} from: {url}")

            result = self.api._make_request('GET', url)

            if result.get('success'):
                claims = result.get('proposed_claims', [])
                self.logger.info(
                    f"[QCLAIMS] Retrieved {len(claims)} proposed claims for project {project_id}"
                )
                return result
            else:
                raise APIException(result.get('error', 'Unknown error'))

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Get proposed claims failed for project {project_id}: {e}")
            raise

    # =========================================================================
    # Document Generation
    # =========================================================================

    def generate_documents(
        self,
        claims: List[Dict[str, Any]],
        waypoints: Optional[List[Dict[str, Any]]] = None,
        document_types: Optional[List[str]] = None,
        project_id: int = None,
        save_to_project: bool = True,
        claimant_info: Optional[Dict[str, Any]] = None,
        claim_prefix: Optional[str] = None,
        order_id: Optional[int] = None,
        order_type: Optional[str] = None,
        reference_points: Optional[List[Dict[str, Any]]] = None,
        claim_package_id: Optional[int] = None,
        input_epsg: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Request document generation for processed claims.

        Args:
            claims: List of processed claim data (from process_claims result).
                Each claim dict should include a 'notes' field (str) for per-claim
                notes to be rendered on location notice documents.
            waypoints: List of waypoints from process_claims result. Required for
                sorted corner certificates - corners are matched to waypoints by
                coordinate proximity and sorted by waypoint index for field work.
            document_types: List of document types to generate:
                - 'location_notice' (default)
                - 'corner_certificate'
                - 'certificate_of_location' (Nevada only)
            project_id: Target project ID (required to save documents)
            save_to_project: If True, saves documents as project documents
            claimant_info: Dict with claimant/agent details for location notices:
                - claimant_name: Name of claimant/locator
                - address_1, address_2, address_3: Claimant address lines
                - agent_name: Name of agent (if different from claimant)
                - agent_address_1, agent_address_2, agent_address_3: Agent address
                - return_name: Name for return mail (if different)
                - return_address_1, return_address_2, return_address_3: Return address
                - district: Mining district name
                - claim_type: 'lode' or 'placer'
                - monument_type: Description of monument type
            claim_prefix: Optional prefix for document filenames (e.g., "TG" -> "TG_Claims_Notices.docx")
            order_id: Optional order ID for staff fulfillment. When provided, links
                documents to the existing order/ClaimPackage instead of creating new.
            order_type: Optional order type for staff fulfillment ('claim_purchase' or 'claim_order')
            reference_points: Optional list of reference points for bearing/distance
                calculations in location notices. Each dict should have:
                - name: str (e.g., "Road Junction", "Section Corner")
                - easting: float (UTM easting in meters)
                - northing: float (UTM northing in meters)
            claim_package_id: Optional ID of an existing ClaimPackage to add documents to.
                When provided, documents are added to this package instead of creating
                a new one. Use this to prevent duplicate packages when regenerating
                documents (e.g., when the user goes back in the wizard and regenerates).
            input_epsg: EPSG code of input coordinates (e.g., 26911 for NAD83 UTM Zone 11N).
                Used for CRS display in location notice reference point coordinates.

        Returns:
            Dict with:
                - documents: list of {filename, document_type, download_url, document_id}
                - package: dict with package info (when save_to_project=True)
        """
        if document_types is None:
            document_types = ['location_notice']

        try:
            url = self.config.get_claims_url('documents/')
            data = {
                'claims': claims,
                'waypoints': waypoints or [],
                'document_types': document_types
            }
            # Include project info for saving documents to project
            if project_id and save_to_project:
                data['project_id'] = project_id
                data['save_to_project'] = True

            # Include claimant info for location notices
            if claimant_info:
                data['claimant_info'] = claimant_info

            # Include claim prefix for filename prefixing
            if claim_prefix:
                data['claim_prefix'] = claim_prefix

            # Include reference points for bearing/distance in location notices
            if reference_points:
                data['reference_points'] = reference_points
                self.logger.info(
                    f"[QCLAIMS] Including {len(reference_points)} reference points for document generation"
                )

            # Include order context for staff fulfillment
            # This links documents to existing order/package instead of creating new
            if order_id and order_type:
                data['order_id'] = order_id
                data['order_type'] = order_type
                self.logger.info(
                    f"[QCLAIMS] Generating documents for order fulfillment: "
                    f"{order_type} #{order_id}"
                )

            # Include existing claim_package_id to prevent duplicate packages
            # when regenerating documents (e.g., user goes back in wizard)
            if claim_package_id:
                data['claim_package_id'] = claim_package_id
                self.logger.info(
                    f"[QCLAIMS] Using existing claim_package_id={claim_package_id} "
                    f"to prevent duplicate package creation"
                )

            # Include EPSG code for CRS display in location notice documents
            if input_epsg:
                data['input_epsg'] = input_epsg

            result = self.api._make_request('POST', url, data=data)

            doc_count = len(result.get('documents', []))
            saved_info = " (saved to project)" if project_id and save_to_project else ""
            order_info = f" (fulfilling {order_type} #{order_id})" if order_id else ""
            self.logger.info(f"[QCLAIMS] Generated {doc_count} documents{saved_info}{order_info}")
            return result

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Generate documents failed: {e}")
            raise

    def link_documents_to_landholdings(
        self,
        document_ids: List[int],
        landholding_names: List[str],
        project_id: int
    ) -> Dict[str, Any]:
        """
        Link existing documents to land holdings by name.

        Args:
            document_ids: List of document IDs to link
            landholding_names: List of landholding names to link to
            project_id: Project ID for finding landholdings

        Returns:
            Dict with linking results
        """
        try:
            # Use v1 API for landholdings endpoint
            base = self.config.base_url
            if '/v2' in base:
                base = base.replace('/v2', '/v1')
            elif '/api/v1' not in base:
                base = base.rstrip('/') + '/api/v1' if not base.endswith('/api/v1') else base

            url = f"{base}/landholdings/link-documents/"
            result = self.api._make_request('POST', url, data={
                'document_ids': document_ids,
                'landholding_names': landholding_names,
                'project_id': project_id
            })

            self.logger.info(
                f"[QCLAIMS] Linked {result.get('documents_linked', 0)} documents "
                f"to {result.get('landholdings_updated', 0)} landholdings"
            )
            return result

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Link documents failed: {e}")
            raise

    # =========================================================================
    # Push to Server
    # =========================================================================

    def push_to_server(
        self,
        claims: List[Dict[str, Any]],
        stakes: List[Dict[str, Any]],
        project_id: int,
        epsg: int = None,
        claim_package_id: int = None
    ) -> Dict[str, Any]:
        """
        Push processed claims to server as LandHoldings + ClaimStakes.

        Uses existing bulk upsert endpoints.

        Args:
            claims: List of processed claim data
            stakes: List of waypoint/stake data
            project_id: Target project ID
            epsg: EPSG code for UTM coordinates (e.g., 26911 for UTM Zone 11N)
            claim_package_id: Optional ClaimPackage ID to link claims to.
                When provided, the server links LandHoldings to this existing
                package instead of creating a new one. This prevents duplicate
                packages when documents are generated before claims are pushed.

        Returns:
            Dict with:
                - landholdings: bulk upsert result
                - stakes: bulk upsert result
        """
        # Validate project_id is a positive integer
        if not isinstance(project_id, int) or project_id <= 0:
            raise ValueError(f"Invalid project_id: {project_id}")

        try:
            self.logger.debug(f"[QCLAIMS] push_to_server: {len(claims)} claims, {len(stakes)} stakes, project_id={project_id}")
            self.logger.info(
                f"[QCLAIMS] push_to_server: {len(claims)} claims, {len(stakes)} stakes, "
                f"project_id={project_id}, epsg={epsg}, claim_package_id={claim_package_id}"
            )

            # Format claims as LandHolding records
            landholding_records = [
                self._format_landholding(claim, project_id, epsg, claim_package_id)
                for claim in claims
            ]

            # Format waypoints as ClaimStake records
            stake_records = [
                self._format_stake(stake, project_id, claim_package_id)
                for stake in stakes
            ]

            self.logger.debug(f"[QCLAIMS] Formatted {len(landholding_records)} LandHoldings, {len(stake_records)} ClaimStakes")
            self.logger.info(
                f"[QCLAIMS] Formatted {len(landholding_records)} LandHoldings, "
                f"{len(stake_records)} ClaimStakes"
            )

            # Use existing bulk upsert endpoints
            # IMPORTANT: Push ClaimStakes FIRST, then LandHoldings
            # The LandHolding serializer's _auto_link_stakes() needs the stakes
            # to already exist so it can link them by coordinate matching
            landholdings_result = {}
            stakes_result = {}

            if stake_records:
                # Push stakes FIRST so they exist when LandHoldings are created.
                # cleanup_orphans=True opts into the server's post-upsert cleanup:
                # when claims are re-processed, sequence numbers may change due to
                # nearest-neighbor sorting. The server hard-deletes any Planned
                # stakes in this project+package whose sequence_number isn't in
                # this push. ONLY safe here because the QGIS plugin pushes the
                # full authoritative set after re-processing — a partial push
                # (e.g. a mobile field-worker sync) would catastrophically wipe
                # every other PL stake. The server defaults the flag to False.

                # Debug: Log first stake record to verify format
                if stake_records:
                    first_stake = stake_records[0]
                    self.logger.debug(f"[QCLAIMS] First stake record: {first_stake}")
                    self.logger.info(f"[QCLAIMS] First stake: seq={first_stake.get('sequence_number')}, "
                                     f"project={first_stake.get('project')}, "
                                     f"claim_package={first_stake.get('claim_package')}")

                stakes_result = self.api.bulk_upsert_records(
                    'ClaimStake',
                    stake_records,
                    cleanup_orphans=True
                )
                self.logger.info(
                    f"[QCLAIMS] Pushed {len(stake_records)} ClaimStakes via bulk upsert"
                )

                # Debug: Log upsert result summary
                summary = stakes_result.get('summary', {})
                self.logger.debug(
                    f"[QCLAIMS] Stakes result: created={summary.get('created')}, "
                    f"updated={summary.get('updated')}, errors={summary.get('errors')}, "
                    f"orphans_deleted={stakes_result.get('orphan_stakes_deleted', 0)}"
                )
            else:
                self.logger.warning("[QCLAIMS] No stake_records to push")

            if landholding_records:
                # Push LandHoldings AFTER stakes - auto-linking will find the stakes
                landholdings_result = self.api.bulk_upsert_records(
                    'LandHolding',
                    landholding_records
                )
                self.logger.info(
                    f"[QCLAIMS] Pushed {len(landholding_records)} LandHoldings"
                )

            return {
                'landholdings': landholdings_result,
                'stakes': stakes_result
            }

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Push to server failed: {e}")
            raise

    def link_geopackage(
        self,
        claim_package_id: int,
        project_file_id: int,
    ) -> Dict[str, Any]:
        """
        Link a ProjectFile (GeoPackage) to a ClaimPackage on the server.

        Called after uploading the claims GeoPackage as a ProjectFile.

        Args:
            claim_package_id: Server-side ClaimPackage ID
            project_file_id: Server-side ProjectFile ID of the uploaded GeoPackage

        Returns:
            Server response with package info and linked geopackage details
        """
        url = self.config.get_claims_url(
            f'packages/{claim_package_id}/link-geopackage/'
        )
        self.logger.info(
            f"[QCLAIMS] Linking GeoPackage ProjectFile {project_file_id} "
            f"to ClaimPackage {claim_package_id}"
        )
        return self.api._make_request(
            'PATCH', url, data={'project_file_id': project_file_id}
        )

    def upload_package_document(
        self,
        claim_package_id: int,
        file_path: str,
        document_type: str = 'other',
        title: str = '',
        description: str = '',
    ) -> Dict[str, Any]:
        """
        Upload a document file to a ClaimPackage on the server.

        Creates a Document record and links it to the package and its claims.

        Args:
            claim_package_id: Server-side ClaimPackage ID
            file_path: Local path to the file to upload
            document_type: One of: location_notice, blm_filing, county_recording,
                survey, noith, field_map, qclaims_export, work_package, other
            title: Display title (defaults to filename on server)
            description: Optional description

        Returns:
            Server response with created document info
        """
        url = self.config.get_claims_url(
            f'packages/{claim_package_id}/upload-document/'
        )

        fields = {
            'document_type': document_type,
        }
        if title:
            fields['title'] = title
        if description:
            fields['description'] = description

        self.logger.info(
            f"[QCLAIMS] Uploading document '{file_path}' ({document_type}) "
            f"to ClaimPackage {claim_package_id}"
        )

        return self.api.upload_file(url, file_path, fields, file_field_name='file')

    def list_package_documents(
        self,
        claim_package_id: int,
    ) -> Dict[str, Any]:
        """
        List all documents in a ClaimPackage.

        Args:
            claim_package_id: Server-side ClaimPackage ID

        Returns:
            Dict with 'documents' list and package info
        """
        url = self.config.get_claims_url(
            f'packages/{claim_package_id}/documents/'
        )
        return self.api._make_request('GET', url)

    def generate_package_maps(
        self,
        claim_package_id: int,
        regenerate: bool = False,
    ) -> Dict[str, Any]:
        """Trigger server-side claim-map rendering for a package.

        Pairs with the local QGIS print-layout generation in step 7 — the
        plugin produces the editable layouts client-side, then calls this
        endpoint to also persist polished server-side renders (per-state
        filing + field + location maps with Vision QC where enabled) as
        ClaimPackageDocument(document_type='field_map') rows.

        Args:
            claim_package_id: Server-side ClaimPackage ID
            regenerate: If True, soft-delete prior auto-generated maps and
                render fresh. If False (default), server short-circuits with
                already-generated documents when present.

        Returns:
            Dict with 'generated' (list of {document_id, title, download_url, ...}),
            'skipped_already_generated' (bool), and package info.
        """
        url = self.config.get_claims_url(
            f'packages/{claim_package_id}/generate-maps/'
        )
        self.logger.info(
            f"[QCLAIMS] Triggering server-side map generation for "
            f"ClaimPackage {claim_package_id} (regenerate={regenerate})"
        )
        return self.api._make_request(
            'POST', url, data={'regenerate': bool(regenerate)},
        )

    def _format_landholding(
        self,
        claim: Dict[str, Any],
        project_id: int,
        epsg: int = None,
        claim_package_id: int = None
    ) -> Dict[str, Any]:
        """Format processed claim for LandHolding bulk upsert.

        Args:
            claim: Processed claim data from server
            project_id: Target project ID
            epsg: EPSG code for UTM coordinates (e.g., 26911 for UTM Zone 11N)
            claim_package_id: Optional ClaimPackage ID to link this claim to
        """
        # Get WGS84 geometry for the geometry field (stored in DB as WGS84)
        geometry = claim.get('geometry') or claim.get('rotated_geometry')

        if not geometry:
            # Build GeoJSON polygon from corners using lat/lon (WGS84)
            corners = claim.get('corners', [])
            if corners and len(corners) >= 3:
                # Create closed polygon ring (first point repeated at end)
                coords = [[c.get('lon'), c.get('lat')] for c in corners]
                # Close the ring if not already closed
                if coords and coords[0] != coords[-1]:
                    coords.append(coords[0])
                geometry = {
                    'type': 'Polygon',
                    'coordinates': [coords]
                }

        # Get UTM geometry for manual_geometry field (preserves original precision)
        # This avoids round-trip conversion errors from WGS84 back to UTM
        geometry_utm = claim.get('rotated_geometry_utm')
        manual_geometry = None
        if geometry_utm:
            # Format as WKT-style string for manual_geometry field
            coords = geometry_utm.get('coordinates', [[]])[0]
            if coords:
                coord_str = ', '.join([f"{c[0]} {c[1]}" for c in coords])
                manual_geometry = f"POLYGON(({coord_str}))"

        # Build qclaims_data with all processing info
        # Use empty lists as defaults for list fields to avoid null values
        qclaims_data = {
            'processing_id': claim.get('session_id'),
            'processed_at': claim.get('processed_at'),
            'plss': claim.get('plss'),
            'corners': claim.get('corners') or [],
            'discovery_monument': claim.get('discovery_monument'),
            'sideline_monuments': claim.get('sideline_monuments') or [],
            'endline_monuments': claim.get('endline_monuments') or [],
            'calculated_acreage': claim.get('calculated_acreage'),
            'deadlines': claim.get('deadlines'),
            'state_requirements_snapshot': claim.get('state_requirements'),
        }

        # Include claim_package_id if provided - this links the LandHolding
        # to an existing ClaimPackage instead of letting server create a new one
        if claim_package_id:
            qclaims_data['claim_package_id'] = claim_package_id

        payload = {
            'name': claim.get('name'),
            'geometry': geometry,
            'manual_geometry': manual_geometry,  # Original UTM coords
            'epsg': epsg,  # EPSG code for manual_geometry
            'project': project_id,
            'source': 'qclaims',
            'claim_status': 'PL',  # Planned
            'state': claim.get('state'),
            'county': claim.get('county'),
            'qclaims_data': qclaims_data
        }

        # Filter through schema to ensure only expected fields are sent
        from ..models.schemas import get_schema
        schema = get_schema('LandHolding')
        if schema:
            payload = schema.filter_for_push(payload)

        return payload

    def _format_stake(self, stake: Dict[str, Any], project_id: int,
                      claim_package_id: int = None) -> Dict[str, Any]:
        """Format waypoint for ClaimStake upsert.

        Server waypoint format (from _deduplicate_waypoints):
        {
            'type': 'corner' | 'discovery' | 'sideline' | 'endline',
            'claim': 'TST 1',           # Claim name (single claim)
            'claims': ['TST 1', 'TST 2'],  # All claims sharing this waypoint
            'sequence_number': 'WP 1',  # Server-assigned sequence number
            'lat': 47.123456,
            'lon': -115.123456,
            'easting': 500000.0,        # UTM coordinates
            'northing': 5000000.0,
            'symbol': 'City (Medium)',  # GPX symbol
            'wp_id': uuid,              # Unique identifier
            'shared': bool,             # True if shared by multiple claims
        }

        ClaimStake model requires:
        - sequence_number: Waypoint reference ID (e.g., "WP 1", "LM 3")
        - stake_type: 'WP' (corner), 'LM' (location monument), 'SL' (sideline), 'EL' (endline), 'WT' (witness)
        - target_latitude, target_longitude: Planned coordinates
        """
        stake_type_value = stake.get('type', 'corner')
        if stake_type_value == 'discovery':
            stake_type = 'LM'  # Location Monument
        elif stake_type_value == 'sideline':
            stake_type = 'SL'  # Sideline Monument (Wyoming)
        elif stake_type_value == 'endline':
            stake_type = 'EL'  # Endline Monument (Arizona)
        elif stake_type_value == 'witness':
            stake_type = 'WT'  # Witness Point
        else:
            stake_type = 'WP'  # Corner Waypoint

        # Get sequence number - server returns 'sequence_number' from _deduplicate_waypoints
        # Fall back to 'name' for backward compatibility, then construct if needed
        sequence_number = stake.get('sequence_number') or stake.get('name')
        if not sequence_number:
            # Construct sequence number from claim and type
            claim_name = stake.get('claim', 'Claim')
            if stake_type == 'LM':
                sequence_number = f"LM {claim_name}"
            else:
                corner_num = stake.get('corner_number', '1')
                sequence_number = f"WP {corner_num}"

        record = {
            'project': project_id,
            'sequence_number': sequence_number,
            'stake_type': stake_type,
            'target_latitude': stake.get('lat'),
            'target_longitude': stake.get('lon'),
            'target_easting': stake.get('easting'),
            'target_northing': stake.get('northing'),
            'status': 'PL',  # Planned
            'gpx_symbol': stake.get('symbol', 'Flag, Blue'),
            # Note: ClaimStake has no 'name' field - the sequence_number IS the identifier
        }

        # Scope to claim_package so multiple claim groups can coexist
        if claim_package_id is not None:
            record['claim_package'] = claim_package_id

        # Store witness metadata so server can link witness to parent claims
        if stake_type == 'WT':
            witnessed_stakes = stake.get('witnessed_stakes', [])
            if witnessed_stakes:
                record['extra_data'] = {
                    'witnessed_stakes': witnessed_stakes,
                    'on_boundary': stake.get('on_boundary', False),
                    'shared': stake.get('shared', False),
                }

        # Filter through schema to ensure only expected fields are sent
        from ..models.schemas import get_schema
        schema = get_schema('ClaimStake')
        if schema:
            record = schema.filter_for_push(record)

        return record

    def _push_stakes_individually(self, stake_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Push stakes individually (DEPRECATED - use bulk_upsert_records instead).

        Note: This method does NOT trigger orphan cleanup. When claims are
        re-processed, sequence numbers may change and this method will create
        duplicate stakes instead of updating existing ones.

        The bulk upsert endpoint (api.bulk_upsert_records('ClaimStake', ...))
        includes orphan cleanup logic that handles this correctly.

        This method is kept for backward compatibility but should not be used
        for normal operations.
        """
        self.logger.debug(f"[QCLAIMS] _push_stakes_individually called with {len(stake_records)} records")
        self.logger.info(f"[QCLAIMS] Pushing {len(stake_records)} ClaimStakes individually")

        results = []
        errors = []

        for i, record in enumerate(stake_records):
            try:
                self.logger.debug(f"[QCLAIMS] Pushing stake {i +1}/{len(stake_records)}: {record.get('sequence_number')}")
                self.logger.info(
                    f"[QCLAIMS] Pushing stake {i +1}/{len(stake_records)}: "
                    f"seq={record.get('sequence_number')}, "
                    f"target_lat={record.get('target_latitude')}, "
                    f"target_lon={record.get('target_longitude')}"
                )
                result = self.api.upsert_record('ClaimStake', record)
                results.append(result)
                self.logger.debug(f"[QCLAIMS] Stake {record.get('sequence_number')} SUCCESS")
                self.logger.info(f"[QCLAIMS] Stake {record.get('sequence_number')} created/updated successfully")
            except APIException as e:
                self.logger.error(f"[QCLAIMS] Failed to push stake {record.get('sequence_number')}: {e}")
                errors.append({'record': record, 'error': str(e)})
            except Exception as e:
                self.logger.error(f"[QCLAIMS] Unexpected error pushing stake {record.get('sequence_number')}: {e}")
                errors.append({'record': record, 'error': str(e)})

        self.logger.info(f"[QCLAIMS] Stakes push complete: {len(results)} created, {len(errors)} errors")

        return {
            'results': results,
            'errors': errors,
            'summary': {
                'created': len(results),
                'errors': len(errors)
            }
        }

    # =========================================================================
    # Preview Layers (Server-side Layer Generation)
    # =========================================================================

    # Async claims job plumbing now lives on `APIClient.post_async_capable`
    # so processors and managers share one helper. This wrapper stays for
    # callers that pre-existed the refactor.
    def _post_preview_layers_async(
        self,
        url: str,
        data: Dict[str, Any],
        progress_title: Optional[str] = None,
        progress_parent=None,
    ) -> Dict[str, Any]:
        """
        Thin wrapper around the shared `APIClient.post_async_capable` so
        existing manager call sites keep working. New callers should use
        `self.api.post_async_capable(...)` directly.
        """
        return self.api.post_async_capable(
            url, data,
            progress_title=progress_title,
            progress_parent=progress_parent,
        )

    def get_preview_layers(
        self,
        claims: List[Dict[str, Any]],
        epsg: int,
        monument_inset_ft: float = 25.0,
        state: Optional[str] = None,
        progress_parent=None,
    ) -> Dict[str, Any]:
        """
        Get preview layers from server for QGIS visualization.

        This calls the server API to perform all proprietary calculations
        for corner points, LM corners, centerlines, and monuments.

        Args:
            claims: List of claim dicts with name, geometry, lm_corner
            epsg: EPSG code of input coordinates
            monument_inset_ft: Monument inset distance in feet
            state: Optional state override for monument type
            progress_parent: Parent widget for the progress dialog (e.g. the
                wizard). When omitted the dialog stays parentless.

        Returns:
            Dict with:
                - session_id: str
                - epsg: int
                - layers: dict with corner_points, lm_corners, centerlines, monuments, etc.
                - claims: list with rotated geometries
                - summary: dict with counts
        """
        try:
            url = self.config.get_claims_url('preview-layers/')
            data = {
                'claims': claims,
                'epsg': epsg,
                'monument_inset_ft': monument_inset_ft
            }
            if state:
                data['state'] = state

            result = self._post_preview_layers_async(
                url, data,
                progress_title="Generating preview layers",
                progress_parent=progress_parent,
            )

            self.logger.info(
                f"[QCLAIMS] Got preview layers: "
                f"{result.get('summary', {}).get('corner_points', 0)} corners, "
                f"{result.get('summary', {}).get('monuments', 0)} monuments"
            )
            return result

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Get preview layers failed: {e}")
            raise

    def update_lm_corner_with_layers(
        self,
        claims: List[Dict[str, Any]],
        epsg: int,
        monument_inset_ft: float = 25.0,
        progress_parent=None,
    ) -> Dict[str, Any]:
        """
        Update LM corners and get refreshed preview layers.

        When the user changes an LM corner value, this sends the change
        to the server which rotates the geometry and recalculates all layers.

        Args:
            claims: List of claim dicts with name, geometry, lm_corner (updated value)
            epsg: EPSG code of input coordinates
            monument_inset_ft: Monument inset distance in feet
            progress_parent: Parent widget for the progress dialog.

        Returns:
            Same as get_preview_layers, with rotated geometries
        """
        try:
            url = self.config.get_claims_url('update-lm-corner-layers/')
            data = {
                'claims': claims,
                'epsg': epsg,
                'monument_inset_ft': monument_inset_ft
            }

            result = self._post_preview_layers_async(
                url, data,
                progress_title="Updating LM corner",
                progress_parent=progress_parent,
            )

            self.logger.info(
                f"[QCLAIMS] Updated LM corners and got new layers for "
                f"{len(result.get('claims', []))} claims"
            )
            return result

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Update LM corner with layers failed: {e}")
            raise

    def update_monument_position(
        self,
        claim_name: str,
        monument_type: str,
        new_easting: float,
        new_northing: float,
        epsg: int
    ) -> Dict[str, Any]:
        """
        Update monument position and get converted coordinates.

        When a user manually moves a monument in QGIS, this validates
        the position and returns WGS84 coordinates.

        Args:
            claim_name: Name of the claim
            monument_type: 'discovery', 'sideline', or 'endline'
            new_easting: New X coordinate
            new_northing: New Y coordinate
            epsg: EPSG code of coordinates

        Returns:
            Dict with:
                - claim_name: str
                - monument: dict with type, easting, northing, lat, lon
                - message: str
        """
        try:
            url = self.config.get_claims_url('update-monument/')
            data = {
                'claim_name': claim_name,
                'monument_type': monument_type,
                'new_easting': new_easting,
                'new_northing': new_northing,
                'epsg': epsg
            }

            result = self.api._make_request('POST', url, data=data)

            self.logger.info(
                f"[QCLAIMS] Updated monument position for {claim_name}"
            )
            return result

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Update monument position failed: {e}")
            raise

    # =========================================================================
    # ClaimPackage Validation
    # =========================================================================

    def get_package_status(self, package_id: int) -> Optional[Dict[str, Any]]:
        """
        Check if a ClaimPackage exists on the server and get its status.

        Used to validate that a stored claim_package_id is still valid before
        trying to reuse it. Returns package info including soft-delete status.

        Args:
            package_id: ClaimPackage ID to check

        Returns:
            Dict with package info if found:
                - exists: True
                - mark_deleted: bool (True if soft-deleted)
                - package_number: str
                - name: str
            None if package not found (hard deleted or never existed)
        """
        if not package_id:
            return None

        try:
            # Use the packages detail endpoint - include deleted packages
            url = self.config.get_claims_url(f'packages/{package_id}/?include_deleted=true')
            result = self.api._make_request('GET', url)

            package_info = {
                'exists': True,
                'mark_deleted': result.get('mark_deleted', False),
                'package_number': result.get('package_number', ''),
                'name': result.get('name', ''),
            }
            self.logger.info(
                f"[QCLAIMS] Package {package_id} found: "
                f"mark_deleted={package_info['mark_deleted']}"
            )
            return package_info

        except APIException as e:
            # 404 means package doesn't exist (hard deleted)
            self.logger.info(
                f"[QCLAIMS] Package {package_id} not found on server: {e}"
            )
            return None

    def restore_package(self, package_id: int) -> bool:
        """
        Restore a soft-deleted ClaimPackage on the server.

        Args:
            package_id: ClaimPackage ID to restore

        Returns:
            True if restored successfully, False otherwise
        """
        if not package_id:
            return False

        try:
            url = self.config.get_claims_url(f'packages/{package_id}/restore/')
            self.api._make_request('POST', url)
            self.logger.info(f"[QCLAIMS] Restored package {package_id}")
            return True

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Failed to restore package {package_id}: {e}")
            return False

    # =========================================================================
    # Staff ClaimPackage Pull (for incomplete packages)
    # =========================================================================

    def get_incomplete_packages(self) -> List[Dict[str, Any]]:
        """
        Get list of incomplete ClaimPackages (staff only).

        Returns packages with status: draft, in_progress, staking, filing.
        These are packages that can be pulled into QGIS for field work.

        Returns:
            List of package dicts with:
                - id: int
                - name: str
                - package_number: str
                - status: str
                - status_display: str
                - source: str
                - project: dict with id, name, company
                - land_holding_count: int
                - claim_stake_count: int
                - date_created: str (ISO timestamp)
                - last_edited: str (ISO timestamp)

        Raises:
            PermissionError: If user is not staff
            APIException: If API call fails
        """
        # Check if user is staff
        access = self.check_access()
        if not access.get('is_staff'):
            raise APIPermissionError("Staff access required to pull ClaimPackages")

        try:
            url = self.config.get_claims_url('packages/incomplete/')
            result = self.api._make_request('GET', url)

            packages = result.get('packages', [])
            self.logger.info(f"[QCLAIMS] Found {len(packages)} incomplete packages")
            return packages

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Get incomplete packages failed: {e}")
            raise

    def pull_package(self, package_id: int) -> Dict[str, Any]:
        """
        Pull a ClaimPackage with all LandHoldings and ClaimStakes.

        Staff-only method for pulling package data into QGIS for field work
        or geometry adjustments.

        Args:
            package_id: ClaimPackage ID to pull

        Returns:
            Dict with:
                - package: dict with id, name, package_number, status, source, description
                - project: dict with id, name, company, epsg, blender_origin_epsg
                - land_holdings: list of land holding dicts with geometry, qclaims_data, etc.
                - claim_stakes: list of claim stake dicts with coordinates, claim_links, etc.

        Raises:
            PermissionError: If user is not staff
            APIException: If API call fails or package not found
        """
        # Check if user is staff
        access = self.check_access()
        if not access.get('is_staff'):
            raise APIPermissionError("Staff access required to pull ClaimPackages")

        try:
            url = self.config.get_claims_url(f'packages/{package_id}/pull/')
            result = self.api._make_request('GET', url)

            pkg_name = result.get('package', {}).get('name', 'unknown')
            lh_count = len(result.get('land_holdings', []))
            stake_count = len(result.get('claim_stakes', []))

            self.logger.info(
                f"[QCLAIMS] Pulled package '{pkg_name}': "
                f"{lh_count} land holdings, {stake_count} stakes"
            )
            return result

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Pull package {package_id} failed: {e}")
            raise

    # =========================================================================
    # Direct Checkout (Pay-per-claim via web purchase endpoint)
    # =========================================================================

    def create_checkout_session(
        self,
        claims: List[Dict[str, Any]],
        project_id: int,
        company_id: int,
        claimant_info: Dict[str, Any] = None,
        epsg: int = 4326
    ) -> Dict[str, Any]:
        """
        Create Stripe checkout session via the unified web purchase endpoint.

        This is used by pay-per-claim users to purchase claims. After payment,
        the claims appear in the admin processing queue as ProposedMiningClaim records.
        Staff then pulls and processes them through the plugin wizard.

        Args:
            claims: List of claim dicts with name and GeoJSON geometry
            project_id: Target project ID
            company_id: Company ID for billing
            claimant_info: Optional dict with claimant details
            epsg: EPSG code of claim geometries (default 4326)

        Returns:
            Dict with checkout_url and session_id
        """
        try:
            # Build GeoJSON FeatureCollection from claims list
            features = []
            for claim in claims:
                geometry = claim.get('geometry')

                # If geometry is a WKT string, convert to GeoJSON dict
                if isinstance(geometry, str):
                    geometry = self._wkt_to_geojson(geometry)

                if not geometry:
                    self.logger.warning(
                        f"[QCLAIMS] Skipping claim '{claim.get('name')}' - no valid geometry"
                    )
                    continue

                feature = {
                    'type': 'Feature',
                    'properties': {
                        'name': claim.get('name', ''),
                    },
                    'geometry': geometry
                }
                features.append(feature)

            if not features:
                raise ValueError("No valid claim geometries to submit")

            generated_claims_data = {
                'type': 'FeatureCollection',
                'features': features
            }

            # Use services_base_url (geodb.io/services/api, not api.geodb.io)
            # because the services app is mounted at /services/ in the main URL conf
            url = f"{self.config.services_base_url}/purchase-claims/create-checkout-session/"

            payload = {
                'generated_claims_data': generated_claims_data,
                'claim_count': len(features),
                'project_id': project_id,
                'company_id': company_id,
                'source': 'plugin',
                'epsg': epsg,
                'is_authenticated': True,
            }
            if claimant_info:
                payload['claimant_info'] = claimant_info

            self.logger.info(
                f"[QCLAIMS] Creating checkout session: {len(features)} claims, "
                f"project={project_id}, company={company_id}"
            )

            result = self.api._make_request('POST', url, data=payload)

            self.logger.info(
                f"[QCLAIMS] Checkout session created: {result.get('session_id', 'unknown')}"
            )
            return result

        except APIException as e:
            self.logger.error(f"[QCLAIMS] Create checkout session failed: {e}")
            raise
        except Exception as e:
            self.logger.error(f"[QCLAIMS] Create checkout session error: {e}")
            raise APIException(f"Failed to create checkout session: {e}")

    @staticmethod
    def _wkt_to_geojson(wkt_string: str) -> Optional[Dict[str, Any]]:
        """
        Convert a WKT string to a GeoJSON geometry dict.

        Uses QGIS geometry parser for reliable handling of all WKT variants
        (POLYGON, MULTIPOLYGON, single/multi-ring, 2D/3D coordinates).

        Args:
            wkt_string: WKT geometry string (e.g., "POLYGON((...))")

        Returns:
            GeoJSON geometry dict or None
        """
        if not wkt_string:
            return None

        try:
            from qgis.core import QgsGeometry
            geom = QgsGeometry.fromWkt(wkt_string)
            if geom and not geom.isEmpty():
                return json.loads(geom.asJson())
            return None
        except Exception:
            return None

    # =========================================================================
    # Witness Point Generation
    # =========================================================================

    def generate_witnesses(
        self,
        waypoints: List[Dict[str, Any]],
        claims: List[Dict[str, Any]],
        epsg: int = None,
        progress_parent=None,
    ) -> Dict[str, Any]:
        """
        Generate witness waypoints for stakes on private land.

        Sends waypoints and claims to the server, which checks each stake
        against federal land boundaries and generates witness waypoints
        on nearby public land for any stakes on private land.

        Routes through the shared async-capable helper so blocks of 200+
        claims don't trip the proxy timeout.

        Raises:
            APIException: On request failure
            APIPermissionError: If user lacks access
        """
        url = self.config.endpoints.get('claims_generate_witnesses', '')
        if not url:
            raise ValueError("Witness generation endpoint not configured")

        data = {
            'waypoints': waypoints,
            'claims': claims,
        }
        if epsg:
            data['epsg'] = epsg

        result = self.api.post_async_capable(
            url, data,
            progress_title="Generating witness points",
            progress_parent=progress_parent,
        )

        self.logger.info(
            f"[QCLAIMS] Generated {result.get('witness_count', 0)} witness points "
            f"for {result.get('private_stake_count', 0)} private-land stakes"
        )
        return result

    # =========================================================================
    # Cache Management
    # =========================================================================

    def clear_cache(self):
        """Clear cached license and TOS info."""
        self._cached_license = None
        self._cached_tos = None
        self.logger.debug("[QCLAIMS] Cache cleared")
