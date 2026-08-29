import 'dart:async';
import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_service.dart';

class QueueView extends StatefulWidget {
  const QueueView({super.key});

  @override
  State<QueueView> createState() => _QueueViewState();
}

class _QueueViewState extends State<QueueView> {
  String _activeCategory = 'all'; // all, metadata_change, download, upload, local_upload
  Map<String, int> _summary = {
    'all': 0,
    'metadata_change': 0,
    'download': 0,
    'upload': 0,
    'local_upload': 0,
    'active': 0,
  };
  List<UnifiedQueueItem> _items = [];
  bool _isActive = false;
  String _activeDescription = '';
  bool _isLoading = true;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _loadQueue();
    // Poll every 2.5 seconds so user sees live download/upload/metadata progress
    _refreshTimer = Timer.periodic(const Duration(milliseconds: 2500), (_) => _loadQueue(silent: true));
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  Future<void> _loadQueue({bool silent = false}) async {
    if (!silent) setState(() => _isLoading = true);
    try {
      final res = await apiService.getUnifiedQueue(
        category: _activeCategory,
        limit: 250,
      );
      if (mounted) {
        setState(() {
          _summary = res.summary;
          _items = res.items;
          _isActive = res.isActive;
          _activeDescription = res.activeDescription;
          _isLoading = false;
        });
      }
    } catch (_) {
      if (mounted && !silent) setState(() => _isLoading = false);
    }
  }

