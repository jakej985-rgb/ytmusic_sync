import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_service.dart';

class HistoryView extends StatefulWidget {
  const HistoryView({super.key});

  @override
  State<HistoryView> createState() => _HistoryViewState();
}

class _HistoryViewState extends State<HistoryView> {
  List<SyncJob> _history = [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadHistory();
  }

  Future<void> _loadHistory() async {
    setState(() => _isLoading = true);
    try {
      final history = await apiService.getHistory();
      if (mounted) {
        setState(() {
          _history = history;
          _isLoading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _isLoading = false);
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
              const Text(
                'Upload History & Activity',
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
              ),
              const Spacer(),
              IconButton(
                icon: const Icon(Icons.refresh),
                onPressed: _loadHistory,
              ),
            ],
          ),
          const SizedBox(height: 24),

          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator())
                : _history.isEmpty
                    ? Center(
                        child: Text(
                          'No upload activity recorded yet.',
                          style: TextStyle(color: Colors.grey[400], fontSize: 16),
                        ),
                      )
                    : ListView.separated(
                        itemCount: _history.length,
                        separatorBuilder: (context, index) => const Divider(height: 1, color: Colors.white10),
                        itemBuilder: (context, index) {
                          final job = _history[index];
                          final isSuccess = job.status == 'verified' || job.status == 'uploaded';
                          final isFailed = job.status == 'failed';

                          return Padding(
                            padding: const EdgeInsets.symmetric(vertical: 12.0),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Icon(
                                  isSuccess
                                      ? Icons.check_circle
                                      : isFailed
                                          ? Icons.error
                                          : Icons.info,
                                  color: isSuccess
                                      ? Colors.greenAccent
                                      : isFailed
                                          ? Colors.redAccent
                                          : Colors.amberAccent,
                                  size: 24,
                                ),
                                const SizedBox(width: 16),
                                Expanded(
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(
                                        job.musicFile?.displayTitle ?? 'Track ID #${job.musicFileId}',
                                        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                                      ),
                                      if (job.musicFile?.artist != null)
                                        Text(
                                          '${job.musicFile!.displayArtist} • ${job.musicFile!.displayAlbum}',
                                          style: TextStyle(color: Colors.grey[400], fontSize: 12),
                                        ),
                                      if (job.musicFile?.path != null)
                                        Padding(
                                          padding: const EdgeInsets.only(top: 2.0),
                                          child: Text(
                                            job.musicFile!.path,
                                            style: TextStyle(color: Colors.grey[600], fontSize: 11, fontFamily: 'monospace'),
                                            maxLines: 1,
                                            overflow: TextOverflow.ellipsis,
                                          ),
                                        ),
                                      if (job.ytmEntityId != null)
                                        Padding(
                                          padding: const EdgeInsets.only(top: 2.0),
                                          child: Text(
                                            'YTM Entity ID: ${job.ytmEntityId}',
                                            style: const TextStyle(color: Colors.cyanAccent, fontSize: 11, fontFamily: 'monospace'),
                                          ),
                                        ),
                                      if (job.error != null)
                                        Padding(
                                          padding: const EdgeInsets.only(top: 4.0),
                                          child: Text(
                                            'Error: ${job.error}',
                                            style: const TextStyle(color: Colors.redAccent, fontSize: 12),
                                          ),
                                        ),
                                    ],
                                  ),
                                ),
                                Column(
                                  crossAxisAlignment: CrossAxisAlignment.end,
                                  children: [
                                    Container(
                                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                                      decoration: BoxDecoration(
                                        color: (isSuccess ? Colors.green : (isFailed ? Colors.red : Colors.grey)).withValues(alpha: 0.15),
                                        borderRadius: BorderRadius.circular(4),
                                      ),
                                      child: Text(
                                        job.status.toUpperCase(),
                                        style: TextStyle(
                                          fontSize: 11,
                                          fontWeight: FontWeight.bold,
                                          color: isSuccess ? Colors.greenAccent : (isFailed ? Colors.redAccent : Colors.grey),
                                        ),
                                      ),
                                    ),
                                    if (job.completedAt != null || job.startedAt != null)
                                      Padding(
                                        padding: const EdgeInsets.only(top: 4.0),
                                        child: Text(
                                          (job.completedAt ?? job.startedAt ?? '').split('T').join(' ').split('.').first,
                                          style: TextStyle(color: Colors.grey[500], fontSize: 11),
                                        ),
                                      ),
                                  ],
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
