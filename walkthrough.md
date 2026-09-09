# Family Mode & Multi-Account Synchronization Walkthrough

This document outlines the architecture, implementation, test verification, and live deployment of **Family Mode & Multi-Account Synchronization** (Sections 1 through 40 in `plan.md`).

---

## 1. Core Architectural Principle (Section 40)

The system enforces strict separation between the requester and the destination YouTube Music locker:
$$\text{requested\_by\_user} + \text{destination\_ytm\_account} + \text{authorization} \longrightarrow \text{operation}$$

- Every upload job, sync operation, and playlist replication executed for a destination account uses **only that destination user's isolated credentials and client instance**.
- No credentials, cookies, or OAuth tokens are ever shared, leaked, or reused across accounts.
- Joining a family group grants **zero implicit permissions**. Every member explicitly controls permissions via granular toggles.

---

## 2. Changes Implemented

### A. Backend Data Models (`backend/ytm_service/models.py`)
- **`SyncJob`**: Expanded with `family_id`, `requested_by_user_id`, `destination_user_id`, and `youtube_music_account_id`.
- **Enums**: Added `FamilyRole` (`OWNER`, `ADMIN`, `MEMBER`) and `FamilyMemberStatus` (`ACTIVE`, `SUSPENDED`, `LEFT`).
- **Pydantic Models**:
  - `Family`, `FamilyCreateRequest`, `FamilyUpdateRequest`, `FamilyTransferOwnershipRequest`
  - `FamilyMember`, `FamilyMemberPrivacyUpdate`, `FamilyMemberRoleUpdate`
  - `FamilyInvitation`, `FamilyInvitationCreateRequest`, `FamilyInvitationInfoResponse`
  - `FamilyDashboardMemberItem`, `FamilyDashboardResponse`
  - `SelectableAccountItem`, `UploadDestinationRequest`, `UploadDestinationResponse`
  - `TrackDestinationDuplicateStatus`, `FamilyQueueItem`, `FamilyQueueDestinationSubItem`
  - `FamilyUploadHistoryItem`, `FamilySyncResponse`, `FamilyPlaylistItem`, `FamilyMultiPlaylistRequest`

### B. Database Schema & Multi-Tenant Engine (`backend/ytm_service/database.py`)
- **Tables Added**:
  - `families`: `id`, `name`, `owner_user_id`, `created_at`, `updated_at`.
  - `family_members`: `id`, `family_id`, `user_id`, `role`, `status`, `show_account_in_family`, `allow_family_uploads`, `allow_family_playlists`, `allow_family_sync`, `joined_at`.
  - `family_invitations`: `id`, `family_id`, `invitation_token`, `created_by_user_id`, `max_uses`, `times_used`, `status`, `expires_at`, `created_at`.
- **Foreign Key Cascades & Safe Deletion**:
  - Deleting a family deletes only `family_invitations` and `family_members`. Individual `users` and their YTM accounts and uploads are preserved.
  - An owner cannot `/leave` without first transferring ownership or deleting the family.
- **Methods Implemented**:
  - `create_family`, `get_family_by_id`, `get_user_families`, `update_family`, `transfer_family_ownership`, `delete_family`, `leave_family`
  - `add_family_member`, `get_family_member`, `get_family_members`, `update_family_member_privacy`, `update_family_member_role`, `remove_family_member`
  - `create_family_invitation`, `get_family_invitation_by_token`, `accept_family_invitation`, `list_family_invitations`, `revoke_family_invitation`
  - `get_permitted_family_accounts`, `validate_upload_destination_permission`, `check_track_duplicate_for_user`
  - `get_family_upload_history`, `get_family_queue_grouped`, `get_family_permitted_sync_members`, `get_family_permitted_playlists`, `create_multi_account_playlists`

### C. REST API Endpoints (`backend/ytm_service/main.py`)
- `GET /api/accounts` & `GET /api/accounts/{account_id}`: Account discovery with privacy and upload permission status.
- `POST /api/families`, `GET /api/families`, `GET /api/families/{id}`, `PATCH /api/families/{id}`, `DELETE /api/families/{id}`: Family CRUD.
- `POST /api/families/{id}/transfer`: Owner transfer with `confirm=true`.
- `POST /api/families/{id}/leave`: Member leave safeguard.
- `GET /api/families/{id}/dashboard`: Aggregated dashboard hiding members who disabled `show_account_in_family`.
- `POST /api/families/{id}/invitations`, `GET /api/invitations/{token}`, `POST /api/invitations/{token}/accept`: Token-based invitation lifecycle.
- `PATCH /api/families/{id}/members/{user_id}/permissions`: Granular privacy toggles.
- `POST /api/uploads/destinations`: Multi-account upload dispatcher.
- `GET /api/tracks/{file_id}/destinations-status`: Destination duplicate status.
- `GET /api/families/{id}/queue`: Grouped queue by track and destinations.
- `GET /api/families/{id}/history`: Combined history identifying requester and destination.
- `POST /api/families/{id}/sync`: Fault-tolerant family bulk sync.
- `GET /api/families/{id}/playlists` & `POST /api/families/{id}/playlists/multi`: Shared playlists.

