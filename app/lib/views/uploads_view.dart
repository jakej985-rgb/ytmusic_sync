import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_service.dart';
import 'components/metadata_editor_dialog.dart';

class UploadsView extends StatefulWidget {
  const UploadsView({super.key});

  @override
  State<UploadsView> createState() => _UploadsViewState();
}

class _UploadsViewState extends State<UploadsView> {
  String _activeFilter = 'missing_metadata'; // 'missing_metadata', 'all', 'proper'
  final TextEditingController _searchController = TextEditingController();

  List<YtmUpload> _uploads = [];
  Map<String, int> _summary = {'total': 0, 'missing_metadata': 0, 'proper': 0};
  int _currentPage = 1;
  int _totalPages = 1;
  int _totalCount = 0;
  final int _pageSize = 30;

  final Set<String> _selectedEntityIds = {};
  bool _isBatchProcessing = false;

  bool _isLoading = false;
  bool _isSyncing = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _loadSummaryAndData();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _loadSummaryAndData({int page = 1}) async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final summaryFuture = apiService.getYtmUploadsSummary();
      final uploadsFuture = apiService.getYtmUploads(
        filterType: _activeFilter,
        search: _searchController.text.trim().isNotEmpty ? _searchController.text.trim() : null,
        page: page,
        pageSize: _pageSize,
      );

      final results = await Future.wait([summaryFuture, uploadsFuture]);
      final summary = results[0] as Map<String, int>;
      final uploadsData = results[1];

