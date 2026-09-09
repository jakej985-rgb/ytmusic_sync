import 'package:flutter/material.dart';
import '../../models/models.dart';
import '../../services/api_service.dart';

class UploadDestinationDialog extends StatefulWidget {
  final List<MusicFile> tracks;

  const UploadDestinationDialog({
    super.key,
    required this.tracks,
  });

  static Future<UploadDestinationResponse?> show(BuildContext context, List<MusicFile> tracks) {
    return showDialog<UploadDestinationResponse>(
      context: context,
      builder: (ctx) => UploadDestinationDialog(tracks: tracks),
    );
  }

  @override
  State<UploadDestinationDialog> createState() => _UploadDestinationDialogState();
}

class _UploadDestinationDialogState extends State<UploadDestinationDialog> {
  List<SelectableAccountItem> _accounts = [];
  Map<String, TrackDestinationDuplicateStatus> _duplicateStatuses = {};
  final Set<String> _selectedUserIds = {};
  bool _isLoading = true;
  bool _isSubmitting = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _loadDestinationsAndStatuses();
  }

  Future<void> _loadDestinationsAndStatuses() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final accounts = await apiService.getSelectableAccounts();
      final duplicateMap = <String, TrackDestinationDuplicateStatus>{};

      // If single track, fetch duplicate status per destination
      if (widget.tracks.length == 1 && widget.tracks.first.id != null) {
        try {
          final statuses = await apiService.getTrackDestinationsStatus(widget.tracks.first.id!);
          for (final s in statuses) {
            duplicateMap[s.destinationUserId] = s;
          }
        } catch (_) {}
      }

      if (!mounted) return;

      setState(() {
        _accounts = accounts;
        _duplicateStatuses = duplicateMap;
        // Pre-select current user by default
        final self = accounts.where((a) => a.isSelf).firstOrNull;
        if (self != null) {
          _selectedUserIds.add(self.userId);
        }
        _isLoading = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          _errorMessage = e.toString();
        });
      }
    }
  }

  String _getSubmitButtonLabel() {
    if (_selectedUserIds.isEmpty) return 'Select a Destination';
    if (_selectedUserIds.length == 1) {
      final dest = _accounts.firstWhere(
        (a) => a.userId == _selectedUserIds.first,
        orElse: () => SelectableAccountItem(
          userId: '',
          username: 'account',
          isConnected: true,
          isSelf: false,
          allowFamilyUploads: true,
        ),
      );
      final name = dest.isSelf ? 'My Account' : dest.username;
      return 'Upload to $name';
    }
    return 'Upload to ${_selectedUserIds.length} accounts';
  }

  Future<void> _submit() async {
    if (_selectedUserIds.isEmpty || _isSubmitting) return;

    setState(() {
      _isSubmitting = true;
      _errorMessage = null;
    });

    try {
      final fileIds = widget.tracks.where((t) => t.id != null).map((t) => t.id!).toList();
      final res = await apiService.uploadToDestinations(
        fileIds,
        _selectedUserIds.toList(),
      );
      if (mounted) {
        Navigator.of(context).pop(res);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isSubmitting = false;
          _errorMessage = e.toString().replaceAll('Exception: ', '');
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      backgroundColor: const Color(0xFF181824),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      titlePadding: const EdgeInsets.fromLTRB(24, 20, 24, 12),
      contentPadding: const EdgeInsets.symmetric(horizontal: 24),
      actionsPadding: const EdgeInsets.fromLTRB(24, 16, 24, 20),
      title: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(8),
            decoration: BoxDecoration(
              color: const Color(0xFFFF0000).withAlpha(30),
              borderRadius: BorderRadius.circular(8),
            ),
            child: const Icon(Icons.cloud_upload, color: Color(0xFFFF0000), size: 22),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Select Upload Destination',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold, color: Colors.white),
                ),
                Text(
                  widget.tracks.length == 1
                      ? widget.tracks.first.displayTitle
                      : '${widget.tracks.length} tracks selected',
                  style: const TextStyle(fontSize: 12, color: Colors.grey),
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
        ],
      ),
      content: SizedBox(
        width: 480,
        child: _isLoading
            ? const SizedBox(
                height: 180,
                child: Center(child: CircularProgressIndicator()),
              )
            : Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  if (_errorMessage != null) ...[
                    Container(
                      padding: const EdgeInsets.all(10),
                      margin: const EdgeInsets.only(bottom: 12),
                      decoration: BoxDecoration(
                        color: Colors.red.withAlpha(40),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: Colors.redAccent.withAlpha(100)),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.error_outline, size: 16, color: Colors.redAccent),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              _errorMessage!,
                              style: const TextStyle(color: Colors.redAccent, fontSize: 12),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                  const Text(
                    'Choose which YouTube Music account(s) will receive these tracks. Each upload is processed independently with that account\'s credentials.',
                    style: TextStyle(fontSize: 12, color: Colors.white70),
                  ),
                  const SizedBox(height: 16),
                  Flexible(
                    child: ListView.separated(
                      shrinkWrap: true,
                      itemCount: _accounts.length,
                      separatorBuilder: (_, _) => const Divider(color: Colors.white10, height: 1),
                      itemBuilder: (ctx, idx) {
                        final acc = _accounts[idx];
                        final isSelected = _selectedUserIds.contains(acc.userId);
                        final dupStatus = _duplicateStatuses[acc.userId];
                        final isAlreadyUploaded = dupStatus?.isUploaded ?? false;

                        return CheckboxListTile(
                          contentPadding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                          activeColor: const Color(0xFFFF0000),
                          value: isSelected,
                          onChanged: (bool? val) {
                            setState(() {
                              if (val == true) {
                                _selectedUserIds.add(acc.userId);
                              } else {
                                _selectedUserIds.remove(acc.userId);
                              }
                            });
                          },
                          secondary: CircleAvatar(
                            radius: 16,
                            backgroundColor: acc.isSelf ? const Color(0xFFFF0000) : const Color(0xFF3EA6FF),
                            child: Icon(
                              acc.isSelf ? Icons.person : Icons.group,
                              size: 16,
                              color: Colors.white,
                            ),
                          ),
                          title: Row(
                            children: [
                              Text(
                                acc.displayName,
                                style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                              ),
                              if (acc.familyName != null) ...[
                                const SizedBox(width: 6),
                                Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                                  decoration: BoxDecoration(
                                    color: const Color(0xFF2A2A38),
                                    borderRadius: BorderRadius.circular(4),
                                  ),
                                  child: Text(
                                    acc.familyName!,
                                    style: const TextStyle(fontSize: 10, color: Colors.grey),
                                  ),
                                ),
                              ],
                            ],
                          ),
                          subtitle: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                acc.displayAccount,
                                style: const TextStyle(fontSize: 11, color: Colors.grey),
                              ),
                              if (isAlreadyUploaded) ...[
                                const SizedBox(height: 4),
                                Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                                  decoration: BoxDecoration(
                                    color: Colors.amber.withAlpha(40),
                                    borderRadius: BorderRadius.circular(4),
                                    border: Border.all(color: Colors.amber.withAlpha(80)),
                                  ),
                                  child: const Text(
                                    'Already uploaded to this account',
                                    style: TextStyle(fontSize: 10, color: Colors.amber, fontWeight: FontWeight.w600),
                                  ),
                                ),
                              ],
                            ],
                          ),
                        );
                      },
                    ),
                  ),
                ],
              ),
      ),
      actions: [
        TextButton(
          onPressed: _isSubmitting ? null : () => Navigator.of(context).pop(),
          child: const Text('Cancel', style: TextStyle(color: Colors.grey)),
        ),
        ElevatedButton(
          onPressed: _selectedUserIds.isEmpty || _isSubmitting ? null : _submit,
          style: ElevatedButton.styleFrom(
            backgroundColor: const Color(0xFFFF0000),
            foregroundColor: Colors.white,
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          ),
          child: _isSubmitting
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                )
              : Text(_getSubmitButtonLabel(), style: const TextStyle(fontWeight: FontWeight.bold)),
        ),
      ],
    );
  }
}