### D. Frontend (Flutter Web)
- **`app/lib/models/models.dart`**: Complete models for Family, Member, Invitation, Dashboard, and Queue items.
- **`app/lib/services/api_service.dart`**: Full HTTP API service implementation with `_patch` support.
- **`app/lib/views/components/account_selector_widget.dart`**: Account switcher supporting personal vs. family accounts and direct jump to Family Mode.
- **`app/lib/views/components/upload_destination_dialog.dart`**: Multi-account selection dialog with per-destination duplicate badges and dynamic button labeling ("Upload to Dad" / "Upload to 2 accounts").
- **`app/lib/views/family_view.dart`**: Family Mode dashboard with responsive layout, tabs for Members & Privacy, Combined Upload History, Grouped Queue, and Shared Playlists.
- **`app/lib/main.dart`**: Integrated Family Mode into left NavigationRail and user account popup menu.
- **`app/lib/views/library_view.dart`**: Integrated `AccountSelectorWidget` and `UploadDestinationDialog` for single and batch track uploads.

---

## 3. Verification & Test Results

### A. Backend Pytest Suite
- **Family Mode Test Suite (`backend/tests/test_family_mode.py`)**:
  - `test_create_family_sets_owner` $\rightarrow$ **PASSED**
  - `test_invitation_lifecycle_and_acceptance` $\rightarrow$ **PASSED**
  - `test_expired_invitation_rejected` $\rightarrow$ **PASSED**
  - `test_member_cannot_modify_family_or_invite` $\rightarrow$ **PASSED**
  - `test_owner_can_transfer_ownership` $\rightarrow$ **PASSED**
  - `test_member_leave_and_owner_guard` $\rightarrow$ **PASSED**
  - `test_family_deletion_preserves_users` $\rightarrow$ **PASSED**
  - `test_privacy_toggles_and_visibility_controls` $\rightarrow$ **PASSED**
  - `test_single_and_multi_account_upload_destinations` $\rightarrow$ **PASSED**
  - `test_upload_destination_authorization_enforced` $\rightarrow$ **PASSED**
  - `test_duplicate_detection_is_strictly_per_account` $\rightarrow$ **PASSED**
  - `test_mocked_end_to_end_family_workflow` (Section 38: Dad, Mom, Charles in Johnson Family) $\rightarrow$ **PASSED**
- **Full Backend Regression Suite**:
  ```bash
  docker exec ytm-sync pytest /app/tests -q
  243 passed, 2 warnings in 32.52s (100% pass rate)
  ```

### B. Frontend Flutter Tests
- **Flutter Test Suite (`flutter test`)**:
  ```bash
  24 passed in 8s (100% pass rate)
  ```
  - Unit tests covering Family, FamilyMember, FamilyDashboardResponse, and SelectableAccountItem JSON deserialization.
  - Widget tests covering `AccountSelectorWidget`, `UploadDestinationDialog`, and `FamilyView`.

### C. Container Build & Live Health
- Rebuilt Docker image with release Flutter Web bundle (`/app/web_dist`).
- Container `ytm-sync` started cleanly via `docker compose`:
  - `GET /health` $\rightarrow$ `{"status":"healthy","version":"1.0.0"}` (HTTP 200)
  - `GET /` $\rightarrow$ HTTP 200 serving Flutter Web UI on host port 6969.

---

## 4. Definition of Done (DoD) Audit (Section 39)

| # | Requirement | Status | Verification |
| :--- | :--- | :--- | :--- |
| 1 | Family creation, updating, deletion, leaving | **PASS** | `test_create_family_sets_owner`, `test_member_leave_and_owner_guard`, `test_family_deletion_preserves_users` |
| 2 | Roles: OWNER, ADMIN, MEMBER | **PASS** | `FamilyRole` enum, DB schema, permissions checks |
| 3 | Token-based invitations with expiry | **PASS** | `test_invitation_lifecycle_and_acceptance`, `test_expired_invitation_rejected` |
| 4 | Granular privacy toggles per member | **PASS** | `test_privacy_toggles_and_visibility_controls` |
| 5 | Zero implicit data sharing on join | **PASS** | `show_account_in_family=1`, `allow_family_uploads=1`, `allow_family_playlists=0`, `allow_family_sync=0` |
| 6 | Account Selector (personal vs. family) | **PASS** | `AccountSelectorWidget` |
| 7 | Single-account destination uploads | **PASS** | `test_single_and_multi_account_upload_destinations` |
| 8 | Multi-account destination uploads | **PASS** | `test_single_and_multi_account_upload_destinations` (Dad + Mom = 2 jobs) |
| 9 | Strict server-side authorization | **PASS** | `test_upload_destination_authorization_enforced` (403 when disabled or non-member) |
| 10 | Isolated credentials / clients | **PASS** | Section 38 E2E test; destination credentials loaded per job |
| 11 | Isolated duplicate detection per account | **PASS** | `test_duplicate_detection_is_strictly_per_account` |
| 12 | Grouped queue display | **PASS** | `get_family_queue_grouped` in `database.py` and `FamilyView` |
| 13 | Combined upload history | **PASS** | `test_mocked_end_to_end_family_workflow` verifying `requested_by` vs `destination` |
| 14 | Family sync across permitted accounts | **PASS** | `POST /api/families/{id}/sync`, `triggerFamilySync` |
| 15 | Multi-account playlist creation | **PASS** | `POST /api/families/{id}/playlists/multi`, `FamilyView` |
| 16 | Section 38 E2E Johnson family workflow | **PASS** | `test_mocked_end_to_end_family_workflow` |
| 17 | Zero Flutter regressions | **PASS** | 24 tests passed in `flutter test` |
| 18 | Zero Backend regressions | **PASS** | 243 tests passed in `pytest` |
| 19 | Production container rebuild | **PASS** | `docker compose up -d --force-recreate` on port 6969 |
| 20 | Documentation updated | **PASS** | `README.md` and `walkthrough.md` |