  Future<void> _enqueueMissing() async {
    try {
      final count = await apiService.uploadAllMissing();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Enqueued $count missing local tracks')),
        );
        _loadQueue();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Future<void> _cancelAll() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Cancel Active Tasks?'),
        content: const Text('This will cancel any active playlist sync or download and clear queued upload jobs.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Back')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Colors.redAccent),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Cancel Tasks'),
          ),
        ],
      ),
    );

    if (confirm == true) {
      try {
        await apiService.cancelAllQueue();
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Active tasks cancelled')),
          );
          _loadQueue();
        }
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Failed to cancel: $e'), backgroundColor: Colors.redAccent),
          );
        }
      }
    }
  }

  Future<void> _clearFinished() async {
    try {
      await apiService.clearCompletedQueue();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Cleared finished task history')),
        );
        _loadQueue();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Error: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Color _getCategoryColor(String category) {
    switch (category) {
      case 'metadata_change':
        return Colors.orangeAccent;
      case 'download':
        return Colors.cyanAccent;
      case 'local_upload':
        return Colors.greenAccent;
      case 'upload':
      default:
        return Colors.blueAccent;
    }
  }

  IconData _getCategoryIcon(String category) {
    switch (category) {
      case 'metadata_change':
        return Icons.edit_note_rounded;
      case 'download':
        return Icons.download_rounded;
      case 'local_upload':
        return Icons.drive_folder_upload_rounded;
      case 'upload':
      default:
        return Icons.cloud_upload_rounded;
    }
  }

  String _getCategoryLabel(String key) {
    switch (key) {
      case 'metadata_change':
        return 'Metadata Change';
      case 'download':
        return 'Download';
      case 'upload':
        return 'Upload';
      case 'local_upload':
        return 'Local Upload';
      case 'all':
      default:
        return 'All';
    }
  }

  Widget _buildCategoryChip(String key) {
    final isSelected = _activeCategory == key;
    final count = _summary[key] ?? 0;
    final color = _getCategoryColor(key);

    return Padding(
      padding: const EdgeInsets.only(right: 8.0),
      child: FilterChip(
        selected: isSelected,
        showCheckmark: false,
        avatar: isSelected
            ? null
            : Icon(_getCategoryIcon(key), size: 16, color: color),
        label: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              _getCategoryLabel(key),
              style: TextStyle(
                fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                color: isSelected ? Colors.white : Colors.grey[300],
              ),
            ),
            const SizedBox(width: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: isSelected ? Colors.white24 : Colors.black26,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(
                '$count',
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.bold,
                  color: isSelected ? Colors.white : Colors.grey[400],
                ),
              ),
            ),
          ],
        ),
        backgroundColor: const Color(0xFF1E1E26),
        selectedColor: color.withValues(alpha: 0.35),
        side: BorderSide(
          color: isSelected ? color : Colors.white12,
          width: isSelected ? 1.5 : 1,
        ),
        onSelected: (_) {
          if (_activeCategory != key) {
            setState(() => _activeCategory = key);
            _loadQueue();
          }
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final totalCount = _summary[_activeCategory] ?? _items.length;

    return Padding(
      padding: const EdgeInsets.all(28.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header Row
          Row(
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Queue',
                    style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold, letterSpacing: -0.5),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    _activeDescription.isNotEmpty
                        ? '$totalCount items • $_activeDescription'
                        : '$totalCount items currently in pipeline',
                    style: TextStyle(color: Colors.grey[400], fontSize: 13),
                  ),
                ],
              ),
              const Spacer(),
              if (_isActive) ...[
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.redAccent,
                    side: const BorderSide(color: Colors.redAccent),
                  ),
                  onPressed: _cancelAll,
                  icon: const Icon(Icons.stop_circle_outlined, size: 18),
                  label: const Text('Cancel Active'),
                ),
                const SizedBox(width: 8),
              ],
              FilledButton.tonalIcon(
                onPressed: _clearFinished,
                icon: const Icon(Icons.cleaning_services_outlined, size: 18),
                label: const Text('Clear Finished'),
              ),
              const SizedBox(width: 8),
              FilledButton.tonalIcon(
                onPressed: _enqueueMissing,
                icon: const Icon(Icons.playlist_add, size: 18),
                label: const Text('Add All Missing'),
              ),
              const SizedBox(width: 8),
              IconButton(
                tooltip: 'Refresh Queue',
                icon: const Icon(Icons.refresh),
                onPressed: () => _loadQueue(),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // Active Activity Live Banner
          if (_isActive && _activeDescription.isNotEmpty)
            Container(
              margin: const EdgeInsets.only(bottom: 16),
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  colors: [
                    Colors.deepPurple.withValues(alpha: 0.3),
                    Colors.blue.withValues(alpha: 0.2),
                  ],
                ),
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: Colors.deepPurpleAccent.withValues(alpha: 0.4)),
              ),
              child: Row(
                children: [
                  const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2.5),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Text(
                      _activeDescription,
                      style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
                    ),
                  ),
                ],
              ),
            ),

          // Category Tabs Row: All, Metadata Change, Download, Upload, Local Upload
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _buildCategoryChip('all'),
                _buildCategoryChip('metadata_change'),
                _buildCategoryChip('download'),
                _buildCategoryChip('upload'),
                _buildCategoryChip('local_upload'),
              ],
            ),
          ),
          const SizedBox(height: 18),

          // Queue Items List
          Expanded(
            child: _isLoading && _items.isEmpty
                ? const Center(child: CircularProgressIndicator())
                : _items.isEmpty
                    ? Center(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(
                              _getCategoryIcon(_activeCategory),
                              size: 56,
                              color: Colors.grey[700],
                            ),
                            const SizedBox(height: 12),
                            Text(
                              'Queue is empty',
                              style: TextStyle(color: Colors.grey[300], fontSize: 17, fontWeight: FontWeight.w600),
                            ),
                            const SizedBox(height: 6),
                            Text(
                              _activeCategory == 'all'
                                  ? 'No active, queued, or recently completed tasks.'
                                  : 'No items in the ${_getCategoryLabel(_activeCategory)} category.',
                              style: TextStyle(color: Colors.grey[500], fontSize: 13),
                            ),
                          ],
                        ),
                      )
                    : ListView.separated(
                        itemCount: _items.length,
                        separatorBuilder: (context, index) => const SizedBox(height: 8),
                        itemBuilder: (context, index) {
                          final item = _items[index];
                          final isInProgress = item.status == 'in_progress';
                          final isCompleted = item.status == 'completed';
                          final isFailed = item.status == 'failed';
                          final catColor = _getCategoryColor(item.category);

                          return Container(
                            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                            decoration: BoxDecoration(
                              color: const Color(0xFF181820),
                              borderRadius: BorderRadius.circular(10),
                              border: Border.all(
                                color: isInProgress
                                    ? catColor.withValues(alpha: 0.5)
                                    : isFailed
                                        ? Colors.redAccent.withValues(alpha: 0.4)
                                        : Colors.white10,
                                width: isInProgress ? 1.5 : 1,
                              ),
                            ),
                            child: Row(
                              children: [
                                // Item index
                                SizedBox(
                                  width: 32,
                                  child: Text(
                                    '#${index + 1}',
                                    style: TextStyle(
                                      fontWeight: FontWeight.bold,
                                      color: Colors.grey[500],
                                      fontSize: 12,
                                    ),
                                  ),
                                ),

                                // Thumbnail or category icon
                                Container(
                                  width: 44,
                                  height: 44,
                                  decoration: BoxDecoration(
                                    color: const Color(0xFF22222E),
                                    borderRadius: BorderRadius.circular(6),
                                  ),
                                  clipBehavior: Clip.antiAlias,
                                  child: item.thumbnail != null && item.thumbnail!.isNotEmpty
                                      ? Image.network(
                                          item.thumbnail!,
                                          fit: BoxFit.cover,
                                          errorBuilder: (context, error, stackTrace) => Icon(
                                            _getCategoryIcon(item.category),
                                            color: catColor,
                                            size: 22,
                                          ),
                                        )
                                      : Icon(
                                          _getCategoryIcon(item.category),
                                          color: catColor,
                                          size: 22,
                                        ),
                                ),
                                const SizedBox(width: 14),

                                // Title, Artist & Album, Step, Source
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Row(
                                        children: [
                                          Expanded(
                                            child: Text(
                                              item.title,
                                              maxLines: 1,
                                              overflow: TextOverflow.ellipsis,
                                              style: const TextStyle(
                                                fontWeight: FontWeight.w600,
                                                fontSize: 14,
                                              ),
                                            ),
                                          ),
                                        ],
                                      ),
                                      const SizedBox(height: 2),
                                      Row(
                                        children: [
                                          if (item.artist != null && item.artist!.isNotEmpty) ...[
                                            Flexible(
                                              child: Text(
                                                item.artist!,
                                                maxLines: 1,
                                                overflow: TextOverflow.ellipsis,
                                                style: TextStyle(color: Colors.grey[400], fontSize: 12),
                                              ),
                                            ),
                                          ],
                                          if (item.album != null && item.album!.isNotEmpty) ...[
                                            Text(
                                              ' • ${item.album!}',
                                              maxLines: 1,
                                              overflow: TextOverflow.ellipsis,
                                              style: TextStyle(color: Colors.grey[500], fontSize: 12),
                                            ),
                                          ],
                                        ],
                                      ),
                                      if (item.currentStep != null && item.currentStep!.isNotEmpty) ...[
                                        const SizedBox(height: 3),
                                        Text(
                                          item.currentStep!,
                                          maxLines: 1,
                                          overflow: TextOverflow.ellipsis,
                                          style: TextStyle(
                                            color: isInProgress
                                                ? catColor
                                                : isFailed
                                                    ? Colors.redAccent
                                                    : Colors.grey[500],
                                            fontSize: 11,
                                            fontWeight: isInProgress ? FontWeight.w500 : FontWeight.normal,
                                          ),
                                        ),
                                      ],
                                    ],
                                  ),
                                ),
                                const SizedBox(width: 12),

                                // Source badge
                                if (item.source != null && item.source!.isNotEmpty) ...[
                                  Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                                    decoration: BoxDecoration(
                                      color: Colors.white.withValues(alpha: 0.05),
                                      borderRadius: BorderRadius.circular(6),
                                    ),
                                    child: Text(
                                      item.source!,
                                      style: TextStyle(color: Colors.grey[400], fontSize: 11),
                                    ),
                                  ),
                                  const SizedBox(width: 12),
                                ],

                                // Status Badge
                                Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                                  decoration: BoxDecoration(
                                    color: isInProgress
                                        ? catColor.withValues(alpha: 0.18)
                                        : isCompleted
                                            ? Colors.green.withValues(alpha: 0.15)
                                            : isFailed
                                                ? Colors.red.withValues(alpha: 0.18)
                                                : Colors.amber.withValues(alpha: 0.12),
                                    borderRadius: BorderRadius.circular(6),
                                  ),
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      if (isInProgress) ...[
                                        SizedBox(
                                          width: 12,
                                          height: 12,
                                          child: CircularProgressIndicator(
                                            strokeWidth: 2,
                                            color: catColor,
                                          ),
                                        ),
                                        const SizedBox(width: 8),
                                      ] else if (isCompleted) ...[
                                        const Icon(Icons.check_circle_outline, size: 14, color: Colors.greenAccent),
                                        const SizedBox(width: 6),
                                      ] else if (isFailed) ...[
                                        const Icon(Icons.error_outline, size: 14, color: Colors.redAccent),
                                        const SizedBox(width: 6),
                                      ] else ...[
                                        Icon(Icons.schedule, size: 14, color: Colors.amber[300]),
                                        const SizedBox(width: 6),
                                      ],
                                      Text(
                                        isInProgress
                                            ? 'Processing'
                                            : isCompleted
                                                ? 'Done'
                                                : isFailed
                                                    ? 'Failed'
                                                    : 'Queued',
                                        style: TextStyle(
                                          fontSize: 12,
                                          fontWeight: FontWeight.bold,
                                          color: isInProgress
                                              ? catColor
                                              : isCompleted
                                                  ? Colors.greenAccent
                                                  : isFailed
                                                      ? Colors.redAccent
                                                      : Colors.amber[300],
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            ),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }
}
