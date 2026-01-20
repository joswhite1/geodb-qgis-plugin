# Plan: Move "Download Documents" to Step 7 and Implement ZIP Download

## Summary

Move the "Download Documents" button from Step 6 (Finalize) to Step 7 (Export), and implement proper functionality to download a zipped document package named with the claim prefix (e.g., `BOON_Claim_Docs.zip`).

## Current State

### Step 6 (`step6_finalize.py`)
- Has a placeholder `_download_documents()` method (lines 883-908)
- Currently just shows a message box listing documents and opens the first URL in browser
- Comment says "Full implementation would handle actual file downloads"

### Step 7 (`step7_export.py`)
- Handles GPX export and push to server
- No document download functionality currently

### Server Side
- `claim_package_views.py` already has a `claim_package_download()` function that:
  - Creates a ZIP file in memory with all package documents
  - Organizes files by document type (Location_Notice/, BLM_Filing_Receipt/, etc.)
  - Includes stake photos from linked claims
  - Adds a `manifest.txt`
  - Returns filename like `{package_number}_{safe_name}.zip`

### Claims Manager (`claims_manager.py`)
- Has `generate_documents()` method that calls `/api/v2/claims/documents/`
- Returns documents with `document_id`, `filename`, `url`
- No method to download document packages as ZIP

### State (`claims_wizard_state.py`)
- Stores `grid_name_prefix` (e.g., "BOON")
- Stores `generated_documents` list
- Stores `generated_document_ids` (added in step7)

## Implementation Plan

### Phase 1: Server-Side API Endpoint (if not exists)

Check if there's an API endpoint for downloading claim documents as ZIP. Options:
1. Use existing `/geodata/claim-packages/<pk>/download/` (web view)
2. Add new API endpoint: `GET /api/v2/claims/documents/download-zip/`

The API endpoint should accept:
- `document_ids`: List of document IDs to include
- `filename_prefix`: The claim prefix for naming the ZIP (e.g., "BOON")

Response: Binary ZIP file download

### Phase 2: Plugin Changes

#### 2.1 Remove from Step 6 (`step6_finalize.py`)
- Remove `download_docs_btn` button and `_download_documents()` method
- Remove `self.docs_status_label` since it's only for download status
- Keep document generation functionality in Step 6

#### 2.2 Add to Step 7 (`step7_export.py`)
- Add a new "Documents" group box
- Add "Download Documents" button
- Implement `_download_documents()` with proper ZIP download functionality

#### 2.3 Update Claims Manager (`claims_manager.py`)
- Add `download_documents_zip()` method that:
  - Calls the server endpoint to get the ZIP
  - Saves to user-specified location
  - Uses claim prefix for filename

#### 2.4 Update State (`claims_wizard_state.py`)
- May need to ensure `grid_name_prefix` is accessible in Step 7

## Detailed Implementation

### Step 7 UI Changes

```python
# In _setup_ui(), add after GPX Export Group:
layout.addWidget(self._create_documents_group())

def _create_documents_group(self) -> QGroupBox:
    """Create the documents download group."""
    group = QGroupBox("Claim Documents")
    group.setStyleSheet(self._get_group_style())
    layout = QVBoxLayout(group)
    layout.setSpacing(8)

    # Info
    info_label = QLabel(
        "Download all generated claim documents (location notices, corner certificates) "
        "as a ZIP file for printing and filing."
    )
    info_label.setWordWrap(True)
    info_label.setStyleSheet(self._get_info_label_style())
    layout.addWidget(info_label)

    # Button
    btn_layout = QHBoxLayout()

    self.download_docs_btn = QPushButton("Download Documents")
    self.download_docs_btn.setStyleSheet(self._get_success_button_style())
    self.download_docs_btn.clicked.connect(self._download_documents)
    self.download_docs_btn.setEnabled(False)  # Enabled when documents exist
    btn_layout.addWidget(self.download_docs_btn)

    btn_layout.addStretch()
    layout.addLayout(btn_layout)

    # Status
    self.docs_status_label = QLabel("")
    self.docs_status_label.setStyleSheet(self._get_info_label_style())
    layout.addWidget(self.docs_status_label)

    return group
```

### Download Implementation

