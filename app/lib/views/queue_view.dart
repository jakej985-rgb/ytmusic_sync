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
  List<MusicFile> _queuedSongs = [];
  bool _isLoading = true;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _loadQueue();
    _timer = Timer.periodic(const Duration(seconds: 3), (_) => _loadQueue(silent: true));
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _loadQueue({bool silent = false}) async {
    if (!silent) setState(() => _isLoading = true);
    try {
      final songs = await apiService.getSongs(status: 'queued', limit: 100);
      if (mounted) {
        setState(() {
          _queuedSongs = songs;
          _isLoading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _enqueueMissing() async {
    try {
      final count = await apiService.uploadAllMissing();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Enqueued $count missing tracks')),
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

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(28.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Upload Queue',
                    style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    '${_queuedSongs.length} items currently in upload pipeline',
                    style: TextStyle(color: Colors.grey[400], fontSize: 13),
                  ),
                ],
              ),
              const Spacer(),
              FilledButton.tonalIcon(
                onPressed: _enqueueMissing,
                icon: const Icon(Icons.playlist_add),
                label: const Text('Add All Missing'),
              ),
              const SizedBox(width: 12),
              IconButton(
                icon: const Icon(Icons.refresh),
                onPressed: () => _loadQueue(),
              ),
            ],
          ),
          const SizedBox(height: 24),

          Expanded(
            child: _isLoading && _queuedSongs.isEmpty
                ? const Center(child: CircularProgressIndicator())
                : _queuedSongs.isEmpty
                    ? Center(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(Icons.done_all, size: 56, color: Colors.grey[600]),
                            const SizedBox(height: 12),
                            Text(
                              'Queue is empty',
                              style: TextStyle(color: Colors.grey[400], fontSize: 16),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              'All missing tracks have been uploaded or no uploads are queued.',
                              style: TextStyle(color: Colors.grey[600], fontSize: 13),
                            ),
                          ],
                        ),
                      )
                    : ListView.separated(
                        itemCount: _queuedSongs.length,
                        separatorBuilder: (context, index) => const SizedBox(height: 10),
                        itemBuilder: (context, index) {
                          final song = _queuedSongs[index];
                          final isUploading = song.uploadStatus == 'uploading';
                          return Container(
                            padding: const EdgeInsets.all(16),
                            decoration: BoxDecoration(
                              color: const Color(0xFF1B1B22),
                              borderRadius: BorderRadius.circular(10),
                              border: Border.all(
                                color: isUploading ? Colors.blueAccent.withValues(alpha: 0.5) : Colors.white10,
                              ),
                            ),
                            child: Row(
                              children: [
                                Text(
                                  '#${index + 1}',
                                  style: const TextStyle(fontWeight: FontWeight.bold, color: Colors.grey),
                                ),
                                const SizedBox(width: 16),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        song.displayTitle,
                                        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                                      ),
                                      const SizedBox(height: 2),
                                      Text(
                                        '${song.displayArtist} • ${song.displayAlbum}',
                                        style: TextStyle(color: Colors.grey[400], fontSize: 12),
                                      ),
                                    ],
                                  ),
                                ),
                                Container(
                                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                                  decoration: BoxDecoration(
                                    color: isUploading
                                        ? Colors.blue.withValues(alpha: 0.2)
                                        : Colors.amber.withValues(alpha: 0.15),
                                    borderRadius: BorderRadius.circular(6),
                                  ),
                                  child: Row(
                                    children: [
                                      if (isUploading) ...[
                                        const SizedBox(
                                          width: 12,
                                          height: 12,
                                          child: CircularProgressIndicator(strokeWidth: 2),
                                        ),
                                        const SizedBox(width: 8),
                                      ],
                                      Text(
                                        isUploading ? 'Uploading...' : 'Waiting in Queue',
                                        style: TextStyle(
                                          fontSize: 12,
                                          fontWeight: FontWeight.bold,
                                          color: isUploading ? Colors.blueAccent : Colors.amberAccent,
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
