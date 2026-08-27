import 'package:flutter/material.dart';
import '../../models/models.dart';
import '../../services/api_service.dart';

class FolderBrowserDialog extends StatefulWidget {
  final String? initialPath;

  const FolderBrowserDialog({super.key, this.initialPath});

  static Future<String?> show(BuildContext context, {String? initialPath}) {
    return showDialog<String>(
      context: context,
      builder: (context) => FolderBrowserDialog(initialPath: initialPath),
    );
  }

  @override
  State<FolderBrowserDialog> createState() => _FolderBrowserDialogState();
}

class _FolderBrowserDialogState extends State<FolderBrowserDialog> {
  final TextEditingController _pathController = TextEditingController();
  FsBrowseResult? _browseResult;
  bool _isLoading = false;
  String? _errorMessage;

  @override
  void initState() {
    super.initState();
    _loadDirectory(widget.initialPath ?? '/music');
  }

  @override
  void dispose() {
    _pathController.dispose();
    super.dispose();
  }

  Future<void> _loadDirectory(String path) async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final res = await apiService.browseFilesystem(path);
      if (mounted) {
        setState(() {
          _browseResult = res;
          _pathController.text = res.currentPath;
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

  void _navigateToParent() {
    if (_browseResult?.parentPath != null) {
      _loadDirectory(_browseResult!.parentPath!);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: const Color(0xFF181820),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Container(
        width: 620,
        height: 520,
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Title Header
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Row(
                  children: [
                    Icon(Icons.folder_open, color: Color(0xFF3EA6FF), size: 22),
                    SizedBox(width: 10),
                    Text(
                      'Select Root Folder',
                      style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                    ),
                  ],
                ),
                IconButton(
                  onPressed: () => Navigator.of(context).pop(),
                  icon: const Icon(Icons.close, size: 20),
                  tooltip: 'Close',
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              'Browsing internal Docker container filesystem. Select the folder where your audio files are mounted.',
              style: TextStyle(color: Colors.grey[400], fontSize: 12),
            ),
            const SizedBox(height: 14),

            // Quick shortcuts
            Row(
              children: [
                const Text('Quick Access: ', style: TextStyle(fontSize: 12, color: Colors.grey)),
                _buildQuickChip('/music'),
                const SizedBox(width: 6),
                _buildQuickChip('/media'),
                const SizedBox(width: 6),
                _buildQuickChip('/'),
              ],
            ),
            const SizedBox(height: 12),

            // Path navigation input & Up button
            Row(
              children: [
                IconButton.filledTonal(
                  onPressed: (_browseResult?.parentPath != null && !_isLoading)
                      ? _navigateToParent
                      : null,
                  icon: const Icon(Icons.arrow_upward, size: 18),
                  tooltip: 'Up one directory',
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: TextField(
                    controller: _pathController,
                    style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
                    decoration: InputDecoration(
                      isDense: true,
                      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                      filled: true,
                      fillColor: const Color(0xFF14141A),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(8),
                        borderSide: const BorderSide(color: Colors.white10),
                      ),
                      prefixIcon: const Icon(Icons.location_on_outlined, size: 18, color: Colors.grey),
                    ),
                    onSubmitted: (val) => _loadDirectory(val.trim()),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filledTonal(
                  onPressed: _isLoading ? null : () => _loadDirectory(_pathController.text.trim()),
                  icon: const Icon(Icons.refresh, size: 18),
                  tooltip: 'Reload directory',
                ),
              ],
            ),
            const SizedBox(height: 12),

            // Directory listing content
            Expanded(
              child: Container(
                decoration: BoxDecoration(
                  color: const Color(0xFF14141A),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: Colors.white10),
                ),
                child: _buildDirectoryList(),
              ),
            ),
            const SizedBox(height: 14),

            // Bottom bar: Free Space & Action buttons
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                if (_browseResult != null)
                  Row(
                    children: [
                      const Icon(Icons.storage, size: 16, color: Colors.grey),
                      const SizedBox(width: 6),
                      Text(
                        'Free: ${_browseResult!.freeSpace} / Total: ${_browseResult!.totalSpace}',
                        style: TextStyle(color: Colors.grey[400], fontSize: 12, fontFamily: 'monospace'),
                      ),
                    ],
                  )
                else
                  const SizedBox(),
                Row(
                  children: [
                    OutlinedButton(
                      onPressed: () => Navigator.of(context).pop(),
                      child: const Text('Cancel'),
                    ),
                    const SizedBox(width: 10),
                    FilledButton(
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFF3EA6FF),
                        foregroundColor: Colors.white,
                      ),
                      onPressed: () {
                        final selected = _pathController.text.trim();
                        if (selected.isNotEmpty) {
                          Navigator.of(context).pop(selected);
                        }
                      },
                      child: const Row(
                        children: [
                          Icon(Icons.check, size: 16),
                          SizedBox(width: 6),
                          Text('Select Folder'),
                        ],
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildQuickChip(String path) {
    return InkWell(
      onTap: () => _loadDirectory(path),
      borderRadius: BorderRadius.circular(4),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: const Color(0xFF22222E),
          borderRadius: BorderRadius.circular(4),
          border: Border.all(color: Colors.white10),
        ),
        child: Text(
          path,
          style: const TextStyle(fontSize: 11, fontFamily: 'monospace', color: Color(0xFF3EA6FF)),
        ),
      ),
    );
  }

  Widget _buildDirectoryList() {
    if (_isLoading) {
      return const Center(
        child: CircularProgressIndicator(color: Color(0xFF3EA6FF)),
      );
    }

    if (_errorMessage != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(16.0),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.error_outline, size: 36, color: Colors.amberAccent),
              const SizedBox(height: 8),
              Text(_errorMessage!, style: const TextStyle(color: Colors.amberAccent, fontSize: 12)),
              const SizedBox(height: 12),
              ElevatedButton(
                onPressed: () => _loadDirectory('/'),
                child: const Text('Go to Root (/)'),
              ),
            ],
          ),
        ),
      );
    }

    final dirs = _browseResult?.directories ?? [];
    if (dirs.isEmpty) {
      return Center(
        child: Text(
          'No subdirectories found in this folder.',
          style: TextStyle(color: Colors.grey[500], fontSize: 13),
        ),
      );
    }

    return ListView.separated(
      itemCount: dirs.length,
      separatorBuilder: (_, __) => const Divider(height: 1, color: Color(0xFF22222E)),
      itemBuilder: (context, index) {
        final d = dirs[index];
        return ListTile(
          dense: true,
          leading: const Icon(Icons.folder, color: Color(0xFF3EA6FF), size: 20),
          title: Text(
            d.name,
            style: const TextStyle(fontSize: 13, fontWeight: FontWeight.w500),
          ),
          trailing: const Icon(Icons.chevron_right, size: 18, color: Colors.grey),
          onTap: () => _loadDirectory(d.path),
        );
      },
    );
  }
}