```python
def _download_documents(self):
    """Download all claim documents as a ZIP file."""
    if not self.state.generated_documents:
        QMessageBox.warning(self, "No Documents", "No documents have been generated yet.")
        return

    # Get filename prefix from state
    prefix = self.state.grid_name_prefix or "Claims"
    default_filename = f"{prefix}_Claim_Docs.zip"

    # Ask for save location
    project_path = QgsProject.instance().absolutePath()
    default_dir = project_path if project_path else str(Path.home())
    default_path = str(Path(default_dir) / default_filename)

    path, _ = QFileDialog.getSaveFileName(
        self,
        "Save Claim Documents",
        default_path,
        "ZIP Files (*.zip);;All Files (*)"
    )

    if not path:
        return

    if not path.endswith('.zip'):
        path += '.zip'

    try:
        # Get document IDs
        doc_ids = [d.get('document_id') for d in self.state.generated_documents if d.get('document_id')]

        if not doc_ids:
            # Fallback: download individual URLs
            self._download_documents_individually(path)
            return

        # Download ZIP from server
        self.claims_manager.download_documents_zip(
            document_ids=doc_ids,
            output_path=path,
            filename_prefix=prefix
        )

        self.docs_status_label.setText(f"Downloaded to {Path(path).name}")
        self.docs_status_label.setStyleSheet(self._get_success_label_style())

        QMessageBox.information(
            self,
            "Download Complete",
            f"Documents downloaded to:\n{path}"
        )

        self.emit_status(f"Downloaded documents to {Path(path).name}", "success")

    except Exception as e:
        QMessageBox.critical(self, "Download Error", str(e))
        self.docs_status_label.setText(f"Download failed: {e}")
        self.docs_status_label.setStyleSheet(self._get_error_label_style())
```

### Claims Manager Method

```python
def download_documents_zip(
    self,
    document_ids: List[int],
    output_path: str,
    filename_prefix: str = "Claims"
) -> bool:
    """
    Download claim documents as a ZIP file.

    Args:
        document_ids: List of document IDs to include
        output_path: Local path to save the ZIP file
        filename_prefix: Prefix for naming (used server-side)

    Returns:
        True if successful

    Raises:
        APIException: If download fails
    """
    try:
        url = self._get_claims_endpoint('documents/download-zip/')

        # Request ZIP with specific documents
        response = self.api._make_request(
            'POST',
            url,
            data={
                'document_ids': document_ids,
                'filename_prefix': filename_prefix
            },
            raw_response=True  # Get raw binary response
        )

        # Save to file
        with open(output_path, 'wb') as f:
            f.write(response.content)

        self.logger.info(f"[QCLAIMS] Downloaded documents ZIP to {output_path}")
        return True

    except APIException as e:
        self.logger.error(f"[QCLAIMS] Download documents ZIP failed: {e}")
        raise
```

## Server-Side Endpoint Needed

Need to add API endpoint at `/api/v2/claims/documents/download-zip/`:

```python
@api_view(['POST'])
@permission_classes([IsAuthenticated])
def download_documents_zip(request):
    """
    Download claim documents as a ZIP file.

    POST /api/v2/claims/documents/download-zip/

    Input:
    {
        "document_ids": [1, 2, 3],
        "filename_prefix": "BOON"  # Optional, defaults to "Claims"
    }

    Response: Binary ZIP file
    """
    # Implementation similar to claim_package_download but:
    # 1. Takes document IDs directly instead of package ID
    # 2. Uses filename_prefix for ZIP name
```

## Questions/Decisions

1. **Server endpoint**: Does this endpoint already exist? If not, need to add it to deploy repo first.

2. **Fallback behavior**: If no document_ids (older workflow), should we:
   - Download individual URLs and create local ZIP?
   - Show error and require re-generation?

3. **Include all documents or just selected types?**: The ZIP should include all generated documents (location notices + corner certificates).

## Files to Modify

### Plugin (QPlugin/devel/geodb/)
1. `ui/claims_step_widgets/step6_finalize.py` - Remove download button
2. `ui/claims_step_widgets/step7_export.py` - Add documents group and download
3. `managers/claims_manager.py` - Add download_documents_zip method
4. `api/client.py` - May need to support binary response downloads

### Server (deploy/geodb/)
1. `services/api/claims_processing.py` - Add download-zip endpoint
2. `services/api/urls.py` - Add URL route

## Testing

1. Generate documents in Step 6
2. Proceed to Step 7
3. Click "Download Documents"
4. Verify ZIP contains all documents with correct naming
5. Verify ZIP structure matches expected format