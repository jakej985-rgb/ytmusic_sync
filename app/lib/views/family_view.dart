import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../models/models.dart';
import '../services/api_service.dart';

class FamilyView extends StatefulWidget {
  final Function(int)? onNavigateTab;

  const FamilyView({super.key, this.onNavigateTab});

  @override
  State<FamilyView> createState() => _FamilyViewState();
}

class _FamilyViewState extends State<FamilyView> with SingleTickerProviderStateMixin {
  late TabController _tabController;
  List<Family> _families = [];
  Family? _selectedFamily;
  FamilyDashboardResponse? _dashboard;
  List<FamilyInvitation> _invitations = [];
  List<FamilyUploadHistoryItem> _history = [];
  List<FamilyQueueItem> _queue = [];
  List<FamilyPlaylistItem> _playlists = [];

  bool _isLoading = true;
  bool _isActionLoading = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 4, vsync: this);
    _loadInitialData();
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  Future<void> _loadInitialData() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });
    try {
      final families = await apiService.getFamilies();
      if (!mounted) return;
      setState(() {
        _families = families;
        if (families.isNotEmpty) {
          _selectedFamily = families.first;
        }
      });
      if (_selectedFamily != null) {
        await _loadFamilyDetails(_selectedFamily!.id);
      } else {
        setState(() => _isLoading = false);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          _errorMessage = e.toString().replaceAll('Exception: ', '');
        });
      }
    }
  }

  Future<void> _loadFamilyDetails(String familyId) async {
    setState(() => _isLoading = true);
    try {
      final dashboard = await apiService.getFamilyDashboard(familyId);
      final invitations = await apiService.listFamilyInvitations(familyId).catchError((_) => <FamilyInvitation>[]);
      final history = await apiService.getFamilyHistory(familyId).catchError((_) => <FamilyUploadHistoryItem>[]);
      final queue = await apiService.getFamilyQueue(familyId).catchError((_) => <FamilyQueueItem>[]);
      final playlists = await apiService.getFamilyPlaylists(familyId).catchError((_) => <FamilyPlaylistItem>[]);

      if (!mounted) return;
      setState(() {
        _dashboard = dashboard;
        _invitations = invitations;
        _history = history;
        _queue = queue;
        _playlists = playlists;
        _isLoading = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          _errorMessage = e.toString().replaceAll('Exception: ', '');
        });
      }
    }
  }

  Future<void> _showCreateFamilyDialog() async {
    final controller = TextEditingController();
    final created = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF181824),
        title: const Text('Create Family Group', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
        content: TextField(
          controller: controller,
          autofocus: true,
          style: const TextStyle(color: Colors.white),
          decoration: const InputDecoration(
            labelText: 'Family Name (e.g. Johnson Family)',
            labelStyle: TextStyle(color: Colors.grey),
            focusedBorder: UnderlineInputBorder(borderSide: BorderSide(color: Color(0xFFFF0000))),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel', style: TextStyle(color: Colors.grey)),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFFFF0000)),
            onPressed: () async {
              if (controller.text.trim().isEmpty) return;
              try {
                final fam = await apiService.createFamily(controller.text.trim());
                if (ctx.mounted) Navigator.of(ctx).pop(true);
                await _loadInitialData();
                setState(() => _selectedFamily = fam);
                await _loadFamilyDetails(fam.id);
              } catch (e) {
                if (ctx.mounted) {
                  ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(e.toString())));
                }
              }
            },
            child: const Text('Create', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
    if (created == true) {
      _loadInitialData();
    }
  }

  Future<void> _showJoinFamilyDialog() async {
    final controller = TextEditingController();
    await showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF181824),
        title: const Text('Join Family with Invite Token', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
        content: TextField(
          controller: controller,
          autofocus: true,
          style: const TextStyle(color: Colors.white),
          decoration: const InputDecoration(
            labelText: 'Paste 32-character invitation token',
            labelStyle: TextStyle(color: Colors.grey),
            focusedBorder: UnderlineInputBorder(borderSide: BorderSide(color: Color(0xFFFF0000))),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel', style: TextStyle(color: Colors.grey)),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF3EA6FF)),
            onPressed: () async {
              final token = controller.text.trim();
              if (token.isEmpty) return;
              try {
                await apiService.acceptFamilyInvitation(token);
                if (ctx.mounted) {
                  Navigator.of(ctx).pop();
                  ScaffoldMessenger.of(ctx).showSnackBar(
                    const SnackBar(content: Text('Successfully joined family!')),
                  );
                }
                await _loadInitialData();
              } catch (e) {
                if (ctx.mounted) {
                  ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(content: Text(e.toString())));
                }
              }
            },
            child: const Text('Join Family', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  Future<void> _createInvitation() async {
    if (_selectedFamily == null) return;
    try {
      final inv = await apiService.createFamilyInvitation(_selectedFamily!.id, expiryHours: 48, maxUses: 1);
      await _loadFamilyDetails(_selectedFamily!.id);
      if (mounted) {
        showDialog(
          context: context,
          builder: (ctx) => AlertDialog(
            backgroundColor: const Color(0xFF181824),
            title: const Text('Family Invitation Created', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Share this one-time token with your family member:', style: TextStyle(color: Colors.grey, fontSize: 13)),
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: const Color(0xFF12121A),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Colors.white12),
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: SelectableText(
                          inv.invitationToken,
                          style: const TextStyle(color: Color(0xFF3EA6FF), fontFamily: 'monospace', fontWeight: FontWeight.bold),
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.copy, size: 18, color: Colors.grey),
                        onPressed: () {
                          Clipboard.setData(ClipboardData(text: inv.invitationToken));
                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Token copied to clipboard!')));
                        },
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 8),
                const Text('Expires in 48 hours. Zero implicit permissions are granted upon joining.', style: TextStyle(fontSize: 11, color: Colors.grey)),
              ],
            ),
            actions: [
              TextButton(onPressed: () => Navigator.of(ctx).pop(), child: const Text('Done', style: TextStyle(color: Colors.white))),
            ],
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    }
  }

  Future<void> _triggerFamilySync() async {
    if (_selectedFamily == null) return;
    setState(() => _isActionLoading = true);
    try {
      final res = await apiService.triggerFamilySync(_selectedFamily!.id);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Family Sync Triggered: ${res['jobs_triggered']} jobs across ${res['sync_targets_count']} accounts'),
            backgroundColor: Colors.green,
          ),
        );
      }
      await _loadFamilyDetails(_selectedFamily!.id);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    } finally {
      if (mounted) setState(() => _isActionLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0F0F13),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header: Responsive Family Switcher and Action Buttons
            LayoutBuilder(
              builder: (ctx, constraints) {
                final isNarrow = constraints.maxWidth < 700;
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Container(
                          padding: const EdgeInsets.all(10),
                          decoration: BoxDecoration(
                            color: const Color(0xFFFF0000).withAlpha(30),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: const Icon(Icons.people, color: Color(0xFFFF0000), size: 26),
                        ),
                        const SizedBox(width: 14),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                'Family Mode',
                                style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: Colors.white),
                              ),
                              Text(
                                _selectedFamily != null
                                    ? '${_selectedFamily!.name} • ${_dashboard?.totalMembers ?? 0} members'
                                    : 'Manage family groups and multi-account destinations',
                                style: const TextStyle(fontSize: 12, color: Colors.grey),
                                overflow: TextOverflow.ellipsis,
                              ),
                            ],
                          ),
                        ),
                        if (!isNarrow) _buildHeaderActions(),
                      ],
                    ),
                    if (isNarrow) ...[
                      const SizedBox(height: 12),
                      _buildHeaderActions(),
                    ],
                  ],
                );
              },
            ),
            const SizedBox(height: 16),

            if (_errorMessage != null) ...[
              Container(
                padding: const EdgeInsets.all(12),
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Colors.red.withAlpha(30),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: Colors.redAccent.withAlpha(80)),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.error_outline, size: 18, color: Colors.redAccent),
                    const SizedBox(width: 10),
                    Expanded(child: Text(_errorMessage!, style: const TextStyle(color: Colors.redAccent, fontSize: 13))),
                    IconButton(
                      icon: const Icon(Icons.close, size: 16, color: Colors.grey),
                      onPressed: () => setState(() => _errorMessage = null),
                    ),
                  ],
                ),
              ),
            ],

            if (_families.isEmpty && !_isLoading) ...[
              Expanded(
                child: Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.family_restroom, size: 64, color: Colors.white24),
                      const SizedBox(height: 16),
                      const Text(
                        'No Family Groups Yet',
                        style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
                      ),
                      const SizedBox(height: 8),
                      const Text(
                        'Create a family group or join an existing one using an invite code.',
                        style: TextStyle(fontSize: 13, color: Colors.grey),
                      ),
                      const SizedBox(height: 20),
                      Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          ElevatedButton.icon(
                            onPressed: _showCreateFamilyDialog,
                            icon: const Icon(Icons.add, size: 16),
                            label: const Text('Create Family Group'),
                            style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFFFF0000)),
                          ),
                          const SizedBox(width: 12),
                          OutlinedButton.icon(
                            onPressed: _showJoinFamilyDialog,
                            icon: const Icon(Icons.group_add, size: 16),
                            label: const Text('Join with Token'),
                            style: OutlinedButton.styleFrom(foregroundColor: const Color(0xFF3EA6FF)),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
            ] else ...[
              // Tabs row
              TabBar(
                controller: _tabController,
                indicatorColor: const Color(0xFFFF0000),
                labelColor: Colors.white,
                unselectedLabelColor: Colors.grey,
                tabs: const [
                  Tab(icon: Icon(Icons.group, size: 18), text: 'Members & Privacy'),
                  Tab(icon: Icon(Icons.history, size: 18), text: 'Upload History'),
                  Tab(icon: Icon(Icons.queue_music, size: 18), text: 'Queue'),
                  Tab(icon: Icon(Icons.playlist_play, size: 18), text: 'Shared Playlists'),
                ],
              ),
              const SizedBox(height: 16),

              Expanded(
                child: _isLoading
                    ? const Center(child: CircularProgressIndicator())
                    : TabBarView(
                        controller: _tabController,
                        children: [
                          _buildMembersTab(),
                          _buildHistoryTab(),
                          _buildQueueTab(),
                          _buildPlaylistsTab(),
                        ],
                      ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildHeaderActions() {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        if (_families.isNotEmpty)
          Container(
            height: 38,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              color: const Color(0xFF1E1E28),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(color: Colors.white12),
            ),
            child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                value: _selectedFamily?.id,
                dropdownColor: const Color(0xFF1E1E28),
                style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                icon: const Icon(Icons.arrow_drop_down, color: Colors.grey),
                items: _families.map((f) {
                  return DropdownMenuItem<String>(
                    value: f.id,
                    child: Text(f.name),
                  );
                }).toList(),
                onChanged: (String? val) {
                  if (val != null) {
                    final fam = _families.firstWhere((f) => f.id == val);
                    setState(() => _selectedFamily = fam);
                    _loadFamilyDetails(fam.id);
                  }
                },
              ),
            ),
          ),
        OutlinedButton.icon(
          onPressed: _showJoinFamilyDialog,
          icon: const Icon(Icons.group_add, size: 16),
          label: const Text('Join Family', style: TextStyle(fontSize: 12)),
          style: OutlinedButton.styleFrom(
            foregroundColor: const Color(0xFF3EA6FF),
            side: const BorderSide(color: Color(0xFF3EA6FF)),
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          ),
        ),
        ElevatedButton.icon(
          onPressed: _showCreateFamilyDialog,
          icon: const Icon(Icons.add, size: 16),
          label: const Text('Create Family', style: TextStyle(fontSize: 12)),
          style: ElevatedButton.styleFrom(
            backgroundColor: const Color(0xFFFF0000),
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          ),
        ),
      ],
    );
  }

  Widget _buildMembersTab() {
    final members = _dashboard?.members ?? [];
    final callerId = apiService.currentUser?.id;
    final isOwnerOrAdmin = _dashboard?.callerRole == 'OWNER' || _dashboard?.callerRole == 'ADMIN';

    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                'Members (${members.length})',
                style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
              ),
              const Spacer(),
              if (isOwnerOrAdmin) ...[
                ElevatedButton.icon(
                  onPressed: _createInvitation,
                  icon: const Icon(Icons.person_add, size: 16),
                  label: const Text('Invite Member'),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF1E1E28),
                    foregroundColor: Colors.white,
                    side: const BorderSide(color: Colors.white12),
                  ),
                ),
                const SizedBox(width: 8),
              ],
              ElevatedButton.icon(
                onPressed: _isActionLoading ? null : _triggerFamilySync,
                icon: const Icon(Icons.sync, size: 16),
                label: const Text('Sync Permitted Accounts'),
                style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFFFF0000), foregroundColor: Colors.white),
              ),
            ],
          ),
          const SizedBox(height: 12),

          ListView.separated(
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            itemCount: members.length,
            separatorBuilder: (_, _) => const SizedBox(height: 8),
            itemBuilder: (ctx, idx) {
              final m = members[idx];
              final isSelf = m.userId == callerId;

              return Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: const Color(0xFF181824),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: isSelf ? const Color(0xFFFF0000).withAlpha(80) : Colors.white12),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        CircleAvatar(
                          radius: 18,
                          backgroundColor: m.role == 'OWNER' ? const Color(0xFFFF0000) : const Color(0xFF3EA6FF),
                          child: Text(
                            m.username.isNotEmpty ? m.username[0].toUpperCase() : 'U',
                            style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold),
                          ),
                        ),
                        const SizedBox(width: 12),
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Text(
                                  m.username + (isSelf ? ' (You)' : ''),
                                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                                ),
                                const SizedBox(width: 8),
                                Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                                  decoration: BoxDecoration(
                                    color: m.role == 'OWNER' ? const Color(0xFFFF0000).withAlpha(40) : const Color(0xFF2A2A38),
                                    borderRadius: BorderRadius.circular(4),
                                  ),
                                  child: Text(
                                    m.role,
                                    style: TextStyle(
                                      fontSize: 10,
                                      fontWeight: FontWeight.w600,
                                      color: m.role == 'OWNER' ? const Color(0xFFFF4E4E) : Colors.grey,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                            const SizedBox(height: 2),
                            Row(
                              children: [
                                Icon(
                                  m.ytmConnected ? Icons.check_circle : Icons.circle_outlined,
                                  size: 11,
                                  color: m.ytmConnected ? Colors.greenAccent : Colors.grey,
                                ),
                                const SizedBox(width: 4),
                                Text(
                                  m.ytmConnected ? (m.accountName ?? 'Connected') : 'Disconnected',
                                  style: TextStyle(fontSize: 11, color: m.ytmConnected ? Colors.greenAccent : Colors.grey),
                                ),
                                if (m.uploadsCount != null) ...[
                                  const SizedBox(width: 8),
                                  Text('• ${m.uploadsCount} uploads', style: const TextStyle(fontSize: 11, color: Colors.grey)),
                                ],
                              ],
                            ),
                          ],
                        ),
                        const Spacer(),
                        if (isSelf) ...[
                          TextButton.icon(
                            onPressed: () => _showPrivacySettingsDialog(m),
                            icon: const Icon(Icons.tune, size: 16, color: Color(0xFF3EA6FF)),
                            label: const Text('My Privacy Settings', style: TextStyle(fontSize: 12, color: Color(0xFF3EA6FF))),
                          ),
                        ] else if (isOwnerOrAdmin && m.role != 'OWNER') ...[
                          IconButton(
                            icon: const Icon(Icons.remove_circle_outline, size: 18, color: Colors.redAccent),
                            tooltip: 'Remove Member',
                            onPressed: () => _removeMember(m.userId),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 10),
                    Wrap(
                      spacing: 8,
                      runSpacing: 4,
                      children: [
                        _buildPermissionPill('Uploads', m.allowFamilyUploads),
                        _buildPermissionPill('Playlists', m.allowFamilyPlaylists),
                        _buildPermissionPill('Sync', m.allowFamilySync),
                      ],
                    ),
                  ],
                ),
              );
            },
          ),
          const SizedBox(height: 24),

          // Invitations subsection
          if (_invitations.isNotEmpty) ...[
            const Text('Pending Invitations', style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white)),
            const SizedBox(height: 8),
            ListView.separated(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              itemCount: _invitations.length,
              separatorBuilder: (_, _) => const SizedBox(height: 6),
              itemBuilder: (ctx, idx) {
                final inv = _invitations[idx];
                return Container(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  decoration: BoxDecoration(
                    color: const Color(0xFF14141E),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Colors.white10),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.link, size: 16, color: Colors.grey),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          'Token: ${inv.invitationToken.substring(0, 12)}... (${inv.status})',
                          style: const TextStyle(fontFamily: 'monospace', fontSize: 12, color: Colors.white70),
                        ),
                      ),
                      IconButton(
                        icon: const Icon(Icons.copy, size: 16, color: Colors.grey),
                        onPressed: () {
                          Clipboard.setData(ClipboardData(text: inv.invitationToken));
                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Token copied!')));
                        },
                      ),
                      if (isOwnerOrAdmin)
                        IconButton(
                          icon: const Icon(Icons.delete_outline, size: 16, color: Colors.redAccent),
                          onPressed: () async {
                            if (_selectedFamily != null) {
                              await apiService.revokeFamilyInvitation(_selectedFamily!.id, inv.id);
                              _loadFamilyDetails(_selectedFamily!.id);
                            }
                          },
                        ),
                    ],
                  ),
                );
              },
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildPermissionPill(String label, bool isEnabled) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: isEnabled ? Colors.green.withAlpha(30) : Colors.white.withAlpha(10),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: isEnabled ? Colors.greenAccent.withAlpha(80) : Colors.white12),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(isEnabled ? Icons.check : Icons.close, size: 12, color: isEnabled ? Colors.greenAccent : Colors.grey),
          const SizedBox(width: 4),
          Text(label, style: TextStyle(fontSize: 11, color: isEnabled ? Colors.greenAccent : Colors.grey)),
        ],
      ),
    );
  }

  Future<void> _showPrivacySettingsDialog(FamilyDashboardMemberItem item) async {
    bool allowUploads = item.allowFamilyUploads;
    bool allowPlaylists = item.allowFamilyPlaylists;
    bool allowSync = item.allowFamilySync;

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          backgroundColor: const Color(0xFF181824),
          title: const Text('My Family Privacy Controls', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SwitchListTile(
                activeThumbColor: const Color(0xFFFF0000),
                title: const Text('Allow Family Uploads', style: TextStyle(color: Colors.white, fontSize: 14)),
                subtitle: const Text('Let family members upload tracks to your YTM account', style: TextStyle(fontSize: 11, color: Colors.grey)),
                value: allowUploads,
                onChanged: (val) => setDialogState(() => allowUploads = val),
              ),
              SwitchListTile(
                activeThumbColor: const Color(0xFFFF0000),
                title: const Text('Allow Family Playlists', style: TextStyle(color: Colors.white, fontSize: 14)),
                subtitle: const Text('Let family members replicate playlists to your account', style: TextStyle(fontSize: 11, color: Colors.grey)),
                value: allowPlaylists,
                onChanged: (val) => setDialogState(() => allowPlaylists = val),
              ),
              SwitchListTile(
                activeThumbColor: const Color(0xFFFF0000),
                title: const Text('Allow Family Sync', style: TextStyle(color: Colors.white, fontSize: 14)),
                subtitle: const Text('Include your account in one-click family bulk sync', style: TextStyle(fontSize: 11, color: Colors.grey)),
                value: allowSync,
                onChanged: (val) => setDialogState(() => allowSync = val),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: const Text('Cancel', style: TextStyle(color: Colors.grey)),
            ),
            ElevatedButton(
              style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFFFF0000)),
              onPressed: () async {
                if (_selectedFamily != null) {
                  await apiService.updateFamilyMemberPrivacy(
                    _selectedFamily!.id,
                    item.userId,
                    allowFamilyUploads: allowUploads,
                    allowFamilyPlaylists: allowPlaylists,
                    allowFamilySync: allowSync,
                  );
                  if (ctx.mounted) Navigator.of(ctx).pop();
                  _loadFamilyDetails(_selectedFamily!.id);
                }
              },
              child: const Text('Save Permissions', style: TextStyle(color: Colors.white)),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _removeMember(String userId) async {
    if (_selectedFamily == null) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF181824),
        title: const Text('Remove Family Member?'),
        content: const Text('This member will be removed from the family. Their account and upload history will be preserved.'),
        actions: [
          TextButton(onPressed: () => Navigator.of(ctx).pop(false), child: const Text('Cancel')),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      await apiService.removeFamilyMember(_selectedFamily!.id, userId);
      _loadFamilyDetails(_selectedFamily!.id);
    }
  }

  Widget _buildHistoryTab() {
    if (_history.isEmpty) {
      return const Center(
        child: Text('No family upload history yet', style: TextStyle(color: Colors.grey)),
      );
    }

    return ListView.separated(
      itemCount: _history.length,
      separatorBuilder: (_, _) => const Divider(color: Colors.white10, height: 1),
      itemBuilder: (ctx, idx) {
        final item = _history[idx];
        return ListTile(
          leading: const CircleAvatar(
            backgroundColor: Color(0xFF2A2A38),
            child: Icon(Icons.music_note, color: Colors.white70, size: 18),
          ),
          title: Text(item.displayTitle, style: const TextStyle(fontWeight: FontWeight.w600, color: Colors.white)),
          subtitle: Row(
            children: [
              Text(
                'Requested by ${item.requestedByUsername} ➔ Dest: ${item.destinationUsername}',
                style: const TextStyle(fontSize: 12, color: Color(0xFF3EA6FF)),
              ),
              if (item.completedAt != null) ...[
                const SizedBox(width: 8),
                Text(
                  '• ${item.completedAt!.toLocal().toString().substring(0, 16)}',
                  style: const TextStyle(fontSize: 11, color: Colors.grey),
                ),
              ],
            ],
          ),
          trailing: Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            decoration: BoxDecoration(
              color: item.status == 'verified' || item.status == 'completed'
                  ? Colors.green.withAlpha(30)
                  : Colors.amber.withAlpha(30),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(
              item.status.toUpperCase(),
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.bold,
                color: item.status == 'verified' || item.status == 'completed' ? Colors.greenAccent : Colors.amber,
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _buildQueueTab() {
    if (_queue.isEmpty) {
      return const Center(
        child: Text('Family upload queue is empty', style: TextStyle(color: Colors.grey)),
      );
    }

    return ListView.separated(
      itemCount: _queue.length,
      separatorBuilder: (_, _) => const Divider(color: Colors.white10, height: 1),
      itemBuilder: (ctx, idx) {
        final item = _queue[idx];
        return Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: const Color(0xFF181824),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(item.displayTitle, style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.white)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                children: item.destinations.map((d) {
                  return Chip(
                    backgroundColor: const Color(0xFF242432),
                    avatar: const Icon(Icons.account_circle, size: 16, color: Color(0xFF3EA6FF)),
                    label: Text('${d.destinationUsername}: ${d.status}', style: const TextStyle(fontSize: 11)),
                  );
                }).toList(),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildPlaylistsTab() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Text('Family Playlists', style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white)),
            const Spacer(),
            ElevatedButton.icon(
              onPressed: () => _showCreateOrClonePlaylistDialog(initialCloneMode: true),
              icon: const Icon(Icons.cloud_download, size: 16),
              label: const Text('Clone from YouTube Music'),
              style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF0288D1), foregroundColor: Colors.white),
            ),
            const SizedBox(width: 8),
            ElevatedButton.icon(
              onPressed: () => _showCreateOrClonePlaylistDialog(initialCloneMode: false),
              icon: const Icon(Icons.playlist_add, size: 16),
              label: const Text('New Empty Playlist'),
              style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFFFF0000), foregroundColor: Colors.white),
            ),
          ],
        ),
        const SizedBox(height: 12),
        if (_playlists.isEmpty)
          const Expanded(child: Center(child: Text('No family playlists found', style: TextStyle(color: Colors.grey))))
        else
          Expanded(
            child: ListView.separated(
              itemCount: _playlists.length,
              separatorBuilder: (_, _) => const Divider(color: Colors.white10, height: 1),
              itemBuilder: (ctx, idx) {
                final p = _playlists[idx];
                return ListTile(
                  leading: const Icon(Icons.playlist_play, color: Color(0xFFFF0000)),
                  title: Text(p.title, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                  subtitle: Text('Owner: ${p.ownerUsername} • ${p.trackCount} tracks', style: const TextStyle(color: Colors.grey, fontSize: 12)),
                );
              },
            ),
          ),
      ],
    );
  }

  Future<void> _showCreateOrClonePlaylistDialog({bool initialCloneMode = true}) async {
    if (_selectedFamily == null) return;
    final currentUserId = apiService.currentUser?.id;

    List<SelectableAccountItem> accounts = [];
    try {
      accounts = await apiService.getSelectableAccounts();
    } catch (_) {}

    final playlistMembers = accounts.where((a) => a.ytmConnected && (a.isSelf || a.allowFamilyPlaylists)).toList();
    if (playlistMembers.isEmpty && _dashboard != null) {
      for (final m in _dashboard!.members) {
        if (m.ytmConnected && (m.userId == currentUserId || m.allowFamilyPlaylists)) {
          playlistMembers.add(SelectableAccountItem(
            userId: m.userId,
            username: m.username,
            accountName: m.accountName,
            isConnected: m.ytmConnected,
            isSelf: m.userId == currentUserId,
            allowFamilyUploads: m.allowFamilyUploads,
            allowFamilyPlaylists: m.allowFamilyPlaylists,
            allowFamilySync: m.allowFamilySync,
          ));
        }
      }
    }

    bool isCloneMode = initialCloneMode;
    String? selectedSourceUserId = playlistMembers.any((m) => m.isSelf)
        ? playlistMembers.firstWhere((m) => m.isSelf).userId
        : (playlistMembers.isNotEmpty ? playlistMembers.first.userId : currentUserId);

    List<YTMPlaylist> sourcePlaylists = [];
    bool isLoadingPlaylists = false;
    String? playlistError;
    YTMPlaylist? selectedSourcePlaylist;
    bool hasLoadedInitial = false;

    final titleController = TextEditingController();
    final Set<String> targetUserIds = Set<String>.from(playlistMembers.map((m) => m.userId));
    bool uploadMissing = true;
    bool isSubmitting = false;
    String? submitError;

    Future<void> loadPlaylists(String? uid, void Function(void Function()) setDialogState) async {
      if (uid == null) return;
      setDialogState(() {
        isLoadingPlaylists = true;
        playlistError = null;
        sourcePlaylists = [];
        selectedSourcePlaylist = null;
      });
      try {
        final list = await apiService.fetchPlaylists(userId: uid);
        setDialogState(() {
          sourcePlaylists = list;
          isLoadingPlaylists = false;
          if (list.isNotEmpty) {
            selectedSourcePlaylist = list.first;
            titleController.text = list.first.title;
          }
        });
      } catch (e) {
        setDialogState(() {
          isLoadingPlaylists = false;
          playlistError = e.toString().replaceFirst('Exception: ', '');
        });
      }
    }

    if (!mounted) return;

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) {
          if (isCloneMode && !hasLoadedInitial && selectedSourceUserId != null) {
            hasLoadedInitial = true;
            WidgetsBinding.instance.addPostFrameCallback((_) {
              loadPlaylists(selectedSourceUserId, setDialogState);
            });
          }

          return AlertDialog(
            backgroundColor: const Color(0xFF181824),
            title: Row(
              children: [
                Icon(
                  isCloneMode ? Icons.cloud_download : Icons.playlist_add,
                  color: isCloneMode ? const Color(0xFF0288D1) : const Color(0xFFFF0000),
                ),
                const SizedBox(width: 10),
                Text(
                  isCloneMode ? 'Clone Playlist from YouTube Music' : 'Create Multi-Account Playlist',
                  style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 16),
                ),
              ],
            ),
            content: SizedBox(
              width: 500,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Mode Toggle
                    Row(
                      children: [
                        ChoiceChip(
                          label: const Text('Clone Existing from YTM'),
                          avatar: const Icon(Icons.cloud_download, size: 16),
                          selected: isCloneMode,
                          selectedColor: const Color(0xFF0288D1).withValues(alpha: 0.3),
                          side: BorderSide(color: isCloneMode ? const Color(0xFF0288D1) : Colors.white24),
                          onSelected: (val) {
                            if (val) {
                              setDialogState(() {
                                isCloneMode = true;
                                if (!hasLoadedInitial && selectedSourceUserId != null) {
                                  hasLoadedInitial = true;
                                  loadPlaylists(selectedSourceUserId, setDialogState);
                                }
                              });
                            }
                          },
                        ),
                        const SizedBox(width: 10),
                        ChoiceChip(
                          label: const Text('Create Blank'),
                          avatar: const Icon(Icons.add, size: 16),
                          selected: !isCloneMode,
                          selectedColor: const Color(0xFFFF0000).withValues(alpha: 0.3),
                          side: BorderSide(color: !isCloneMode ? const Color(0xFFFF0000) : Colors.white24),
                          onSelected: (val) {
                            if (val) {
                              setDialogState(() {
                                isCloneMode = false;
                              });
                            }
                          },
                        ),
                      ],
                    ),
                    const SizedBox(height: 18),

                    if (isCloneMode) ...[
                      // Step 1: Source User
                      const Text('1. Select Source Account', style: TextStyle(fontSize: 12, color: Colors.grey, fontWeight: FontWeight.bold)),
                      const SizedBox(height: 6),
                      DropdownButtonFormField<String>(
                        dropdownColor: const Color(0xFF1E1E28),
                        style: const TextStyle(color: Colors.white, fontSize: 13),
                        initialValue: selectedSourceUserId,
                        decoration: const InputDecoration(
                          border: OutlineInputBorder(),
                          contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                          prefixIcon: Icon(Icons.person, color: Color(0xFF0288D1)),
                        ),
                        items: playlistMembers.map((m) {
                          return DropdownMenuItem(
                            value: m.userId,
                            child: Text('${m.username} ${m.isSelf ? "(You)" : (m.accountName != null ? "(${m.accountName})" : "")}'),
                          );
                        }).toList(),
                        onChanged: isSubmitting
                            ? null
                            : (val) {
                                setDialogState(() {
                                  selectedSourceUserId = val;
                                });
                                loadPlaylists(val, setDialogState);
                              },
                      ),
                      const SizedBox(height: 16),

                      // Step 2: Source Playlist
                      const Text('2. Select YouTube Music Playlist to Clone', style: TextStyle(fontSize: 12, color: Colors.grey, fontWeight: FontWeight.bold)),
                      const SizedBox(height: 6),
                      if (isLoadingPlaylists)
                        const Padding(
                          padding: EdgeInsets.symmetric(vertical: 16),
                          child: Center(
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF0288D1))),
                                SizedBox(width: 12),
                                Text('Fetching playlists from YouTube Music...', style: TextStyle(fontSize: 12, color: Colors.grey)),
                              ],
                            ),
                          ),
                        )
                      else if (playlistError != null)
                        Container(
                          padding: const EdgeInsets.all(10),
                          decoration: BoxDecoration(
                            color: Colors.amber.withValues(alpha: 0.1),
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(color: Colors.amber.withValues(alpha: 0.3)),
                          ),
                          child: Row(
                            children: [
                              const Icon(Icons.warning, size: 16, color: Colors.amber),
                              const SizedBox(width: 8),
                              Expanded(child: Text(playlistError!, style: const TextStyle(fontSize: 12, color: Colors.amber))),
                              IconButton(
                                icon: const Icon(Icons.refresh, size: 16),
                                onPressed: () => loadPlaylists(selectedSourceUserId, setDialogState),
                              ),
                            ],
                          ),
                        )
                      else if (sourcePlaylists.isEmpty)
                        const Text('No playlists found for this account', style: TextStyle(color: Colors.grey, fontSize: 13))
                      else
                        DropdownButtonFormField<String>(
                          dropdownColor: const Color(0xFF1E1E28),
                          style: const TextStyle(color: Colors.white, fontSize: 13),
                          initialValue: selectedSourcePlaylist?.id,
                          decoration: const InputDecoration(
                            border: OutlineInputBorder(),
                            contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                            prefixIcon: Icon(Icons.queue_music, color: Color(0xFF0288D1)),
                          ),
                          items: sourcePlaylists.map((p) {
                            return DropdownMenuItem(
                              value: p.id,
                              child: Text(
                                '${p.title} (${p.trackCount != null ? "${p.trackCount} tracks" : "Live"})',
                                overflow: TextOverflow.ellipsis,
                              ),
                            );
                          }).toList(),
                          onChanged: isSubmitting
                              ? null
                              : (pId) {
                                  final chosen = sourcePlaylists.firstWhere((p) => p.id == pId);
                                  setDialogState(() {
                                    selectedSourcePlaylist = chosen;
                                    titleController.text = chosen.title;
                                  });
                                },
                        ),
                      const SizedBox(height: 16),

                      // Destination Playlist Title
                      const Text('Destination Playlist Name', style: TextStyle(fontSize: 12, color: Colors.grey)),
                      const SizedBox(height: 4),
                      TextField(
                        controller: titleController,
                        enabled: !isSubmitting,
                        style: const TextStyle(color: Colors.white, fontSize: 13),
                        decoration: const InputDecoration(
                          border: OutlineInputBorder(),
                          contentPadding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                        ),
                      ),
                      const SizedBox(height: 16),
                    ] else ...[
                      // Blank Playlist Title
                      TextField(
                        controller: titleController,
                        enabled: !isSubmitting,
                        style: const TextStyle(color: Colors.white),
                        decoration: const InputDecoration(
                          labelText: 'Playlist Title',
                          labelStyle: TextStyle(color: Colors.grey),
                          border: OutlineInputBorder(),
                        ),
                      ),
                      const SizedBox(height: 16),
                    ],

                    // Step 3: Target Accounts
                    const Text('Target Accounts (members who permit playlists):', style: TextStyle(fontSize: 12, color: Colors.grey, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 6),
                    Container(
                      decoration: BoxDecoration(
                        color: const Color(0xFF12121A),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
                      ),
                      child: Column(
                        children: playlistMembers.map((m) {
                          final isSelected = targetUserIds.contains(m.userId);
                          return CheckboxListTile(
                            dense: true,
                            title: Row(
                              children: [
                                Text(m.username, style: const TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.bold)),
                                if (m.accountName != null && m.accountName!.isNotEmpty) ...[
                                  const SizedBox(width: 8),
                                  Text('(${m.accountName})', style: const TextStyle(color: Colors.grey, fontSize: 12)),
                                ],
                                if (m.isSelf) ...[
                                  const SizedBox(width: 8),
                                  Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                                    decoration: BoxDecoration(
                                      color: Colors.blueAccent.withValues(alpha: 0.2),
                                      borderRadius: BorderRadius.circular(4),
                                    ),
                                    child: const Text('You', style: TextStyle(fontSize: 10, color: Colors.lightBlueAccent, fontWeight: FontWeight.bold)),
                                  ),
                                ],
                              ],
                            ),
                            value: isSelected,
                            activeColor: isCloneMode ? const Color(0xFF0288D1) : const Color(0xFFFF0000),
                            onChanged: isSubmitting
                                ? null
                                : (val) {
                                    setDialogState(() {
                                      if (val == true) {
                                        targetUserIds.add(m.userId);
                                      } else if (targetUserIds.length > 1) {
                                        targetUserIds.remove(m.userId);
                                      }
                                    });
                                  },
                          );
                        }).toList(),
                      ),
                    ),

                    if (isCloneMode) ...[
                      const SizedBox(height: 10),
                      CheckboxListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        value: uploadMissing,
                        activeColor: const Color(0xFF8A2387),
                        title: const Text(
                          'Download & Upload missing songs to selected member lockers',
                          style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
                        ),
                        subtitle: const Text(
                          'Ensures all members have the tracks uploaded into their personal cloud locker so everyone can play them.',
                          style: TextStyle(fontSize: 11, color: Colors.grey),
                        ),
                        onChanged: isSubmitting
                            ? null
                            : (val) {
                                setDialogState(() {
                                  uploadMissing = val ?? true;
                                });
                              },
                      ),
                    ],

                    if (submitError != null) ...[
                      const SizedBox(height: 12),
                      Text(submitError!, style: const TextStyle(color: Colors.redAccent, fontSize: 13)),
                    ],
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: isSubmitting ? null : () => Navigator.of(ctx).pop(),
                child: const Text('Cancel'),
              ),
              ElevatedButton.icon(
                style: ElevatedButton.styleFrom(
                  backgroundColor: isCloneMode ? const Color(0xFF0288D1) : const Color(0xFFFF0000),
                  foregroundColor: Colors.white,
                ),
                icon: isSubmitting
                    ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                    : Icon(isCloneMode ? Icons.cloud_download : Icons.check, size: 16),
                label: Text(isCloneMode ? 'Clone Playlist to Family' : 'Create Blank Playlist'),
                onPressed: isSubmitting
                    ? null
                    : () async {
                        final title = titleController.text.trim();
                        if (title.isEmpty) {
                          setDialogState(() => submitError = 'Playlist title cannot be empty');
                          return;
                        }
                        if (targetUserIds.isEmpty) {
                          setDialogState(() => submitError = 'Select at least one destination account');
                          return;
                        }
                        if (isCloneMode && selectedSourcePlaylist == null) {
                          setDialogState(() => submitError = 'Please select a source playlist to clone');
                          return;
                        }

                        setDialogState(() {
                          isSubmitting = true;
                          submitError = null;
                        });

                        try {
                          await apiService.createMultiPlaylists(
                            _selectedFamily!.id,
                            title,
                            targetUserIds.toList(),
                            sourcePlaylistId: isCloneMode ? selectedSourcePlaylist?.id : null,
                            sourceUserId: isCloneMode ? selectedSourceUserId : null,
                            uploadMissingToTargets: isCloneMode && uploadMissing,
                          );
                          if (ctx.mounted) {
                            Navigator.of(ctx).pop();
                          }
                          if (mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(
                                content: Text(
                                  isCloneMode
                                      ? 'Cloned "$title" across ${targetUserIds.length} family accounts!'
                                      : 'Created multi-account playlist "$title"',
                                ),
                                backgroundColor: isCloneMode ? const Color(0xFF0288D1) : const Color(0xFFFF0000),
                              ),
                            );
                            _loadFamilyDetails(_selectedFamily!.id);
                          }
                        } catch (e) {
                          setDialogState(() {
                            isSubmitting = false;
                            submitError = e.toString().replaceFirst('Exception: ', '');
                          });
                        }
                      },
              ),
            ],
          );
        },
      ),
    );
  }
}
