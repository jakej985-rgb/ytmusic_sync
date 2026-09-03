import 'dart:async';
import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_service.dart';

class PlaylistsView extends StatefulWidget {
  const PlaylistsView({super.key});

  @override
  State<PlaylistsView> createState() => _PlaylistsViewState();
}

class _PlaylistsViewState extends State<PlaylistsView> {
  List<YTMPlaylist> _playlists = [];
  bool _isLoading = false;
  String? _errorMessage;

  YTMPlaylist? _selectedPlaylist;
  YTMPlaylistDetails? _playlistDetails;
  bool _isLoadingDetails = false;
  String? _detailsErrorMessage;

  String _searchQuery = '';
  String _trackFilter = 'all'; // 'all', 'local', 'uploads', 'missing'

  Timer? _syncPollTimer;
  PlaylistSyncStatusModel? _syncStatus;
  final Set<String> _downloadingVideoIds = {};

  List<ReplicatedPlaylistModel> _replicatedPlaylists = [];

  ReplicatedPlaylistModel? get _currentReplicaConfig {
    if (_selectedPlaylist == null) return null;
    try {
      return _replicatedPlaylists.firstWhere((r) => r.sourcePlaylistId == _selectedPlaylist!.id);
    } catch (_) {
      return null;
    }
  }

  @override
  void initState() {
    super.initState();
    _loadPlaylists();
    _loadReplicatedPlaylists();
    _checkInitialSyncStatus();
  }

  @override
  void dispose() {
    _syncPollTimer?.cancel();
    super.dispose();
  }

  Future<void> _checkInitialSyncStatus() async {
    try {
      final status = await apiService.getPlaylistSyncStatus();
      if (mounted && status.isRunning) {
        setState(() => _syncStatus = status);
        _startSyncPolling();
      }
    } catch (_) {}
  }

