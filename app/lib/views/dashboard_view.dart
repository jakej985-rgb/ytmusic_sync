import 'dart:async';
import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_service.dart';

class DashboardView extends StatefulWidget {
  final Function(int) onNavigateTab;

  const DashboardView({super.key, required this.onNavigateTab});

  @override
  State<DashboardView> createState() => _DashboardViewState();
}

class _DashboardViewState extends State<DashboardView> {
  DashboardStats? _stats;
  bool _isLoading = true;
  String? _errorMessage;
  Timer? _refreshTimer;

  @override
  void initState() {
    super.initState();
    _loadStats();
    _refreshTimer = Timer.periodic(const Duration(seconds: 5), (_) => _loadStats(silent: true));
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  Future<void> _loadStats({bool silent = false}) async {
    if (!silent) {
      setState(() {
        _isLoading = true;
        _errorMessage = null;
      });
    }
    try {
      final stats = await apiService.fetchDashboardStatus();
      if (mounted) {
        setState(() {
          _stats = stats;
          _isLoading = false;
          _errorMessage = null;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          _errorMessage = 'Backend service offline or unreachable. Is the service running on localhost?';
        });
      }
    }
  }

  Future<void> _triggerScan() async {
    try {
      await apiService.triggerScan();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Music folder scan started...')),
        );
      }
      _loadStats(silent: true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Scan error: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Future<void> _triggerSync() async {
    try {
      await apiService.triggerSync();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Library sync started (fetching YTM uploads & matching)...')),
        );
      }
      _loadStats(silent: true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Sync error: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Future<void> _uploadAllMissing() async {
    try {
      final count = await apiService.uploadAllMissing();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Enqueued $count missing tracks for upload!')),
        );
        _loadStats(silent: true);
        widget.onNavigateTab(2); // Go to queue view
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Upload error: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading && _stats == null) {
      return const Center(child: CircularProgressIndicator());
    }

    if (_errorMessage != null && _stats == null) {
      return Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.cloud_off, size: 64, color: Colors.amber),
            const SizedBox(height: 16),
            Text(_errorMessage!, style: const TextStyle(fontSize: 16)),
            const SizedBox(height: 16),
            ElevatedButton.icon(
              onPressed: () => _loadStats(),
              icon: const Icon(Icons.refresh),
              label: const Text('Retry Connection'),
            ),
          ],
        ),
      );
    }

    final stats = _stats!;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(28.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header Connection Banner
          _buildConnectionCard(stats),
          const SizedBox(height: 24),

          // Active Operations Indicator
          if (stats.isScanning || stats.isUploading) ...[
            _buildActiveTaskBanner(stats),
            const SizedBox(height: 24),
          ],

          // Quick Action Buttons Bar
          Row(
            children: [
              ElevatedButton.icon(
                onPressed: stats.ytmConnected ? _triggerSync : null,
                icon: const Icon(Icons.sync),
                label: const Text('SYNC NOW'),
                style: ElevatedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
                  backgroundColor: const Color(0xFFFF0000),
                  foregroundColor: Colors.white,
                  textStyle: const TextStyle(fontWeight: FontWeight.bold, letterSpacing: 1),
                ),
              ),
              const SizedBox(width: 16),
              OutlinedButton.icon(
                onPressed: _triggerScan,
                icon: const Icon(Icons.folder_open),
                label: const Text('Scan Local Music'),
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
                ),
              ),
              const SizedBox(width: 16),
              if (stats.missingCount > 0)
                FilledButton.tonalIcon(
                  onPressed: stats.ytmConnected ? _uploadAllMissing : null,
                  icon: const Icon(Icons.cloud_upload),
                  label: Text('Upload All Missing (${stats.missingCount})'),
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
                  ),
                ),
              const Spacer(),
              IconButton(
                icon: const Icon(Icons.refresh),
                tooltip: 'Refresh Dashboard',
                onPressed: () => _loadStats(silent: false),
              ),
            ],
          ),
          const SizedBox(height: 28),

          // Stats Grid
          const Text(
            'Library Overview',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 16),
          GridView.count(
            crossAxisCount: 3,
            crossAxisSpacing: 16,
            mainAxisSpacing: 16,
            shrinkWrap: true,
            childAspectRatio: 2.2,
            physics: const NeverScrollableScrollPhysics(),
            children: [
              _buildStatCard(
                title: 'Local Music',
                count: stats.localSongsCount,
                icon: Icons.library_music,
                color: Colors.blueAccent,
                onTap: () => widget.onNavigateTab(1),
              ),
              _buildStatCard(
                title: 'YTM Uploads',
                count: stats.ytmUploadsCount,
                icon: Icons.cloud_done,
                color: Colors.purpleAccent,
                onTap: () => widget.onNavigateTab(1),
              ),
              _buildStatCard(
                title: 'Missing From YTM',
                count: stats.missingCount,
                icon: Icons.cloud_upload_outlined,
                color: stats.missingCount > 0 ? Colors.orangeAccent : Colors.greenAccent,
                onTap: () => widget.onNavigateTab(1),
              ),
              _buildStatCard(
                title: 'Uploaded / Verified',
                count: stats.uploadedCount,
                icon: Icons.check_circle_outline,
                color: Colors.greenAccent,
                onTap: () => widget.onNavigateTab(1),
              ),
              _buildStatCard(
                title: 'In Upload Queue',
                count: stats.inQueueCount,
                icon: Icons.queue_music,
                color: Colors.tealAccent,
                onTap: () => widget.onNavigateTab(2),
              ),
              _buildStatCard(
                title: 'Failed Uploads',
                count: stats.failedCount,
                icon: Icons.error_outline,
                color: stats.failedCount > 0 ? Colors.redAccent : Colors.grey,
                onTap: () => widget.onNavigateTab(3),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildConnectionCard(DashboardStats stats) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E24),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: stats.ytmConnected
              ? Colors.green.withValues(alpha: 0.4)
              : Colors.redAccent.withValues(alpha: 0.4),
        ),
      ),
      child: Row(
        children: [
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: stats.ytmConnected
                  ? Colors.green.withValues(alpha: 0.15)
                  : Colors.red.withValues(alpha: 0.15),
              shape: BoxShape.circle,
            ),
            child: Icon(
              stats.ytmConnected ? Icons.check_circle : Icons.warning_amber_rounded,
              color: stats.ytmConnected ? Colors.greenAccent : Colors.redAccent,
              size: 32,
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    const Text(
                      'YouTube Music Status: ',
                      style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
                    ),
                    Text(
                      stats.ytmConnected ? 'Connected' : 'Not Connected',
                      style: TextStyle(
                        fontSize: 16,
                        fontWeight: FontWeight.bold,
                        color: stats.ytmConnected ? Colors.greenAccent : Colors.redAccent,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  stats.ytmConnected
                      ? (stats.accountName ?? 'Ready to synchronize personal music uploads')
                      : 'Browser authentication is required to upload music. Open settings to connect.',
                  style: TextStyle(fontSize: 13, color: Colors.grey[400]),
                ),
              ],
            ),
          ),
          if (!stats.ytmConnected)
            ElevatedButton(
              onPressed: () => widget.onNavigateTab(4), // Go to Settings
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFFFF0000),
                foregroundColor: Colors.white,
              ),
              child: const Text('Setup Connection'),
            ),
        ],
      ),
    );
  }

  Widget _buildActiveTaskBanner(DashboardStats stats) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
      decoration: BoxDecoration(
        color: const Color(0xFF262338),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.deepPurpleAccent.withValues(alpha: 0.5)),
      ),
      child: Row(
        children: [
          const SizedBox(
            width: 20,
            height: 20,
            child: CircularProgressIndicator(strokeWidth: 2.5),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Text(
              stats.isScanning && stats.isUploading
                  ? 'Scanning local folders and processing upload queue...'
                  : stats.isScanning
                      ? 'Scanning local music folders...'
                      : 'Uploading music queue to YouTube Music...',
              style: const TextStyle(fontWeight: FontWeight.w500),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildStatCard({
    required String title,
    required int count,
    required IconData icon,
    required Color color,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: const Color(0xFF191920),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Colors.white10),
        ),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: color.withValues(alpha: 0.12),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Icon(icon, color: color, size: 28),
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Text(
                    title,
                    style: TextStyle(fontSize: 13, color: Colors.grey[400], fontWeight: FontWeight.w500),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    count.toString(),
                    style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
                  ),
                ],
              ),
            ),
            const Icon(Icons.chevron_right, color: Colors.white24),
          ],
        ),
      ),
    );
  }
}
