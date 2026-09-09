# YTM Sync — Family Mode and Multi-Account Addendum

## 1. Goal

Extend the multi-user architecture with an optional **Family** feature that allows multiple YTM Sync accounts to be managed together in one interface.

Family Mode should make it easy to:

* See multiple connected YouTube Music accounts in one window.
* Quickly switch between family members.
* Upload a song to one selected account.
* Upload the same song to multiple selected accounts.
* See which account owns a playlist or upload.
* Run sync operations for one account or multiple family accounts.
* Keep every user's credentials and private data isolated.

Family Mode must **not** weaken the security boundaries between individual users.

---

# 2. Family Architecture

The architecture should become:

```text
YTM Sync
│
├── Users
│   ├── User A
│   │   └── YouTube Music Account A
│   │
│   ├── User B
│   │   └── YouTube Music Account B
│   │
│   └── User C
│       └── YouTube Music Account C
│
└── Families
    └── Family 1
        ├── User A
        ├── User B
        └── User C
```

A Family is a **grouping and authorization layer**.

It is not a shared authentication account.

---

# 3. Family Model

Create a family entity:

```text
Family
├── id
├── name
├── owner_user_id
├── created_at
└── updated_at
```

Example:

```text
Family
├── id: family_001
├── name: Johnson Family
└── owner: user_001
```

---

# 4. Family Membership

Create a membership model:

```text
FamilyMember
├── id
├── family_id
├── user_id
├── role
├── created_at
└── status
```

Roles should initially be:

```text
OWNER
ADMIN
MEMBER
```

Optional future role:

```text
CHILD
```

Do not implement unnecessary child-account restrictions unless they are actually required.

---

# 5. Family Permissions

Family membership must determine what another family member can see or do.

Recommended initial behavior:

### OWNER

Can:

* manage family,
* invite members,
* remove members,
* see family members,
* see family account connection status,
* use Family Mode,
* upload to permitted family accounts.

### ADMIN

Can:

* see family members,
* use Family Mode,
* upload to permitted family accounts,
* manage normal family settings.

### MEMBER

Can:

* see permitted family accounts,
* use Family Mode,
* upload where permission is granted.

The permission model must be explicit.

Do not assume:

```text
same family = full access to everything
```

---

# 6. YouTube Music Account Visibility

A family member should be able to choose whether their YouTube Music account is visible to the family.

Example:

```text
Family visibility

[✓] Show my YouTube Music account in Family Mode
[✓] Allow family uploads to my account
[ ] Allow family members to see my playlists
```

This creates separate permissions for:

* account visibility,
* upload access,
* playlist visibility,
* sync visibility.

---

# 7. Important Privacy Rule

**Family membership does not automatically grant access to private YTM data.**

For example:

```text
Dad
  │
  └── YouTube Music
       ├── Private playlists
       ├── Uploads
       └── Sync state
```

Mom should not automatically receive access to all of Dad's playlists merely because they are in the same Family.

Family Mode should only expose information explicitly allowed by the account owner.

---

# 8. Family Dashboard

Create a Family dashboard that displays connected accounts.

Example:

```text
┌──────────────────────────────────────────────┐
│ Johnson Family                               │
├──────────────────────────────────────────────┤
│                                              │
│  👤 Dad                                      │
│  YouTube Music        ✓ Connected            │
│  Uploads: 243                                 │
│                                              │
│  👤 Mom                                      │
│  YouTube Music        ✓ Connected            │
│  Uploads: 118                                 │
│                                              │
│  👤 Charles                                  │
│  YouTube Music        ✓ Connected            │
│  Uploads: 42                                  │
│                                              │
└──────────────────────────────────────────────┘
```

Only information permitted by the member's privacy settings should be displayed.

---

# 9. Account Selector

Create a reusable account selector throughout the application.

Example:

```text
YouTube Music Account

┌─────────────────────────────────────┐
│ 👤 Dad                              │
│    Connected ✓                      │
├─────────────────────────────────────┤
│ 👤 Mom                              │
│    Connected ✓                      │
├─────────────────────────────────────┤
│ 👤 Charles                          │
│    Connected ✓                      │
└─────────────────────────────────────┘
```

This should become the standard way to choose a YTM account.

---

# 10. Upload Destination Selector

The upload screen must allow the user to choose where the upload goes.

Example:

```text
Upload Music
────────────────────────────────

Files
  My Song.mp3
  Another Song.mp3

Upload to:

☑ Dad — YouTube Music
☐ Mom — YouTube Music
☐ Charles — YouTube Music

[ Upload ]
```