      if (mounted) {
        setState(() {
          _summary = summary;
          _uploads = List<YtmUpload>.from(uploadsData['items']);
          _totalCount = uploadsData['total'] as int;
          _currentPage = uploadsData['page'] as int;
          _totalPages = uploadsData['total_pages'] as int;
          _isLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _errorMessage = e.toString().replaceAll('Exception:', '').trim();
          _isLoading = false;
        });
      }
    }
  }

  Future<void> _triggerCloudSync() async {
    setState(() => _isSyncing = true);
    try {
      await apiService.triggerSync();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Syncing uploads from YouTube Music in the background...'),
            backgroundColor: Color(0xFF00B4D8),
          ),
        );
      }
      await Future.delayed(const Duration(seconds: 3));
      await _loadSummaryAndData(page: _currentPage);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to sync: $e'), backgroundColor: Colors.redAccent),
        );
      }
    } finally {
      if (mounted) {
        setState(() => _isSyncing = false);
      }
    }
  }

  Future<void> _openRetagDialog(YtmUpload upload) async {
    final result = await MetadataEditorDialog.showForYtmUpload(context, upload);
    if (result != null) {
      if (result is Map<String, dynamic> && result['saved'] == true) {
        final newTitle = result['title'] as String? ?? upload.title;
        final newArtist = result['artist'] as String?;
        final newAlbum = result['album'] as String?;
        final newCoverUrl = result['coverUrl'] as String? ?? upload.thumbnail;

        final updatedUpload = upload.copyWith(
          title: newTitle,
          artist: newArtist,
          album: newAlbum,
          thumbnail: newCoverUrl,
        );

        final stillMissing = updatedUpload.isMissingMetadata;

        setState(() {
          final index = _uploads.indexWhere((u) => u.entityId == upload.entityId);
          if (stillMissing) {
            // Keep on list, but refresh immediately with changed data!
            if (index != -1) {
              _uploads[index] = updatedUpload;
            }
          } else {
            // Completely clean! Remove from untagged list if currently viewing missing metadata
            if (_activeFilter == 'missing_metadata') {
              _uploads.removeWhere((u) => u.entityId == upload.entityId);
              final currentMissing = _summary['missing_metadata'] ?? 0;
              final currentProper = _summary['proper'] ?? 0;
              _summary = {
                ..._summary,
                'missing_metadata': currentMissing > 0 ? currentMissing - 1 : 0,
                'proper': currentProper + 1,
              };
            } else if (index != -1) {
              _uploads[index] = updatedUpload;
            }
          }
        });
      }
      _loadSummaryAndData(page: _currentPage);
    }
  }

  Future<void> _confirmDelete(YtmUpload upload) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: const Color(0xFF1E1E26),
        title: const Row(
          children: [
            Icon(Icons.warning_amber_rounded, color: Colors.redAccent),
            SizedBox(width: 10),
            Text('Delete from YouTube Music?'),
          ],
        ),
        content: Text(
          'Are you sure you want to delete "${upload.displayTitle}" from your YouTube Music cloud uploads? This action cannot be undone.',
          style: const TextStyle(fontSize: 13, color: Colors.white70),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.redAccent),
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );

    if (confirmed == true) {
      try {
        final ok = await apiService.deleteYtmUpload(upload.entityId);
        if (ok && mounted) {
          setState(() {
            _uploads.removeWhere((u) => u.entityId == upload.entityId);
            final currentTotal = _summary['total'] ?? 0;
            final currentMissing = _summary['missing_metadata'] ?? 0;
            _summary = {
              ..._summary,
              'total': currentTotal > 0 ? currentTotal - 1 : 0,
              'missing_metadata': currentMissing > 0 ? currentMissing - 1 : 0,
            };
          });
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Deleted "${upload.displayTitle}" from YouTube Music.'), backgroundColor: Colors.green),
          );
          _loadSummaryAndData(page: _currentPage);
        }
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Error deleting upload: $e'), backgroundColor: Colors.redAccent),
          );
        }
      }
    }
  }

  void _toggleSelectAllPage() {
    setState(() {
      final pageIds = _uploads.map((u) => u.entityId).toSet();
      if (_selectedEntityIds.containsAll(pageIds)) {
        _selectedEntityIds.removeAll(pageIds);
      } else {
        _selectedEntityIds.addAll(pageIds);
      }
    });
  }

  void _toggleSelectItem(String entityId) {
    setState(() {
      if (_selectedEntityIds.contains(entityId)) {
        _selectedEntityIds.remove(entityId);
      } else {
        _selectedEntityIds.add(entityId);
      }
    });
  }

  Future<void> _batchDeleteSelected() async {
    if (_selectedEntityIds.isEmpty) return;
    final count = _selectedEntityIds.length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF181822),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        title: Row(
          children: [
            const Icon(Icons.delete_forever, color: Colors.redAccent, size: 22),
            const SizedBox(width: 8),
            Text('Delete $count Upload${count > 1 ? 's' : ''}?'),
          ],
        ),
        content: Text(
          'Are you sure you want to permanently delete $count selected upload${count > 1 ? 's' : ''} from YouTube Music? This action cannot be undone.',
          style: const TextStyle(color: Colors.white70, fontSize: 13),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel', style: TextStyle(color: Colors.white60)),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(backgroundColor: Colors.redAccent),
            child: const Text('Delete All'),
          ),
        ],
      ),
    );

    if (confirmed == true) {
      setState(() => _isBatchProcessing = true);
      try {
        final toDelete = _selectedEntityIds.toList();
        final res = await apiService.batchDeleteYtmUploads(toDelete);
        final deletedCount = res['deleted'] ?? toDelete.length;
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text('Successfully deleted $deletedCount upload${deletedCount > 1 ? 's' : ''} from YouTube Music.'),
              backgroundColor: Colors.green,
            ),
          );
        }
        _selectedEntityIds.clear();
        await _loadSummaryAndData(page: _currentPage);
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Failed to batch delete: $e'), backgroundColor: Colors.redAccent),
          );
        }
      } finally {
        if (mounted) {
          setState(() => _isBatchProcessing = false);
        }
      }
    }
  }

  Future<void> _batchAutoUploadSelected() async {
    if (_selectedEntityIds.isEmpty) return;
    final selectedUploads = _uploads.where((u) => _selectedEntityIds.contains(u.entityId)).toList();
    if (selectedUploads.isEmpty) return;

    final count = selectedUploads.length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF181822),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        title: Row(
          children: [
            const Icon(Icons.cloud_upload, color: Color(0xFF00B4D8), size: 22),
            const SizedBox(width: 8),
            Text('Auto-Tag & Upload $count Song${count > 1 ? 's' : ''}?'),
          ],
        ),
        content: Text(
          'This will automatically clean up titles, fetch high-res artwork, and replace/upload the $count selected song${count > 1 ? 's' : ''} to YouTube Music in sequence.',
          style: const TextStyle(color: Colors.white70, fontSize: 13),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('Cancel', style: TextStyle(color: Colors.white60)),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(backgroundColor: const Color(0xFF00B4D8)),
            child: const Text('Start Upload'),
          ),
        ],
      ),
    );

    if (confirmed == true) {
      setState(() => _isBatchProcessing = true);
      int successful = 0;
      int failed = 0;
      for (final upload in selectedUploads) {
        try {
          String cleanTitle = upload.title.replaceAll(RegExp(r'\.[a-zA-Z0-9]+$'), '').trim();
          String cleanArtist = upload.artist ?? '';
          if (cleanTitle.contains(' - ') && cleanArtist.isEmpty) {
            final parts = cleanTitle.split(' - ');
            cleanArtist = parts[0].trim();
            cleanTitle = parts[1].trim();
          }
          final res = await apiService.replaceYtmUpload(
            upload.entityId,
            title: cleanTitle,
            artist: cleanArtist.isNotEmpty ? cleanArtist : null,
            album: upload.album,
            coverUrl: upload.thumbnail,
          );
          if (res['success'] == true) {
            successful++;
          } else {
            failed++;
          }
        } catch (_) {
          failed++;
        }
      }
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Batch upload complete: $successful processed, $failed failed.'),
            backgroundColor: successful > 0 ? Colors.green : Colors.redAccent,
          ),
        );
      }
      _selectedEntityIds.clear();
      await _loadSummaryAndData(page: _currentPage);
      if (mounted) {
        setState(() => _isBatchProcessing = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final missingCount = _summary['missing_metadata'] ?? 0;
    final duplicatesCount = _summary['duplicates'] ?? 0;
    final skitsCount = _summary['skits'] ?? 0;
    final totalCount = _summary['total'] ?? 0;
    final properCount = _summary['proper'] ?? 0;

    return Scaffold(
      backgroundColor: const Color(0xFF0E0E12),
      body: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Top Header
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Icon(Icons.cloud_done, color: Color(0xFFFF0000), size: 28),
                        const SizedBox(width: 10),
                        const Text(
                          'YTM Cloud Uploads',
                          style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold, color: Colors.white),
                        ),
                        if (missingCount > 0) ...[
                          const SizedBox(width: 12),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                            decoration: BoxDecoration(
                              color: Colors.amber.withValues(alpha: 0.2),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(color: Colors.amber.withValues(alpha: 0.5)),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                const Icon(Icons.warning_amber_rounded, size: 14, color: Colors.amber),
                                const SizedBox(width: 4),
                                Text(
                                  '$missingCount Untagged',
                                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.amber),
                                ),
                              ],
                            ),
                          ),
                        ],
                        if (duplicatesCount > 0) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                            decoration: BoxDecoration(
                              color: Colors.deepOrangeAccent.withValues(alpha: 0.2),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(color: Colors.deepOrangeAccent.withValues(alpha: 0.5)),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                const Icon(Icons.copy_outlined, size: 14, color: Colors.deepOrangeAccent),
                                const SizedBox(width: 4),
                                Text(
                                  '$duplicatesCount Duplicates',
                                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.deepOrangeAccent),
                                ),
                              ],
                            ),
                          ),
                        ],
                        if (skitsCount > 0) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                            decoration: BoxDecoration(
                              color: Colors.purpleAccent.withValues(alpha: 0.2),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(color: Colors.purpleAccent.withValues(alpha: 0.5)),
                            ),
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                const Icon(Icons.timer_outlined, size: 14, color: Colors.purpleAccent),
                                const SizedBox(width: 4),
                                Text(
                                  '$skitsCount Skits (<1m)',
                                  style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.purpleAccent),
                                ),
                              ],
                            ),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'Audit metadata health, find duplicate songs, clean up skits (<1m), or retag & replace uploaded songs.',
                      style: TextStyle(fontSize: 13, color: Colors.white54),
                    ),
                  ],
                ),
                Row(
                  children: [
                    OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.white70,
                        side: const BorderSide(color: Colors.white24),
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                      ),
                      onPressed: _isLoading ? null : () => _loadSummaryAndData(page: _currentPage),
                      icon: const Icon(Icons.refresh, size: 16),
                      label: const Text('Refresh'),
                    ),
                    const SizedBox(width: 10),
                    FilledButton.icon(
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFF00B4D8),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                      ),
                      onPressed: _isSyncing ? null : _triggerCloudSync,
                      icon: _isSyncing
                          ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                          : const Icon(Icons.cloud_sync, size: 16),
                      label: Text(_isSyncing ? 'Syncing...' : 'Sync From YTM'),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 18),

            // Filter Tabs & Search Bar
            Row(
              children: [
                // Filter Tabs
                Container(
                  padding: const EdgeInsets.all(4),
                  decoration: BoxDecoration(
                    color: const Color(0xFF181820),
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: Colors.white10),
                  ),
                  child: Row(
                    children: [
                      _buildFilterChip('missing_metadata', 'Missing Metadata', missingCount, Colors.amber),
                      _buildFilterChip('duplicates', 'Duplicates', duplicatesCount, Colors.deepOrangeAccent),
                      _buildFilterChip('skits', 'Short / Skits (< 1m)', skitsCount, Colors.purpleAccent),
                      _buildFilterChip('proper', 'Properly Tagged', properCount, Colors.greenAccent),
                      _buildFilterChip('all', 'All Uploads', totalCount, const Color(0xFF3EA6FF)),
                    ],
                  ),
                ),
                const SizedBox(width: 16),

                // Search Input
                Expanded(
                  child: TextField(
                    controller: _searchController,
                    onSubmitted: (_) => _loadSummaryAndData(page: 1),
                    decoration: InputDecoration(
                      hintText: 'Search uploads by title, artist, or album...',
                      hintStyle: const TextStyle(fontSize: 13, color: Colors.white38),
                      prefixIcon: const Icon(Icons.search, size: 18, color: Colors.white54),
                      suffixIcon: _searchController.text.isNotEmpty
                          ? IconButton(
                              icon: const Icon(Icons.clear, size: 16, color: Colors.white54),
                              onPressed: () {
                                _searchController.clear();
                                _loadSummaryAndData(page: 1);
                              },
                            )
                          : null,
                      isDense: true,
                      filled: true,
                      fillColor: const Color(0xFF181820),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(color: Colors.white10),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(10),
                        borderSide: const BorderSide(color: Colors.white10),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.white70,
                    side: const BorderSide(color: Colors.white24),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                  ),
                  onPressed: _toggleSelectAllPage,
                  icon: Icon(
                    _uploads.isNotEmpty && _selectedEntityIds.containsAll(_uploads.map((u) => u.entityId))
                        ? Icons.deselect
                        : Icons.select_all,
                    size: 16,
                  ),
                  label: Text(
                    _uploads.isNotEmpty && _selectedEntityIds.containsAll(_uploads.map((u) => u.entityId))
                        ? 'Deselect Page'
                        : 'Select Page',
                    style: const TextStyle(fontSize: 12),
                  ),
                ),
              ],
            ),
            if (_selectedEntityIds.isNotEmpty) ...[
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                decoration: BoxDecoration(
                  color: const Color(0xFF1B1B28),
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: const Color(0xFF00B4D8).withValues(alpha: 0.4)),
                  boxShadow: [
                    BoxShadow(
                      color: Colors.black.withValues(alpha: 0.3),
                      blurRadius: 10,
                      offset: const Offset(0, 4),
                    ),
                  ],
                ),
                child: Row(
                  children: [
                    Checkbox(
                      value: _uploads.isNotEmpty && _selectedEntityIds.containsAll(_uploads.map((u) => u.entityId)),
                      activeColor: const Color(0xFF00B4D8),
                      checkColor: Colors.black,
                      side: const BorderSide(color: Colors.white38),
                      onChanged: (_) => _toggleSelectAllPage(),
                    ),
                    Text(
                      '${_selectedEntityIds.length} selected',
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.white),
                    ),
                    const SizedBox(width: 12),
                    TextButton(
                      onPressed: () => setState(() => _selectedEntityIds.clear()),
                      child: const Text('Deselect All', style: TextStyle(color: Colors.white60, fontSize: 12)),
                    ),
                    const Spacer(),
                    if (_isBatchProcessing)
                      const Row(
                        children: [
                          SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF00B4D8)),
                          ),
                          SizedBox(width: 10),
                          Text('Processing batch...', style: TextStyle(fontSize: 12, color: Colors.white70)),
                        ],
                      )
                    else ...[
                      FilledButton.icon(
                        style: FilledButton.styleFrom(
                          backgroundColor: const Color(0xFF00B4D8),
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        ),
                        onPressed: _batchAutoUploadSelected,
                        icon: const Icon(Icons.cloud_upload, size: 16),
                        label: Text('Upload Selected (${_selectedEntityIds.length})'),
                      ),
                      const SizedBox(width: 10),
                      FilledButton.icon(
                        style: FilledButton.styleFrom(
                          backgroundColor: Colors.redAccent,
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        ),
                        onPressed: _batchDeleteSelected,
                        icon: const Icon(Icons.delete_forever, size: 16),
                        label: Text('Delete Selected (${_selectedEntityIds.length})'),
                      ),
                    ],
                  ],
                ),
              ),
            ],
            const SizedBox(height: 16),

            // Content Area
            Expanded(
              child: _isLoading
                  ? const Center(child: CircularProgressIndicator(color: Color(0xFFFF0000)))
                  : _errorMessage != null
                      ? Center(
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              const Icon(Icons.error_outline, color: Colors.redAccent, size: 36),
                              const SizedBox(height: 10),
                              Text(_errorMessage!, style: const TextStyle(color: Colors.redAccent)),
                              const SizedBox(height: 14),
                              FilledButton(
                                onPressed: () => _loadSummaryAndData(page: _currentPage),
                                child: const Text('Retry'),
                              ),
                            ],
                          ),
                        )
                      : _uploads.isEmpty
                          ? _buildEmptyState()
                          : _buildUploadsList(),
            ),

            // Pagination Controls
            if (_totalPages > 1) ...[
              const SizedBox(height: 12),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(
                    'Showing ${_uploads.length} of $_totalCount uploads',
                    style: const TextStyle(fontSize: 12, color: Colors.white54),
                  ),
                  Row(
                    children: [
                      IconButton(
                        icon: const Icon(Icons.chevron_left),
                        onPressed: _currentPage > 1 ? () => _loadSummaryAndData(page: _currentPage - 1) : null,
                      ),
                      Text(
                        'Page $_currentPage of $_totalPages',
                        style: const TextStyle(fontSize: 13, fontWeight: FontWeight.bold),
                      ),
                      IconButton(
                        icon: const Icon(Icons.chevron_right),
                        onPressed: _currentPage < _totalPages ? () => _loadSummaryAndData(page: _currentPage + 1) : null,
                      ),
                    ],
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildFilterChip(String key, String label, int count, Color highlightColor) {
    final isSelected = _activeFilter == key;
    return GestureDetector(
      onTap: () {
        if (_activeFilter != key) {
          setState(() => _activeFilter = key);
          _loadSummaryAndData(page: 1);
        }
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        decoration: BoxDecoration(
          color: isSelected ? const Color(0xFF22222E) : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              label,
              style: TextStyle(
                fontSize: 12,
                fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                color: isSelected ? Colors.white : Colors.white60,
              ),
            ),
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: isSelected ? highlightColor.withValues(alpha: 0.2) : Colors.white10,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                count.toString(),
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.bold,
                  color: isSelected ? highlightColor : Colors.white54,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildEmptyState() {
    final isMissingTab = _activeFilter == 'missing_metadata';
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            isMissingTab ? Icons.verified : Icons.cloud_off,
            size: 48,
            color: isMissingTab ? Colors.greenAccent : Colors.white38,
          ),
          const SizedBox(height: 14),
          Text(
            isMissingTab ? '🎉 All Uploads Have Clean Metadata!' : 'No uploads found',
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Colors.white),
          ),
          const SizedBox(height: 6),
          Text(
            isMissingTab
                ? 'Every song in your YouTube Music library has proper artist and album tags.'
                : 'Try adjusting your search query or sync uploads from YouTube Music.',
            style: const TextStyle(fontSize: 13, color: Colors.white54),
          ),
        ],
      ),
    );
  }

  Widget _buildUploadsList() {
    return ListView.separated(
      itemCount: _uploads.length,
      separatorBuilder: (_, _) => const SizedBox(height: 8),
      itemBuilder: (context, index) {
        final upload = _uploads[index];
        final isSelected = _selectedEntityIds.contains(upload.entityId);
        final isUntagged = upload.isMissingMetadata;
        final isRawFilename = upload.hasFileExt;
        final hasNoArtist = upload.hasNoArtist;
        final hasNoAlbum = upload.hasNoAlbum;
        final hasNoArtwork = upload.hasNoArtwork;

        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
          decoration: BoxDecoration(
            color: isSelected ? const Color(0xFF181F2C) : const Color(0xFF14141C),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(
              color: isSelected
                  ? const Color(0xFF00B4D8)
                  : (isUntagged ? Colors.amber.withValues(alpha: 0.3) : Colors.white10),
              width: isSelected ? 1.5 : 1.0,
            ),
          ),
          child: Row(
            children: [
              // Checkbox
              Checkbox(
                value: isSelected,
                activeColor: const Color(0xFF00B4D8),
                checkColor: Colors.black,
                side: const BorderSide(color: Colors.white38),
                onChanged: (_) => _toggleSelectItem(upload.entityId),
              ),
              const SizedBox(width: 6),

              // Icon or Cover Thumbnail
              ClipRRect(
                borderRadius: BorderRadius.circular(6),
                child: (upload.thumbnail != null && upload.thumbnail!.isNotEmpty)
                    ? Image.network(
                        upload.thumbnail!,
                        width: 44,
                        height: 44,
                        fit: BoxFit.cover,
                        errorBuilder: (context, error, stackTrace) => Container(
                          width: 44,
                          height: 44,
                          decoration: BoxDecoration(
                            color: isUntagged ? Colors.amber.withValues(alpha: 0.1) : const Color(0xFF22222E),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Center(
                            child: Icon(
                              isUntagged ? Icons.warning_amber_rounded : Icons.music_note,
                              color: isUntagged ? Colors.amber : const Color(0xFF00B4D8),
                              size: 22,
                            ),
                          ),
                        ),
                      )
                    : Container(
                        width: 44,
                        height: 44,
                        decoration: BoxDecoration(
                          color: isUntagged ? Colors.amber.withValues(alpha: 0.1) : const Color(0xFF22222E),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: Center(
                          child: Icon(
                            isUntagged ? Icons.warning_amber_rounded : Icons.music_note,
                            color: isUntagged ? Colors.amber : const Color(0xFF00B4D8),
                            size: 22,
                          ),
                        ),
                      ),
              ),
              const SizedBox(width: 14),

              // Title and Badges
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            upload.displayTitle,
                            style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600, color: Colors.white),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        if (isRawFilename) ...[
                          const SizedBox(width: 6),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                            decoration: BoxDecoration(
                              color: Colors.amber.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: Colors.amber.withValues(alpha: 0.3)),
                            ),
                            child: const Text('FILE EXT', style: TextStyle(fontSize: 10, color: Colors.amber, fontWeight: FontWeight.bold)),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        // Artist Tag
                        if (hasNoArtist) ...[
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                            decoration: BoxDecoration(
                              color: Colors.redAccent.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: const Text('Missing Artist', style: TextStyle(fontSize: 11, color: Colors.redAccent)),
                          ),
                        ] else ...[
                          Flexible(
                            child: Text(
                              upload.displayArtist,
                              style: const TextStyle(fontSize: 12, color: Colors.white70),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ],

                        // Missing Artwork Tag
                        if (hasNoArtwork) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                            decoration: BoxDecoration(
                              color: Colors.amber.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: Colors.amber.withValues(alpha: 0.3)),
                            ),
                            child: const Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(Icons.image_not_supported_outlined, size: 10, color: Colors.amber),
                                SizedBox(width: 4),
                                Text('Missing Artwork', style: TextStyle(fontSize: 10, color: Colors.amber, fontWeight: FontWeight.w600)),
                              ],
                            ),
                          ),
                        ],

                        // Album Tag
                        if (hasNoAlbum) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                            decoration: BoxDecoration(
                              color: Colors.deepPurpleAccent.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: Colors.deepPurpleAccent.withValues(alpha: 0.3)),
                            ),
                            child: const Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(Icons.album_outlined, size: 10, color: Colors.deepPurpleAccent),
                                SizedBox(width: 4),
                                Text('Missing Album', style: TextStyle(fontSize: 10, color: Colors.deepPurpleAccent, fontWeight: FontWeight.w600)),
                              ],
                            ),
                          ),
                        ] else ...[
                          const SizedBox(width: 8),
                          const Text('•', style: TextStyle(color: Colors.white30, fontSize: 12)),
                          const SizedBox(width: 8),
                          Flexible(
                            child: Text(
                              upload.album!,
                              style: TextStyle(fontSize: 12, color: Colors.grey[400]),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ],

                        // Duration
                        if (upload.duration != null && upload.duration! > 0) ...[
                          const SizedBox(width: 8),
                          const Text('•', style: TextStyle(color: Colors.white30, fontSize: 12)),
                          const SizedBox(width: 8),
                          Text(upload.formattedDuration, style: TextStyle(fontSize: 12, color: Colors.grey[500])),
                        ],

                        // Skit / Short Tag
                        if (upload.isSkitOrShort) ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                            decoration: BoxDecoration(
                              color: Colors.purpleAccent.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: Colors.purpleAccent.withValues(alpha: 0.3)),
                            ),
                            child: const Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(Icons.timer_outlined, size: 10, color: Colors.purpleAccent),
                                SizedBox(width: 4),
                                Text('Skit / Short (<1m)', style: TextStyle(fontSize: 10, color: Colors.purpleAccent, fontWeight: FontWeight.w600)),
                              ],
                            ),
                          ),
                        ],

                        // Duplicate Match Tag
                        if (_activeFilter == 'duplicates') ...[
                          const SizedBox(width: 8),
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                            decoration: BoxDecoration(
                              color: Colors.deepOrangeAccent.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: Colors.deepOrangeAccent.withValues(alpha: 0.3)),
                            ),
                            child: const Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Icon(Icons.copy_outlined, size: 10, color: Colors.deepOrangeAccent),
                                SizedBox(width: 4),
                                Text('Duplicate Match', style: TextStyle(fontSize: 10, color: Colors.deepOrangeAccent, fontWeight: FontWeight.w600)),
                              ],
                            ),
                          ),
                        ],
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 14),

              // Action Buttons
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  FilledButton.icon(
                    style: FilledButton.styleFrom(
                      backgroundColor: isUntagged ? const Color(0xFFFF0000) : const Color(0xFF252535),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    ),
                    onPressed: () => _openRetagDialog(upload),
                    icon: const Icon(Icons.auto_fix_high, size: 14),
                    label: Text(
                      isUntagged ? 'Retag & Replace' : 'Edit Tags',
                      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton(
                    tooltip: 'Delete from YouTube Music',
                    icon: const Icon(Icons.delete_outline, size: 18, color: Colors.white38),
                    hoverColor: Colors.redAccent.withValues(alpha: 0.1),
                    onPressed: () => _confirmDelete(upload),
                  ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }
}
