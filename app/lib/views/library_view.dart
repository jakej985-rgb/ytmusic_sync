import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_service.dart';
import 'components/metadata_editor_dialog.dart';

class LibraryView extends StatefulWidget {
  const LibraryView({super.key});

  @override
  State<LibraryView> createState() => _LibraryViewState();
}

class _LibraryViewState extends State<LibraryView> with SingleTickerProviderStateMixin {
  late TabController _tabController;
  final TextEditingController _searchController = TextEditingController();
  List<MusicFile> _songs = [];
  bool _isLoading = false;
  String? _error;
  String _currentFilter = 'all';

  final List<String> _filters = ['all', 'missing', 'uploaded', 'queued', 'failed'];

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: _filters.length, vsync: this);
    _tabController.addListener(() {
      if (!_tabController.indexIsChanging) {
        setState(() {
          _currentFilter = _filters[_tabController.index];
        });
        _loadSongs();
      }
    });
    _loadSongs();
  }

  @override
  void dispose() {
    _tabController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _loadSongs() async {
    setState(() {
      _isLoading = true;
      _error = null;
    });
    try {
      final songs = await apiService.getSongs(
        status: _currentFilter,
        search: _searchController.text.trim(),
        limit: 300,
      );
      if (mounted) {
        setState(() {
          _songs = songs;
          _isLoading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          _error = e.toString();
        });
      }
    }
  }

  Future<void> _uploadTrack(MusicFile song) async {
    if (song.id == null) return;
    try {
      await apiService.uploadSong(song.id!);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Enqueued "${song.displayTitle}" for upload')),
        );
        _loadSongs();
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Failed to enqueue upload: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Future<void> _openMetadataEditor(MusicFile song) async {
    final saved = await MetadataEditorDialog.show(context, song);
    if (saved == true) {
      _loadSongs();
    }
  }

  final Set<int> _selectedSongIds = {};
  bool _isBatchProcessing = false;

  void _toggleSelectAll() {
    setState(() {
      final allIds = _songs.where((s) => s.id != null).map((s) => s.id!).toSet();
      if (_selectedSongIds.containsAll(allIds)) {
        _selectedSongIds.removeAll(allIds);
      } else {
        _selectedSongIds.addAll(allIds);
      }
    });
  }

  void _toggleSelectSong(int id) {
    setState(() {
      if (_selectedSongIds.contains(id)) {
        _selectedSongIds.remove(id);
      } else {
        _selectedSongIds.add(id);
      }
    });
  }

  Future<void> _batchUploadSelected() async {
    if (_selectedSongIds.isEmpty) return;
    setState(() => _isBatchProcessing = true);
    try {
      final count = await apiService.batchUploadSongs(_selectedSongIds.toList());
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Enqueued $count song${count > 1 ? 's' : ''} for upload!'),
            backgroundColor: Colors.green,
          ),
        );
      }
      _selectedSongIds.clear();
      await _loadSongs();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Batch upload failed: $e'), backgroundColor: Colors.redAccent),
        );
      }
    } finally {
      if (mounted) setState(() => _isBatchProcessing = false);
    }
  }

  Future<void> _batchDeleteSelected() async {
    if (_selectedSongIds.isEmpty) return;
    final count = _selectedSongIds.length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF181822),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        title: Row(
          children: [
            const Icon(Icons.delete_forever, color: Colors.redAccent, size: 22),
            const SizedBox(width: 8),
            Text('Remove $count Song${count > 1 ? 's' : ''}?'),
          ],
        ),
        content: Text(
          'Remove $count selected track${count > 1 ? 's' : ''} from the library database? (Audio files on disk will NOT be deleted).',
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
            child: const Text('Remove All'),
          ),
        ],
      ),
    );

    if (confirmed == true) {
      setState(() => _isBatchProcessing = true);
      try {
        final deleted = await apiService.batchDeleteSongs(_selectedSongIds.toList());
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text('Removed $deleted song${deleted > 1 ? 's' : ''} from library.'),
              backgroundColor: Colors.green,
            ),
          );
        }
        _selectedSongIds.clear();
        await _loadSongs();
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Batch delete failed: $e'), backgroundColor: Colors.redAccent),
          );
        }
      } finally {
        if (mounted) setState(() => _isBatchProcessing = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Filter tabs & search bar
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
          decoration: const BoxDecoration(
            color: Color(0xFF16161C),
            border: Border(bottom: BorderSide(color: Colors.white10)),
          ),
          child: Row(
            children: [
              Expanded(
                flex: 3,
                child: TabBar(
                  controller: _tabController,
                  isScrollable: true,
                  indicatorColor: const Color(0xFFFF0000),
                  labelColor: Colors.white,
                  unselectedLabelColor: Colors.grey,
                  tabs: const [
                    Tab(text: 'All Songs'),
                    Tab(text: 'Missing From YTM'),
                    Tab(text: 'Uploaded / Verified'),
                    Tab(text: 'Queued'),
                    Tab(text: 'Failed'),
                  ],
                ),
              ),
              const SizedBox(width: 24),
              Expanded(
                flex: 2,
                child: TextField(
                  controller: _searchController,
                  decoration: InputDecoration(
                    hintText: 'Search title, artist, album...',
                    prefixIcon: const Icon(Icons.search, size: 20),
                    suffixIcon: _searchController.text.isNotEmpty
                        ? IconButton(
                            icon: const Icon(Icons.clear, size: 18),
                            onPressed: () {
                              _searchController.clear();
                              _loadSongs();
                            },
                          )
                        : null,
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                  ),
                  onSubmitted: (_) => _loadSongs(),
                ),
              ),
              const SizedBox(width: 12),
              IconButton(
                icon: const Icon(Icons.refresh),
                tooltip: 'Refresh list',
                onPressed: _loadSongs,
              ),
              const SizedBox(width: 8),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: Colors.white70,
                  side: const BorderSide(color: Colors.white24),
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
                ),
                onPressed: _toggleSelectAll,
                icon: Icon(
                  _songs.isNotEmpty && _selectedSongIds.containsAll(_songs.where((s) => s.id != null).map((s) => s.id!))
                      ? Icons.deselect
                      : Icons.select_all,
                  size: 16,
                ),
                label: Text(
                  _songs.isNotEmpty && _selectedSongIds.containsAll(_songs.where((s) => s.id != null).map((s) => s.id!))
                      ? 'Deselect All'
                      : 'Select All',
                  style: const TextStyle(fontSize: 12),
                ),
              ),
            ],
          ),
        ),

        // Multi-Select Action Bar (shown when 1 or more tracks selected)
        if (_selectedSongIds.isNotEmpty)
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 10),
            decoration: BoxDecoration(
              color: const Color(0xFF1B1B28),
              border: Border(bottom: BorderSide(color: const Color(0xFF00B4D8).withValues(alpha: 0.3))),
            ),
            child: Row(
              children: [
                Checkbox(
                  value: _songs.isNotEmpty && _selectedSongIds.containsAll(_songs.where((s) => s.id != null).map((s) => s.id!)),
                  activeColor: const Color(0xFF00B4D8),
                  checkColor: Colors.black,
                  side: const BorderSide(color: Colors.white38),
                  onChanged: (_) => _toggleSelectAll(),
                ),
                Text(
                  '${_selectedSongIds.length} tracks selected',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.white),
                ),
                const SizedBox(width: 12),
                TextButton(
                  onPressed: () => setState(() => _selectedSongIds.clear()),
                  child: const Text('Clear Selection', style: TextStyle(color: Colors.white60, fontSize: 12)),
                ),
                const Spacer(),
                if (_isBatchProcessing)
                  const SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF00B4D8)),
                  )
                else ...[
                  FilledButton.icon(
                    style: FilledButton.styleFrom(
                      backgroundColor: const Color(0xFF00B4D8),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                    ),
                    onPressed: _batchUploadSelected,
                    icon: const Icon(Icons.cloud_upload, size: 16),
                    label: Text('Upload Selected (${_selectedSongIds.length})'),
                  ),
                  const SizedBox(width: 10),
                  FilledButton.icon(
                    style: FilledButton.styleFrom(
                      backgroundColor: Colors.redAccent,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
                    ),
                    onPressed: _batchDeleteSelected,
                    icon: const Icon(Icons.delete_outline, size: 16),
                    label: Text('Remove Selected (${_selectedSongIds.length})'),
                  ),
                ],
              ],
            ),
          ),

        // Content Area
        Expanded(
          child: _isLoading
              ? const Center(child: CircularProgressIndicator())
              : _error != null
                  ? Center(child: Text('Error: $_error', style: const TextStyle(color: Colors.redAccent)))
                  : _songs.isEmpty
                      ? Center(
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(Icons.music_off, size: 56, color: Colors.grey[600]),
                              const SizedBox(height: 12),
                              Text(
                                'No songs found for filter "$_currentFilter"',
                                style: TextStyle(color: Colors.grey[400], fontSize: 16),
                              ),
                            ],
                          ),
                        )
                      : ListView.separated(
                          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
                          itemCount: _songs.length,
                          separatorBuilder: (context, index) => const Divider(height: 1, color: Colors.white10),
                          itemBuilder: (context, index) {
                            final song = _songs[index];
                            return _buildSongRow(song);
                          },
                        ),
        ),
      ],
    );
  }

  Widget _buildSongRow(MusicFile song) {
    final isSelected = song.id != null && _selectedSongIds.contains(song.id!);
    return InkWell(
      onTap: () {
        if (_selectedSongIds.isNotEmpty && song.id != null) {
          _toggleSelectSong(song.id!);
        } else {
          _showSongDetails(song);
        }
      },
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 8.0, horizontal: 8.0),
        decoration: BoxDecoration(
          color: isSelected ? const Color(0xFF181F2C) : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Row(
          children: [
            if (song.id != null) ...[
              Checkbox(
                value: isSelected,
                activeColor: const Color(0xFF00B4D8),
                checkColor: Colors.black,
                side: const BorderSide(color: Colors.white38),
                onChanged: (_) => _toggleSelectSong(song.id!),
              ),
              const SizedBox(width: 4),
            ],
            // Track format & track number icon / box
            Container(
              width: 52,
              height: 44,
              decoration: BoxDecoration(
                color: const Color(0xFF22222C),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    song.format,
                    style: const TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: Colors.grey),
                  ),
                  if (song.trackNumber != null)
                    Text(
                      '#${song.trackNumber}',
                      style: const TextStyle(fontSize: 10, color: Colors.white70, fontWeight: FontWeight.w600),
                    ),
                ],
              ),
            ),
            const SizedBox(width: 16),
            // Title & Artist & Path
            Expanded(
              flex: 4,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    song.displayTitle,
                    style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 3),
                  Text(
                    song.displayArtist,
                    style: TextStyle(color: Colors.grey[400], fontSize: 12),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  Text(
                    song.path,
                    style: TextStyle(color: Colors.grey[600], fontSize: 10, fontFamily: 'monospace'),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            // Album
            Expanded(
              flex: 3,
              child: Text(
                song.displayAlbum,
                style: TextStyle(color: Colors.grey[400], fontSize: 12),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            // Duration & Size
            SizedBox(
              width: 100,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(song.formattedDuration, style: const TextStyle(fontSize: 12)),
                  Text(song.formattedSize, style: TextStyle(color: Colors.grey[500], fontSize: 11)),
                ],
              ),
            ),
            const SizedBox(width: 16),
            // Status Badge
            SizedBox(
              width: 110,
              child: _buildStatusBadge(song.uploadStatus),
            ),
            const SizedBox(width: 8),
            // Actions: Edit Metadata & Upload
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                IconButton(
                  icon: const Icon(Icons.edit_outlined, size: 18, color: Colors.grey),
                  tooltip: 'Edit Track Metadata',
                  onPressed: () => _openMetadataEditor(song),
                ),
                const SizedBox(width: 4),
                if (song.uploadStatus == 'not_uploaded' || song.uploadStatus == 'failed')
                  FilledButton.tonal(
                    onPressed: () => _uploadTrack(song),
                    style: FilledButton.styleFrom(
                      padding: const EdgeInsets.symmetric(horizontal: 10),
                      visualDensity: VisualDensity.compact,
                    ),
                    child: const Text('Upload', style: TextStyle(fontSize: 12)),
                  )
                else
                  const SizedBox(width: 64),
              ],
            ),
          ],
        ),
      ),
    );
  }

  void _showSongDetails(MusicFile song) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(song.displayTitle),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              _detailRow('Artist:', song.displayArtist),
              _detailRow('Album:', song.displayAlbum),
              _detailRow('Track #:', song.trackNumber?.toString() ?? 'N/A'),
              _detailRow('Format:', song.format),
              _detailRow('Duration:', song.formattedDuration),
              _detailRow('File Size:', song.formattedSize),
              _detailRow('Upload Status:', song.uploadStatus.toUpperCase()),
              if (song.matchedUploadId != null)
                _detailRow('Matched YTM ID:', song.matchedUploadId!),
              if (song.matchScore != null)
                _detailRow('Match Score:', '${(song.matchScore! * 100).toStringAsFixed(0)}%'),
              const SizedBox(height: 8),
              const Text('Full Path:', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
              SelectableText(
                song.path,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 11, color: Colors.grey),
              ),
            ],
          ),
        ),
        actions: [
          OutlinedButton.icon(
            icon: const Icon(Icons.edit_outlined, size: 16),
            label: const Text('Edit Metadata'),
            onPressed: () {
              Navigator.of(ctx).pop();
              _openMetadataEditor(song);
            },
          ),
          if (song.uploadStatus == 'not_uploaded' || song.uploadStatus == 'failed')
            FilledButton(
              onPressed: () {
                Navigator.of(ctx).pop();
                _uploadTrack(song);
              },
              child: const Text('Upload Now'),
            ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  Widget _detailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3.0),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 110,
            child: Text(label, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12)),
          ),
          Expanded(
            child: SelectableText(value, style: const TextStyle(fontSize: 12)),
          ),
        ],
      ),
    );
  }

  Widget _buildStatusBadge(String status) {
    Color color;
    String label;
    IconData icon;

    switch (status) {
      case 'verified':
      case 'uploaded':
        color = Colors.greenAccent;
        label = 'Uploaded';
        icon = Icons.check_circle_outline;
        break;
      case 'uploading':
        color = Colors.blueAccent;
        label = 'Uploading';
        icon = Icons.upload;
        break;
      case 'queued':
        color = Colors.amberAccent;
        label = 'Queued';
        icon = Icons.hourglass_top;
        break;
      case 'verifying':
        color = Colors.purpleAccent;
        label = 'Verifying';
        icon = Icons.verified_outlined;
        break;
      case 'failed':
        color = Colors.redAccent;
        label = 'Failed';
        icon = Icons.error_outline;
        break;
      case 'not_uploaded':
      default:
        color = Colors.grey;
        label = 'Missing';
        icon = Icons.cloud_upload_outlined;
        break;
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: color.withValues(alpha: 0.3)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 13, color: color),
          const SizedBox(width: 4),
          Text(
            label,
            style: TextStyle(fontSize: 11, fontWeight: FontWeight.w600, color: color),
          ),
        ],
      ),
    );
  }
}