The default should be the currently selected account.

---

# 11. Single-Account Upload

If the user selects one account:

```text
☑ Dad
☐ Mom
☐ Charles
```

the file is uploaded only to Dad's YouTube Music account.

The backend must use:

```text
selected_ytm_account_id
```

to determine the destination.

It must **not** trust a client-provided user ID without validating permissions.

---

# 12. Multi-Account Upload

Allow the user to select multiple permitted accounts:

```text
☑ Dad
☑ Mom
☐ Charles
```

The system creates independent upload jobs:

```text
Upload
│
├── Job → Dad
└── Job → Mom
```

Each job must use that account's:

* authentication,
* YTMusic client,
* upload state,
* error handling.

---

# 13. Never Reuse Authentication Between Accounts

This is critical.

For:

```text
☑ Dad
☑ Mom
```

the application must perform:

```text
Dad upload
    ↓
Dad's YTMusic client
    ↓
Dad's authentication

Mom upload
    ↓
Mom's YTMusic client
    ↓
Mom's authentication
```

Never:

```text
One global YTMusic client
        ↓
switch account
```

This prevents accidental cross-account uploads.

---

# 14. Upload Job Model

Expand the upload job model:

```text
UploadJob
├── id
├── family_id
├── requested_by_user_id
├── destination_user_id
├── youtube_music_account_id
├── file_id
├── status
├── progress
├── error
├── created_at
├── started_at
└── completed_at
```

This makes the destination explicit.

---

# 15. Upload Authorization

Before starting an upload:

```text
Current User
      ↓
Is user allowed to upload to destination?
      ↓
YES
      ↓
Is destination YTM account connected?
      ↓
YES
      ↓
Create upload job
```

Reject if:

* destination account doesn't exist,
* destination account isn't connected,
* destination user isn't in the family,
* destination user has disabled family uploads,
* requesting user lacks permission,
* authentication is invalid.

---

# 16. Upload Confirmation

When uploading to another person's account, make the destination obvious.

Example:

```text
Upload to:

⚠️ Dad's YouTube Music account

2 songs will be uploaded.

[ Cancel ]    [ Upload ]
```

For multiple accounts:

```text
Upload to:

✓ Dad
✓ Mom
✗ Charles — Family uploads disabled

2 upload jobs will be created.

[ Cancel ]    [ Upload ]
```

This prevents accidental uploads to the wrong account.

---

# 17. Family Upload History

Family Mode should provide a combined upload history where permitted.

Example:

```text
Recent Family Uploads

Song                 Destination      Status
------------------------------------------------
My Song              Dad               ✓
My Song              Mom               ✓
New Track            Dad               ✓
New Track            Mom               Failed
```

The history should clearly identify:

* source file,
* destination account,
* requesting user,
* status,
* error,
* timestamp.

---

# 18. Family Sync

Family Mode should eventually support:

```text
Sync
├── This Account
└── Family
```

### This Account

Sync only the currently selected user's YTM account.

### Family

Run permitted sync jobs for all family accounts.

Example:

```text
Family Sync

Dad       ✓ Complete
Mom       ✓ Complete
Charles   ⏳ Syncing
```

A failure for one account must not stop the others.

---

# 19. Family Playlist Visibility

Playlist visibility should be independently configurable.

Example:

```text
Family Sharing

YouTube Music Account
[✓] Show account in family
[✓] Allow family uploads
[✓] Show playlists
[ ] Allow playlist modification
```

Initially, recommend:

```text
View playlist
```

before allowing:

```text
Modify playlist
```

---

# 20. Family Playlist Operations

When the future playlist system is implemented, every operation must specify its destination account.

Example:

```text
Create Playlist

Playlist name:
My Family Playlist

Account:
[ Dad ▼ ]

Source:
☑ Uploaded songs only

[ Create Playlist ]
```

For multiple accounts:

```text
Create playlist on:

☑ Dad
☑ Mom
☐ Charles
```

Each account receives an independent operation.

---

# 21. Family Account Switcher

The application should provide a persistent account selector.

Example:

```text
┌─────────────────────────┐
│ Active Account           │
│                          │
│ 👤 Dad                   │
│ ✓ YouTube Music         │
│                          │
│ ▼ Switch account         │
└─────────────────────────┘
```

Options:

```text
Dad
Mom
Charles
──────────────
Family
```

Selecting **Family** activates the multi-account dashboard.

---