  void _startSyncPolling() {
    _syncPollTimer?.cancel();
    _syncPollTimer = Timer.periodic(const Duration(seconds: 2), (_) async {
      try {
        final status = await apiService.getPlaylistSyncStatus();
        if (mounted) {
          setState(() => _syncStatus = status);
          if (!status.isRunning) {
            _stopSyncPolling();
            if (_selectedPlaylist != null) {
              _selectPlaylist(_selectedPlaylist!);
            }
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(
                content: Text('Playlist sync completed: ${status.completedTracks} uploaded, ${status.failedTracks} failed.'),
                backgroundColor: status.failedTracks > 0 ? Colors.amber[800] : Colors.green,
              ),
            );
          }
        }
      } catch (_) {}
    });
  }

  void _stopSyncPolling() {
    _syncPollTimer?.cancel();
    _syncPollTimer = null;
  }

  Future<void> _loadReplicatedPlaylists() async {
    try {
      final list = await apiService.fetchReplicatedPlaylists();
      if (mounted) {
        setState(() => _replicatedPlaylists = list);
      }
    } catch (_) {}
  }

  Future<void> _loadPlaylists() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    _loadReplicatedPlaylists();

    try {
      final list = await apiService.fetchPlaylists();
      if (mounted) {
        setState(() {
          _playlists = list;
          _isLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _errorMessage = e.toString().replaceFirst('Exception: ', '');
          _isLoading = false;
        });
      }
    }
  }

  Future<void> _selectPlaylist(YTMPlaylist playlist, {bool refresh = false}) async {
    setState(() {
      _selectedPlaylist = playlist;
      _isLoadingDetails = true;
      _detailsErrorMessage = null;
      _trackFilter = 'all';
    });

    try {
      final details = await apiService.fetchPlaylistDetails(playlist.id, refresh: refresh);
      if (mounted) {
        setState(() {
          _playlistDetails = details;
          _isLoadingDetails = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _detailsErrorMessage = e.toString().replaceFirst('Exception: ', '');
          _isLoadingDetails = false;
        });
      }
    }
  }

  Future<void> _syncMissingTracks() async {
    if (_selectedPlaylist == null) return;
    try {
      final res = await apiService.syncMissingPlaylistTracks(_selectedPlaylist!.id);
      final queued = res['queued'] as int? ?? 0;
      if (mounted) {
        if (queued > 0) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text('Started background sync for $queued missing tracks via yt-dlp!'),
              backgroundColor: const Color(0xFF8A2387),
            ),
          );
          _startSyncPolling();
        } else {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('All tracks in this playlist are already in your uploads!')),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Sync error: ${e.toString().replaceFirst('Exception: ', '')}'),
            backgroundColor: Colors.redAccent,
          ),
        );
      }
    }
  }

  Future<void> _downloadAndUploadSingleTrack(YTMPlaylistTrack track) async {
    if (track.videoId == null) return;
    setState(() {
      _downloadingVideoIds.add(track.videoId!);
    });

    try {
      final res = await apiService.downloadAndUploadPlaylistTrack({
        'video_id': track.videoId,
        'title': track.title,
        'artist': track.artist,
        'album': track.album,
        'thumbnail': track.thumbnail,
        'enrich_metadata': true,
      });

      if (mounted) {
        setState(() {
          _downloadingVideoIds.remove(track.videoId);
          if (_playlistDetails != null) {
            final idx = _playlistDetails!.tracks.indexWhere((t) => t.videoId == track.videoId);
            if (idx != -1) {
              final updated = _playlistDetails!.tracks[idx].copyWith(
                inUploads: true,
                inLocal: res['local_path'] != null ? true : null,
                localPath: res['local_path'] as String?,
              );
              _playlistDetails!.tracks[idx] = updated;
            }
          }
        });

        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Downloaded, tagged, and uploaded "${track.title}" to YouTube Music!'),
            backgroundColor: Colors.green,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _downloadingVideoIds.remove(track.videoId);
        });
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Download failed: ${e.toString().replaceFirst('Exception: ', '')}'),
            backgroundColor: Colors.redAccent,
          ),
        );
      }
    }
  }

  Future<void> _showImportPlaylistDialog() async {
    final controller = TextEditingController();
    bool isImporting = false;
    String? importError;

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          backgroundColor: const Color(0xFF1E1E28),
          title: const Row(
            children: [
              Icon(Icons.link, color: Colors.blueAccent),
              SizedBox(width: 8),
              Text('Import YouTube Playlist'),
            ],
          ),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Enter any YouTube or YouTube Music playlist URL (public or unlisted) to audit and download missing tracks.',
                style: TextStyle(fontSize: 13, color: Colors.grey),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: controller,
                enabled: !isImporting,
                decoration: const InputDecoration(
                  hintText: 'https://www.youtube.com/playlist?list=...',
                  prefixIcon: Icon(Icons.playlist_play),
                  border: OutlineInputBorder(),
                ),
              ),
              if (isImporting) ...[
                const SizedBox(height: 16),
                const Row(
                  children: [
                    SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                    SizedBox(width: 12),
                    Expanded(
                      child: Text('Extracting playlist tracks via yt-dlp...', style: TextStyle(fontSize: 12, color: Colors.grey)),
                    ),
                  ],
                ),
              ],
              if (importError != null) ...[
                const SizedBox(height: 12),
                Text(importError!, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
              ],
            ],
          ),
          actions: [
            TextButton(
              onPressed: isImporting ? null : () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            ElevatedButton(
              onPressed: isImporting ? null : () async {
                final url = controller.text.trim();
                if (url.isEmpty) return;
                setDialogState(() {
                  isImporting = true;
                  importError = null;
                });
                try {
                  final details = await apiService.importPlaylistUrl(url);
                  if (ctx.mounted) {
                    Navigator.pop(ctx);
                  }
                  if (mounted) {
                    setState(() {
                      _selectedPlaylist = YTMPlaylist(
                        id: details.id,
                        title: details.title,
                        description: details.description,
                        trackCount: details.trackCount,
                        thumbnail: details.thumbnail,
                      );
                      _playlistDetails = details;
                      _isLoadingDetails = false;
                      _trackFilter = 'all';
                    });
                  }
                } catch (e) {
                  setDialogState(() {
                    isImporting = false;
                    importError = e.toString().replaceFirst('Exception: ', '');
                  });
                }
              },
              child: const Text('Import & Audit'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _showAllReplicasDialog() async {
    await showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF1E1E28),
        title: const Row(
          children: [
            Icon(Icons.sync_alt, color: Colors.tealAccent),
            SizedBox(width: 10),
            Text('Active Locker Replicas'),
          ],
        ),
        content: SizedBox(
          width: 500,
          child: _replicatedPlaylists.isEmpty
              ? const Text('No active replicas configured yet.', style: TextStyle(color: Colors.grey))
              : ListView.separated(
                  shrinkWrap: true,
                  itemCount: _replicatedPlaylists.length,
                  separatorBuilder: (context, index) => const Divider(color: Colors.white10),
                  itemBuilder: (ctx, idx) {
                    final r = _replicatedPlaylists[idx];
                    return ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.queue_music, color: Colors.tealAccent),
                      title: Text(r.sourcePlaylistName, style: const TextStyle(fontWeight: FontWeight.bold)),
                      subtitle: Text('Replica: ${r.destinationPlaylistName}', style: TextStyle(fontSize: 12, color: Colors.grey[400])),
                      trailing: ElevatedButton(
                        onPressed: () {
                          Navigator.pop(ctx);
                          _openReplicationModal(r);
                        },
                        style: ElevatedButton.styleFrom(
                          backgroundColor: const Color(0xFF00897B),
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                        ),
                        child: const Text('Manage', style: TextStyle(fontSize: 12, color: Colors.white)),
                      ),
                    );
                  },
                ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  Future<void> _openCreateReplicaDialog(YTMPlaylist playlist) async {
    final destNameController = TextEditingController(text: '${playlist.title} - Locker');
    bool isCreating = false;
    String? errorMsg;

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDialogState) => AlertDialog(
          backgroundColor: const Color(0xFF1E1E28),
          title: const Row(
            children: [
              Icon(Icons.copy_all, color: Color(0xFF0288D1)),
              SizedBox(width: 10),
              Text('Create 1:1 Locker Replica'),
            ],
          ),
          content: SizedBox(
            width: 480,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: const Color(0xFF0288D1).withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: const Color(0xFF0288D1).withValues(alpha: 0.3)),
                  ),
                  child: const Text(
                    'Creates an automated YouTube Music playlist containing ONLY songs verified in your Upload Locker, in exact 1:1 source order.',
                    style: TextStyle(fontSize: 13, color: Colors.white70),
                  ),
                ),
                const SizedBox(height: 16),
                const Text('Source Playlist', style: TextStyle(fontSize: 12, color: Colors.grey)),
                const SizedBox(height: 4),
                Text(
                  playlist.title,
                  style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
                ),
                const SizedBox(height: 16),
                const Text('Destination Replica Name', style: TextStyle(fontSize: 12, color: Colors.grey)),
                const SizedBox(height: 4),
                TextField(
                  controller: destNameController,
                  enabled: !isCreating,
                  decoration: const InputDecoration(
                    border: OutlineInputBorder(),
                    prefixIcon: Icon(Icons.queue_music),
                  ),
                ),
                const SizedBox(height: 16),
                const Row(
                  children: [
                    Icon(Icons.verified, size: 16, color: Colors.tealAccent),
                    SizedBox(width: 6),
                    Text('Mode: Locker Only (Verified Uploads)', style: TextStyle(fontSize: 12, color: Colors.tealAccent)),
                  ],
                ),
                if (isCreating) ...[
                  const SizedBox(height: 16),
                  const Row(
                    children: [
                      SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                      SizedBox(width: 12),
                      Text('Creating & Reconciling replica playlist...', style: TextStyle(fontSize: 12, color: Colors.grey)),
                    ],
                  ),
                ],
                if (errorMsg != null) ...[
                  const SizedBox(height: 12),
                  Text(errorMsg!, style: const TextStyle(color: Colors.redAccent, fontSize: 13)),
                ],
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: isCreating ? null : () => Navigator.pop(ctx),
              child: const Text('Cancel'),
            ),
            ElevatedButton.icon(
              onPressed: isCreating
                  ? null
                  : () async {
                      final destName = destNameController.text.trim();
                      if (destName.isEmpty) return;

                      setDialogState(() {
                        isCreating = true;
                        errorMsg = null;
                      });

                      try {
                        final created = await apiService.createReplicatedPlaylist({
                          'source_playlist_id': playlist.id,
                          'source_playlist_name': playlist.title,
                          'destination_playlist_name': destName,
                          'enabled': true,
                        });
                        await apiService.syncReplicatedPlaylist(created.id);
                        await _loadReplicatedPlaylists();
                        if (ctx.mounted) {
                          Navigator.pop(ctx);
                          _openReplicationModal(created);
                        }
                      } catch (e) {
                        setDialogState(() {
                          isCreating = false;
                          errorMsg = e.toString().replaceFirst('Exception: ', '');
                        });
                      }
                    },
              icon: const Icon(Icons.check, size: 16),
              label: const Text('Create & Sync Replica'),
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF0288D1),
                foregroundColor: Colors.white,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _openReplicationModal(ReplicatedPlaylistModel replica) async {
    bool isActionRunning = false;
    String? actionStatus;
    ReplicationPreviewModel? preview;
    bool isLoadingPreview = true;
    String? loadError;
    bool showExcludedDetails = false;

    await showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setModalState) {
          void fetchPreviewData() async {
            try {
              final p = await apiService.fetchReplicatedPlaylist(replica.id);
              if (ctx.mounted) {
                setModalState(() {
                  preview = p;
                  isLoadingPreview = false;
                });
              }
            } catch (e) {
              if (ctx.mounted) {
                setModalState(() {
                  loadError = e.toString().replaceFirst('Exception: ', '');
                  isLoadingPreview = false;
                });
              }
            }
          }

          if (isLoadingPreview && preview == null && loadError == null) {
            fetchPreviewData();
          }

          return AlertDialog(
            backgroundColor: const Color(0xFF1E1E28),
            title: Row(
              children: [
                const Icon(Icons.sync_alt, color: Colors.tealAccent),
                const SizedBox(width: 10),
                const Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('Playlist Replication', style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                      Text('1:1 Locker-Only Replica Engine', style: TextStyle(fontSize: 12, color: Colors.grey)),
                    ],
                  ),
                ),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: Colors.green.withValues(alpha: 0.2),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: Colors.greenAccent.withValues(alpha: 0.4)),
                  ),
                  child: const Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.circle, size: 8, color: Colors.greenAccent),
                      SizedBox(width: 6),
                      Text('Watching', style: TextStyle(fontSize: 11, color: Colors.greenAccent, fontWeight: FontWeight.bold)),
                    ],
                  ),
                ),
              ],
            ),
            content: SizedBox(
              width: 540,
              child: isLoadingPreview
                  ? const SizedBox(
                      height: 200,
                      child: Center(child: CircularProgressIndicator(color: Colors.tealAccent)),
                    )
                  : loadError != null
                      ? Text('Error loading replica: $loadError', style: const TextStyle(color: Colors.amberAccent))
                      : SingleChildScrollView(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              // Config Details (Section 23 of plan)
                              Container(
                                padding: const EdgeInsets.all(16),
                                decoration: BoxDecoration(
                                  color: const Color(0xFF14141E),
                                  borderRadius: BorderRadius.circular(10),
                                  border: Border.all(color: Colors.white10),
                                ),
                                child: Column(
                                  children: [
                                    _buildReplicaDetailRow('Source Playlist', preview!.sourcePlaylistName, Icons.queue_music),
                                    const Divider(height: 20, color: Colors.white10),
                                    _buildReplicaDetailRow('Locker Replica', preview!.destinationPlaylistName, Icons.cloud_done),
                                    const Divider(height: 20, color: Colors.white10),
                                    _buildReplicaDetailRow('Mode', 'Locker Only (1:1 Ordered)', Icons.lock),
                                  ],
                                ),
                              ),
                              const SizedBox(height: 16),

                              // Metrics Grid (Section 23 of plan)
                              Row(
                                children: [
                                  _buildMetricCard('Source Tracks', '${preview!.sourceTracksCount}', Colors.blueAccent),
                                  const SizedBox(width: 8),
                                  _buildMetricCard('Locker Matches', '${preview!.desiredTracksCount}', Colors.tealAccent),
                                  const SizedBox(width: 8),
                                  _buildMetricCard('Excluded', '${preview!.excludedCount}', Colors.amberAccent),
                                  const SizedBox(width: 8),
                                  _buildMetricCard('Destination', '${preview!.desiredTracksCount}', Colors.purpleAccent),
                                ],
                              ),
                              const SizedBox(height: 16),

                              // Excluded Tracks section (Section 24 & 25 of plan)
                              if (preview!.excludedCount > 0) ...[
                                InkWell(
                                  onTap: () {
                                    setModalState(() => showExcludedDetails = !showExcludedDetails);
                                  },
                                  borderRadius: BorderRadius.circular(8),
                                  child: Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                                    decoration: BoxDecoration(
                                      color: Colors.amber.withValues(alpha: 0.1),
                                      borderRadius: BorderRadius.circular(8),
                                      border: Border.all(color: Colors.amber.withValues(alpha: 0.3)),
                                    ),
                                    child: Row(
                                      children: [
                                        const Icon(Icons.info_outline, size: 16, color: Colors.amberAccent),
                                        const SizedBox(width: 8),
                                        Expanded(
                                          child: Text(
                                            '${preview!.excludedCount} tracks not uploaded to locker (excluded from replica)',
                                            style: const TextStyle(fontSize: 12, color: Colors.amberAccent),
                                          ),
                                        ),
                                        Icon(showExcludedDetails ? Icons.expand_less : Icons.expand_more, size: 18, color: Colors.amberAccent),
                                      ],
                                    ),
                                  ),
                                ),
                                if (showExcludedDetails) ...[
                                  const SizedBox(height: 8),
                                  Container(
                                    constraints: const BoxConstraints(maxHeight: 180),
                                    decoration: BoxDecoration(
                                      color: const Color(0xFF14141E),
                                      borderRadius: BorderRadius.circular(8),
                                      border: Border.all(color: Colors.white10),
                                    ),
                                    child: ListView.builder(
                                      shrinkWrap: true,
                                      itemCount: preview!.excludedTracks.length,
                                      itemBuilder: (ctx, i) {
                                        final item = preview!.excludedTracks[i];
                                        return ListTile(
                                          dense: true,
                                          visualDensity: VisualDensity.compact,
                                          leading: const Icon(Icons.remove_circle_outline, size: 16, color: Colors.amberAccent),
                                          title: Text('${item.artist} - ${item.title}', style: const TextStyle(fontSize: 13)),
                                          subtitle: Text(item.humanReason, style: TextStyle(fontSize: 11, color: Colors.grey[400])),
                                        );
                                      },
                                    ),
                                  ),
                                ],
                                const SizedBox(height: 16),
                              ],

                              if (isActionRunning) ...[
                                Row(
                                  children: [
                                    const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                                    const SizedBox(width: 12),
                                    Text(actionStatus ?? 'Processing...', style: const TextStyle(fontSize: 12, color: Colors.grey)),
                                  ],
                                ),
                                const SizedBox(height: 12),
                              ],
                            ],
                          ),
                        ),
            ),
            actions: [
              TextButton(
                onPressed: isActionRunning
                    ? null
                    : () async {
                        final confirm = await showDialog<bool>(
                          context: context,
                          builder: (c) => AlertDialog(
                            backgroundColor: const Color(0xFF1E1E28),
                            title: const Text('Delete Replica Configuration?'),
                            content: const Text('This removes the watcher configuration. The destination playlist on YouTube Music will not be deleted.'),
                            actions: [
                              TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
                              ElevatedButton(
                                onPressed: () => Navigator.pop(c, true),
                                style: ElevatedButton.styleFrom(backgroundColor: Colors.redAccent),
                                child: const Text('Delete'),
                              ),
                            ],
                          ),
                        );
                        if (confirm == true) {
                          await apiService.deleteReplicatedPlaylist(replica.id);
                          await _loadReplicatedPlaylists();
                          if (ctx.mounted) Navigator.pop(ctx);
                          if (mounted) setState(() {});
                        }
                      },
                child: const Text('Delete Config', style: TextStyle(color: Colors.redAccent)),
              ),
              const Spacer(),
              TextButton(
                onPressed: isActionRunning ? null : () => Navigator.pop(ctx),
                child: const Text('Close'),
              ),
              OutlinedButton.icon(
                onPressed: isActionRunning
                    ? null
                    : () async {
                        setModalState(() {
                          isActionRunning = true;
                          actionStatus = 'Running dry-run diff calculation...';
                        });
                        try {
                          final res = await apiService.dryRunReplicatedPlaylist(replica.id);
                          setModalState(() {
                            isActionRunning = false;
                            preview = res;
                          });
                          if (mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text('Dry Run Complete: ${res.actions.length} changes planned.')),
                            );
                          }
                        } catch (e) {
                          setModalState(() => isActionRunning = false);
                          if (mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text('Dry run failed: $e'), backgroundColor: Colors.redAccent),
                            );
                          }
                        }
                      },
                icon: const Icon(Icons.preview, size: 16),
                label: const Text('Dry Run'),
              ),
              ElevatedButton.icon(
                onPressed: isActionRunning
                    ? null
                    : () async {
                        setModalState(() {
                          isActionRunning = true;
                          actionStatus = 'Reconciling destination replica...';
                        });
                        try {
                          final res = await apiService.syncReplicatedPlaylist(replica.id);
                          await _loadReplicatedPlaylists();
                          setModalState(() {
                            isActionRunning = false;
                            preview = res;
                          });
                          if (mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              const SnackBar(content: Text('Locker replica reconciled successfully!'), backgroundColor: Colors.green),
                            );
                          }
                        } catch (e) {
                          setModalState(() => isActionRunning = false);
                          if (mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text('Reconcile failed: $e'), backgroundColor: Colors.redAccent),
                            );
                          }
                        }
                      },
                icon: const Icon(Icons.sync, size: 16),
                label: const Text('Sync Now'),
                style: ElevatedButton.styleFrom(
                  backgroundColor: const Color(0xFF00897B),
                  foregroundColor: Colors.white,
                ),
              ),
            ],
          );
        },
      ),
    );
  }

  Widget _buildReplicaDetailRow(String label, String value, IconData icon) {
    return Row(
      children: [
        Icon(icon, size: 16, color: Colors.grey[400]),
        const SizedBox(width: 8),
        Text(label, style: TextStyle(fontSize: 12, color: Colors.grey[400])),
        const Spacer(),
        Text(value, style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold)),
      ],
    );
  }

  Widget _buildMetricCard(String title, String value, Color color) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 8),
        decoration: BoxDecoration(
          color: const Color(0xFF14141E),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: color.withValues(alpha: 0.2)),
        ),
        child: Column(
          children: [
            Text(value, style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: color)),
            const SizedBox(height: 2),
            Text(title, style: TextStyle(fontSize: 10, color: Colors.grey[400])),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_selectedPlaylist != null) {
      return _buildPlaylistDetailsView();
    }
    return _buildPlaylistsGridView();
  }

  Widget _buildPlaylistsGridView() {
    final filteredPlaylists = _playlists.where((p) {
      if (_searchQuery.isEmpty) return true;
      return p.title.toLowerCase().contains(_searchQuery.toLowerCase()) ||
          p.description.toLowerCase().contains(_searchQuery.toLowerCase());
    }).toList();

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'YouTube Music Playlists',
                      style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Browse your playlists, compare tracks with locker uploads, and sync missing songs via yt-dlp',
                      style: TextStyle(color: Colors.grey[400], fontSize: 13),
                    ),
                  ],
                ),
                Row(
                  children: [
                    if (_replicatedPlaylists.isNotEmpty) ...[
                      OutlinedButton.icon(
                        onPressed: _showAllReplicasDialog,
                        icon: const Icon(Icons.sync_alt, size: 16, color: Colors.tealAccent),
                        label: Text('Locker Replicas (${_replicatedPlaylists.length})', style: const TextStyle(color: Colors.tealAccent)),
                        style: OutlinedButton.styleFrom(
                          side: BorderSide(color: Colors.tealAccent.withValues(alpha: 0.4)),
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        ),
                      ),
                      const SizedBox(width: 8),
                    ],
                    OutlinedButton.icon(
                      onPressed: _showImportPlaylistDialog,
                      icon: const Icon(Icons.link, size: 16),
                      label: const Text('Import Playlist URL'),
                      style: OutlinedButton.styleFrom(
                        side: BorderSide(color: Colors.blueAccent.withValues(alpha: 0.4)),
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                      ),
                    ),
                    const SizedBox(width: 8),
                    IconButton.filledTonal(
                      onPressed: _isLoading ? null : _loadPlaylists,
                      icon: _isLoading
                          ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.refresh),
                      tooltip: 'Refresh Playlists',
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 20),

            // Search Bar
            TextField(
              decoration: InputDecoration(
                hintText: 'Search playlists...',
                prefixIcon: const Icon(Icons.search, size: 20),
                filled: true,
                fillColor: const Color(0xFF181820),
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide.none,
                ),
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              ),
              onChanged: (val) {
                setState(() => _searchQuery = val.trim());
              },
            ),
            const SizedBox(height: 20),

            // Content
            Expanded(
              child: _buildPlaylistsContent(filteredPlaylists),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPlaylistsContent(List<YTMPlaylist> playlists) {
    if (_isLoading) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(color: Color(0xFFFF0000)),
            SizedBox(height: 16),
            Text('Loading YouTube Music playlists...'),
          ],
        ),
      );
    }

    if (_errorMessage != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, size: 48, color: Colors.amberAccent),
            const SizedBox(height: 12),
            Text(_errorMessage!, style: const TextStyle(color: Colors.amberAccent)),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: _loadPlaylists,
              child: const Text('Try Again'),
            ),
          ],
        ),
      );
    }

    if (playlists.isEmpty) {
      return Center(
        child: Text(
          _searchQuery.isEmpty ? 'No playlists found in your account.' : 'No playlists matched "$_searchQuery".',
          style: TextStyle(color: Colors.grey[500]),
        ),
      );
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        final crossAxisCount = (constraints.maxWidth / 220).floor().clamp(2, 6);
        return GridView.builder(
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: crossAxisCount,
            crossAxisSpacing: 16,
            mainAxisSpacing: 16,
            childAspectRatio: 0.78,
          ),
          itemCount: playlists.length,
          itemBuilder: (context, index) {
            final p = playlists[index];
            final isLikedMusic = p.id == 'LM';

            return InkWell(
              onTap: () => _selectPlaylist(p),
              borderRadius: BorderRadius.circular(12),
              child: Container(
                decoration: BoxDecoration(
                  color: const Color(0xFF181820),
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(
                    color: isLikedMusic ? const Color(0xFFFF0000).withValues(alpha: 0.4) : Colors.white.withValues(alpha: 0.05),
                  ),
                ),
                padding: const EdgeInsets.all(12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    // Cover Thumbnail
                    Expanded(
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: Container(
                          width: double.infinity,
                          color: const Color(0xFF22222E),
                          child: isLikedMusic
                              ? Container(
                                  decoration: const BoxDecoration(
                                    gradient: LinearGradient(
                                      colors: [Color(0xFF8A2387), Color(0xFFE94057), Color(0xFFF27121)],
                                      begin: Alignment.topLeft,
                                      end: Alignment.bottomRight,
                                    ),
                                  ),
                                  child: const Center(
                                    child: Icon(Icons.thumb_up, color: Colors.white, size: 40),
                                  ),
                                )
                              : (p.thumbnail != null
                                  ? Image.network(
                                      p.thumbnail!,
                                      fit: BoxFit.cover,
                                      errorBuilder: (_, _, _) => const Center(
                                        child: Icon(Icons.music_note, color: Colors.grey, size: 36),
                                      ),
                                    )
                                  : const Center(
                                      child: Icon(Icons.playlist_play, color: Colors.grey, size: 40),
                                    )),
                        ),
                      ),
                    ),
                    const SizedBox(height: 10),

                    // Title
                    Text(
                      p.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14),
                    ),
                    const SizedBox(height: 4),

                    // Track count or subtitle
                    Row(
                      children: [
                        if (isLikedMusic)
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: const Color(0xFFFF0000).withValues(alpha: 0.2),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: const Text(
                              'Auto Playlist',
                              style: TextStyle(color: Color(0xFFFF4E4E), fontSize: 10, fontWeight: FontWeight.bold),
                            ),
                          )
                        else if (p.trackCount != null)
                          Text(
                            '${p.trackCount} tracks',
                            style: TextStyle(color: Colors.grey[400], fontSize: 12),
                          )
                        else
                          Text(
                            'Playlist',
                            style: TextStyle(color: Colors.grey[500], fontSize: 12),
                          ),
                        if (_replicatedPlaylists.any((r) => r.sourcePlaylistId == p.id)) ...[
                          const SizedBox(width: 6),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: const Color(0xFF00897B).withValues(alpha: 0.2),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: const Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(Icons.sync, size: 10, color: Color(0xFF4DB6AC)),
                                SizedBox(width: 3),
                                Text(
                                  'Replica Active',
                                  style: TextStyle(color: Color(0xFF4DB6AC), fontSize: 10, fontWeight: FontWeight.bold),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ],
                    ),
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }

  Widget _buildPlaylistDetailsView() {
    final playlist = _selectedPlaylist!;
    final details = _playlistDetails;

    // Filter tracks
    List<YTMPlaylistTrack> displayedTracks = [];
    int localCount = 0;
    int uploadsCount = 0;
    int streamingCount = 0;
    int missingFromUploadsCount = 0;

    if (details != null) {
      for (final t in details.tracks) {
        if (t.inLocal) localCount++;
        if (t.inUploads) uploadsCount++;
        if (!t.inUploads) missingFromUploadsCount++;
        if (!t.inLocal && !t.inUploads) streamingCount++;
      }

      displayedTracks = details.tracks.where((t) {
        if (_trackFilter == 'local' && !t.inLocal) return false;
        if (_trackFilter == 'uploads' && !t.inUploads) return false;
        if (_trackFilter == 'missing' && (t.inLocal || t.inUploads)) return false;
        return true;
      }).toList();
    }

    final isSyncRunning = _syncStatus != null && _syncStatus!.isRunning;

    return Scaffold(
      backgroundColor: Colors.transparent,
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Top Bar with Back Button and Sync Actions
            Row(
              children: [
                IconButton.filledTonal(
                  onPressed: () {
                    setState(() {
                      _selectedPlaylist = null;
                      _playlistDetails = null;
                    });
                  },
                  icon: const Icon(Icons.arrow_back),
                  tooltip: 'Back to Playlists',
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        playlist.title,
                        style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      if (playlist.description.isNotEmpty)
                        Text(
                          playlist.description,
                          style: TextStyle(color: Colors.grey[400], fontSize: 12),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                    ],
                  ),
                ),
                if (missingFromUploadsCount > 0) ...[
                  ElevatedButton.icon(
                    onPressed: isSyncRunning ? null : _syncMissingTracks,
                    icon: const Icon(Icons.cloud_sync, size: 16),
                    label: Text(isSyncRunning ? 'Syncing...' : 'Download & Upload Missing ($missingFromUploadsCount)'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF8A2387),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    ),
                  ),
                  const SizedBox(width: 8),
                ],
                if (_currentReplicaConfig != null) ...[
                  ElevatedButton.icon(
                    onPressed: () => _openReplicationModal(_currentReplicaConfig!),
                    icon: const Icon(Icons.sync_alt, size: 16),
                    label: const Text('Locker Replica (Active)'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF00897B),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    ),
                  ),
                  const SizedBox(width: 8),
                ] else ...[
                  ElevatedButton.icon(
                    onPressed: () => _openCreateReplicaDialog(playlist),
                    icon: const Icon(Icons.copy_all, size: 16),
                    label: const Text('Make Locker Replica'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF0288D1),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    ),
                  ),
                  const SizedBox(width: 8),
                ],
                if (!_isLoadingDetails)
                  IconButton.filledTonal(
                    onPressed: () => _selectPlaylist(playlist, refresh: true),
                    icon: const Icon(Icons.refresh, size: 18),
                    tooltip: 'Refresh Playlist & Sync Uploads',
                  ),
              ],
            ),
            const SizedBox(height: 16),

            // Sync Progress Banner (when active)
            if (isSyncRunning) ...[
              Container(
                margin: const EdgeInsets.only(bottom: 16),
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.purple.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: Colors.purpleAccent.withValues(alpha: 0.4)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Row(
                          children: [
                            const SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(strokeWidth: 2, color: Colors.purpleAccent),
                            ),
                            const SizedBox(width: 10),
                            Text(
                              'Downloading & Uploading: ${_syncStatus!.completedTracks}/${_syncStatus!.totalTracks} tracks',
                              style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.purpleAccent),
                            ),
                          ],
                        ),
                        Text(
                          '${(_syncStatus!.progress * 100).toInt()}%',
                          style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
                        ),
                      ],
                    ),
                    if (_syncStatus!.currentTrack != null) ...[
                      const SizedBox(height: 6),
                      Text(
                        'Processing: ${_syncStatus!.currentTrack}',
                        style: TextStyle(fontSize: 12, color: Colors.grey[300]),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ],
                    const SizedBox(height: 8),
                    LinearProgressIndicator(
                      value: _syncStatus!.progress,
                      backgroundColor: Colors.white10,
                      valueColor: const AlwaysStoppedAnimation<Color>(Colors.purpleAccent),
                    ),
                  ],
                ),
              ),
            ],

            // Summary Stats Cards
            if (details != null) ...[
              Wrap(
                spacing: 12,
                runSpacing: 8,
                children: [
                  _buildFilterChip(
                    label: 'All Tracks (${details.tracks.length})',
                    filterKey: 'all',
                    color: Colors.blueAccent,
                  ),
                  _buildFilterChip(
                    label: 'In Local Files ($localCount)',
                    filterKey: 'local',
                    color: Colors.greenAccent,
                  ),
                  _buildFilterChip(
                    label: 'In Cloud Locker ($uploadsCount)',
                    filterKey: 'uploads',
                    color: Colors.purpleAccent,
                  ),
                  _buildFilterChip(
                    label: 'Streaming Only ($streamingCount)',
                    filterKey: 'missing',
                    color: Colors.grey,
                  ),
                ],
              ),
              const SizedBox(height: 16),
            ],

            // Tracks Table
            Expanded(
              child: _buildPlaylistTracksContent(displayedTracks),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildFilterChip({required String label, required String filterKey, required Color color}) {
    final isSelected = _trackFilter == filterKey;
    return FilterChip(
      selected: isSelected,
      label: Text(
        label,
        style: TextStyle(
          color: isSelected ? Colors.white : Colors.grey[300],
          fontSize: 12,
          fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
        ),
      ),
      backgroundColor: const Color(0xFF181820),
      selectedColor: color.withValues(alpha: 0.3),
      side: BorderSide(color: isSelected ? color : Colors.white.withValues(alpha: 0.1)),
      onSelected: (_) {
        setState(() => _trackFilter = filterKey);
      },
    );
  }

  Widget _buildPlaylistTracksContent(List<YTMPlaylistTrack> tracks) {
    if (_isLoadingDetails) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            CircularProgressIndicator(color: Color(0xFFFF0000)),
            SizedBox(height: 16),
            Text('Fetching playlist tracks & comparing with local library...'),
          ],
        ),
      );
    }

    if (_detailsErrorMessage != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, size: 48, color: Colors.amberAccent),
            const SizedBox(height: 12),
            Text(_detailsErrorMessage!, style: const TextStyle(color: Colors.amberAccent)),
            const SizedBox(height: 16),
            ElevatedButton(
              onPressed: () => _selectPlaylist(_selectedPlaylist!),
              child: const Text('Try Again'),
            ),
          ],
        ),
      );
    }

    if (tracks.isEmpty) {
      return Center(
        child: Text(
          _trackFilter == 'all' ? 'This playlist is empty.' : 'No tracks match the selected filter.',
          style: TextStyle(color: Colors.grey[500]),
        ),
      );
    }

    return Card(
      color: const Color(0xFF14141A),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: ListView.separated(
        itemCount: tracks.length,
        separatorBuilder: (_, _) => const Divider(height: 1, color: Color(0xFF22222E)),
        itemBuilder: (context, index) {
          final track = tracks[index];
          final isDownloadingThis = track.videoId != null && _downloadingVideoIds.contains(track.videoId);

          return ListTile(
            leading: ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: SizedBox(
                width: 44,
                height: 44,
                child: track.thumbnail != null
                    ? Image.network(
                        track.thumbnail!,
                        fit: BoxFit.cover,
                        errorBuilder: (_, _, _) => const Icon(Icons.music_note, color: Colors.grey),
                      )
                    : const Icon(Icons.music_note, color: Colors.grey),
              ),
            ),
            title: Text(
              track.title,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
            ),
            subtitle: Text(
              '${track.displayArtist} • ${track.displayAlbum}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(color: Colors.grey[400], fontSize: 12),
            ),
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Local Library Badge
                if (track.inLocal)
                  Tooltip(
                    message: track.localPath ?? 'In Local Music Library',
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                      decoration: BoxDecoration(
                        color: Colors.greenAccent.withValues(alpha: 0.15),
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(color: Colors.greenAccent.withValues(alpha: 0.5)),
                      ),
                      child: const Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(Icons.folder_outlined, size: 12, color: Colors.greenAccent),
                          SizedBox(width: 4),
                          Text('In Local Files', style: TextStyle(color: Colors.greenAccent, fontSize: 11, fontWeight: FontWeight.bold)),
                        ],
                      ),
                    ),
                  )
                else
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: Colors.grey.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text('Missing Local', style: TextStyle(color: Colors.grey[400], fontSize: 11)),
                  ),
                const SizedBox(width: 8),

                // Uploads Badge / Action
                if (track.inUploads)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: Colors.purpleAccent.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: Colors.purpleAccent.withValues(alpha: 0.5)),
                    ),
                    child: const Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.cloud_done_outlined, size: 12, color: Colors.purpleAccent),
                        SizedBox(width: 4),
                        Text('In Uploads', style: TextStyle(color: Colors.purpleAccent, fontSize: 11, fontWeight: FontWeight.bold)),
                      ],
                    ),
                  )
                else if (isDownloadingThis)
                  const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.purpleAccent),
                  )
                else
                  OutlinedButton.icon(
                    onPressed: () => _downloadAndUploadSingleTrack(track),
                    icon: const Icon(Icons.cloud_upload_outlined, size: 13, color: Colors.purpleAccent),
                    label: const Text('Download & Upload', style: TextStyle(fontSize: 11, color: Colors.purpleAccent)),
                    style: OutlinedButton.styleFrom(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      side: BorderSide(color: Colors.purpleAccent.withValues(alpha: 0.5)),
                    ),
                  ),

                const SizedBox(width: 12),
                Text(
                  track.formattedDuration,
                  style: TextStyle(color: Colors.grey[500], fontSize: 12),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}