# 22. Family Mode vs Personal Mode

The application should have two conceptual modes:

```text
PERSONAL
    ↓
One user's YTM account

FAMILY
    ↓
Multiple permitted family accounts
```

Personal mode remains the default.

Family Mode is opt-in.

---

# 23. Family API

Add endpoints along the lines of:

```text
POST   /api/families
GET    /api/families
GET    /api/families/{family_id}
PATCH  /api/families/{family_id}
DELETE /api/families/{family_id}

POST   /api/families/{family_id}/members
DELETE /api/families/{family_id}/members/{user_id}

GET    /api/families/{family_id}/accounts
PATCH  /api/families/{family_id}/members/{user_id}/permissions
```

Account-selection endpoints:

```text
GET /api/accounts
GET /api/accounts/{account_id}
```

Upload endpoints should accept a destination account ID:

```text
POST /api/uploads
```

with the server validating that the current user is allowed to use that destination.

---

# 24. Never Trust Destination IDs

A malicious client could attempt:

```json
{
  "destination_user_id": "someone_else"
}
```

or:

```json
{
  "youtube_music_account_id": "another_account"
}
```

The backend must verify:

```text
Current User
      ↓
Family membership
      ↓
Destination account
      ↓
Permission
```

before doing anything.

---

# 25. Family Database Relationships

Recommended relationships:

```text
User
 │
 ├── YouTubeMusicAccount
 │
 └── FamilyMembership
          │
          ↓
        Family
          │
          ├── FamilyMember
          ├── FamilyMember
          └── FamilyMember
```

Uploads:

```text
UploadJob
 ├── requested_by_user_id
 ├── destination_user_id
 ├── youtube_music_account_id
 └── family_id
```

This clearly separates:

**Who requested it**

from:

**Where it goes.**

---

# 26. Security Rules

Family Mode must enforce all of the following:

* [x] Users cannot access families they don't belong to.
* [x] Users cannot add themselves to arbitrary families.
* [x] Users cannot see accounts hidden from them.
* [x] Users cannot upload to accounts that disallow family uploads.
* [x] Users cannot modify another user's permissions.
* [x] Users cannot access another user's credentials.
* [x] Users cannot access another user's private playlists.
* [x] Users cannot access another user's private sync state.
* [x] Users cannot construct arbitrary destination account IDs.
* [x] Destination accounts are validated server-side.
* [x] Upload jobs record both requester and destination.

---

# 27. Family Invitations

Add a family invitation system.

Example:

```text
Family
  ↓
Invite Member
  ↓
Invitation
  ↓
User accepts
  ↓
FamilyMember created
```

Invitation tokens must be:

* random,
* short-lived,
* single-use,
* invalidated after acceptance.

Do not expose internal family IDs as invitation secrets.

---

# 28. Family Removal

When removing a family member:

```text
Remove Charles from Family?
```

Removing a member must **not**:

* delete their YTM account,
* delete their YTM Sync account,
* delete their uploads,
* delete their playlists,
* delete their authentication.

It should only remove the family relationship.

---

# 29. Leaving a Family

A normal member should be able to:

```text
Leave Family
```

The user's personal account remains intact.

Their YTM Music connection remains intact.

Their personal data remains intact.

Only the family relationship is removed.

---

# 30. Family Owner Transfer

Allow the owner to transfer ownership.

Example:

```text
Transfer Family Ownership

Transfer ownership to:
[ Mom ▼ ]

[ Cancel ] [ Transfer ]
```

Require explicit confirmation.

---

# 31. Family Deletion

Deleting a family must **not delete users**.

It should only delete:

* family,
* memberships,
* family permissions,
* family invitations.

Individual user accounts and YTM accounts remain intact.

---

# 32. Family UI Safety

Always display the destination account prominently.

Avoid ambiguous buttons such as:

```text
Upload
```

Prefer:

```text
Upload to Dad
```

or:

```text
Upload to 2 accounts
```

This is especially important when the same song is being uploaded to multiple accounts.

---

# 33. Multi-Account Upload Queue

The queue should group jobs by destination.

Example:

```text
Upload Queue

My Song.mp3
 ├── Dad       ✓ Complete
 ├── Mom       ⏳ Uploading
 └── Charles   — Not permitted

Another Song.mp3
 ├── Dad       ⏳ Waiting
 └── Mom       ✓ Complete
```

Each destination gets its own status.

---

# 34. Duplicate Handling Per Account

Duplicate detection must be performed independently per destination account.

Example:

```text
My Song.mp3

Dad:
✓ Already uploaded

Mom:
⬆ Uploading

Charles:
✗ Family uploads disabled
```

Never assume that because a song exists in Dad's account it exists in Mom's account.

---

# 35. Family Playlist Planning

The planned uploaded-song-only playlist functionality should integrate with Family Mode.

Example:

```text
Create / Watch Playlist

Playlist:
Family Uploads

Account:
[ Mom ▼ ]

Source:
● Uploaded songs only

```

For Family Mode:

```text
Accounts:

☑ Dad
☑ Mom
☐ Charles
```

The application creates/maintains the playlist independently on each selected YouTube Music account.

---

# 36. Family-Aware Background Jobs

Every background job must include:

```text
requested_by_user_id
destination_user_id
youtube_music_account_id
family_id
```

Example:

```text
Family playlist sync
       ↓
Dad playlist sync
       ↓
Mom playlist sync
       ↓
Charles playlist sync
```

One account failing must not affect the others.

---

# 37. Family Security Testing

Add tests for:

### Membership

* [x] Member can access their family.
* [x] Non-member cannot access family.
* [x] Member cannot modify family ownership.
* [x] Member cannot add unauthorized users.

### Account visibility

* [x] Hidden YTM account is invisible.
* [x] Visible account is shown.
* [x] Family upload permission is enforced.

### Uploads

* [x] User can upload to own account.
* [x] User can upload to permitted family account.
* [x] User cannot upload to restricted family account.
* [x] User cannot upload to non-family account.
* [x] Destination account cannot be spoofed.

### Privacy

* [x] Private playlist remains private.
* [x] Private sync state remains private.
* [x] Authentication remains private.
* [x] User cannot retrieve another user's credentials.

---

# 38. Family Integration Test

Create a complete mocked workflow:

```text
Dad
 ├── YTM Account A
 │
Mom
 ├── YTM Account B
 │
Charles
 └── YTM Account C

        ↓

Johnson Family

        ↓

Dad uploads song

        ↓

Select:
☑ Dad
☑ Mom

        ↓

Create two upload jobs

        ↓

Dad → YTMusic A
Mom → YTMusic B

        ↓

Charles receives nothing
```

Then verify:

```text
Dad cannot access Mom's credentials.
Mom cannot access Dad's credentials.
Charles cannot access either account unless permitted.
```

---

# 39. Definition of Done — Family Mode

Family Mode is complete when:

* [x] Families can be created.
* [x] Users can be invited.
* [x] Users can join families.
* [x] Users can leave families.
* [x] Owners can remove members.
* [x] Family ownership can be transferred.
* [x] Families can be deleted without deleting users.
* [x] Multiple YTM accounts can be displayed together.
* [x] Account visibility can be controlled.
* [x] Family upload permissions can be controlled.
* [x] Upload destination can be selected.
* [x] Multiple upload destinations can be selected.
* [x] Each destination gets an independent upload job.
* [x] Each upload uses the correct YTM authentication.
* [x] Duplicate detection is per account.
* [x] Upload history identifies destination.
* [x] Family sync works independently per account.
* [x] Family playlist functionality is account-aware.
* [x] Private user data remains private.
* [x] Cross-family access is blocked.
* [x] Cross-user authentication access is blocked.
* [x] Security tests pass.
* [x] Docker/reverse-proxy testing passes.

---

# 40. Final Architecture

The target architecture should ultimately look like:

```text
                         YTM Sync
                            │
              ┌─────────────┴─────────────┐
              │                           │
          Personal                     Family
              │                           │
              │                    ┌──────┴──────┐
              │                    │             │
            User                 Dad           Mom
              │                    │             │
              │              YTM Account A   YTM Account B
              │                    │             │
              │                    │             │
              └──────────────┬─────┴─────────────┘
                             │
                        Upload Manager
                             │
                  ┌──────────┴──────────┐
                  │                     │
             Upload Job A          Upload Job B
                  │                     │
             YTMusic A              YTMusic B
                  │                     │
             Dad account             Mom account
```

## Core Design Principle

**The person making the request and the account receiving the operation are separate concepts.**

For every operation, especially uploads:

```text
requested_by_user
        +
destination_ytm_account
        +
authorization
        ↓
operation
```

This allows YTM Sync to support:

* personal accounts,
* family accounts,
* multiple YTM accounts in one window,
* uploading to another family member's account,
* uploading to several accounts at once,

while keeping authentication and private data isolated.
