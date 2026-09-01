import 'package:flutter/material.dart';
import '../models/models.dart';
import '../services/api_service.dart';
import 'components/folder_browser_dialog.dart';

class SettingsView extends StatefulWidget {
  const SettingsView({super.key});

  @override
  State<SettingsView> createState() => _SettingsViewState();
}

class _SettingsViewState extends State<SettingsView> {
  final TextEditingController _headersController = TextEditingController();
  final TextEditingController _folderPathController = TextEditingController();
  final TextEditingController _apiKeyController = TextEditingController();

  List<String> _folders = [];
  List<RootFolderStats> _folderStats = [];
  AppSettings? _settings;
  ConnectionStatus? _authStatus;
  bool _isLoading = true;
  bool _isSavingAuth = false;
  String? _authMessage;

  @override
  void initState() {
    super.initState();
    _apiKeyController.text = apiService.apiKey ?? '';
    _loadAll();
  }

  @override
  void dispose() {
    _headersController.dispose();
    _folderPathController.dispose();
    _apiKeyController.dispose();
    super.dispose();
  }

  Future<void> _loadAll() async {
    setState(() => _isLoading = true);
    try {
      final folders = await apiService.getFolders();
      final stats = await apiService.fetchFolderStats();
      final settings = await apiService.getSettings();
      final authStatus = await apiService.fetchAuthStatus();
      if (mounted) {
        setState(() {
          _folders = folders;
          _folderStats = stats;
          _settings = settings;
          _authStatus = authStatus;
          _isLoading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _openFolderBrowser() async {
    final selectedPath = await FolderBrowserDialog.show(
      context,
      initialPath: _folders.isNotEmpty ? _folders.first : '/music',
    );
    if (selectedPath != null && selectedPath.isNotEmpty && !_folders.contains(selectedPath)) {
      final updated = List<String>.from(_folders)..add(selectedPath);
      await apiService.updateFolders(updated);
      await _loadAll();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Added root folder: $selectedPath'),
            backgroundColor: Colors.green,
          ),
        );
      }
    }
  }

  Future<void> _addManualFolder() async {
    final path = _folderPathController.text.trim();
    if (path.isNotEmpty && !_folders.contains(path)) {
      final updated = List<String>.from(_folders)..add(path);
      await apiService.updateFolders(updated);
      _folderPathController.clear();
      await _loadAll();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Added root folder: $path'),
            backgroundColor: Colors.green,
          ),
        );
      }
    }
  }

  Future<void> _removeFolder(String folder) async {
    final updated = List<String>.from(_folders)..remove(folder);
    await apiService.updateFolders(updated);
    await _loadAll();
  }

  Future<void> _scanFolder(String folder) async {
    try {
      await apiService.triggerScan([folder]);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Started scan for $folder')),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Scan failed: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  Future<void> _submitAuthHeaders() async {
    final raw = _headersController.text.trim();
    if (raw.isEmpty) return;

    setState(() {
      _isSavingAuth = true;
      _authMessage = null;
    });

    try {
      final status = await apiService.setupAuth(raw);
      if (mounted) {
        setState(() {
          _authStatus = status;
          _isSavingAuth = false;
          _headersController.clear();
          _authMessage = status.message;
        });
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(status.connected ? 'YouTube Music connected successfully!' : status.message),
            backgroundColor: status.connected ? Colors.green : Colors.redAccent,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isSavingAuth = false;
          _authMessage = 'Failed: $e';
        });
      }
    }
  }

  Future<void> _testConnection() async {
    try {
      final status = await apiService.testAuth();
      if (mounted) {
        setState(() => _authStatus = status);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(status.message),
            backgroundColor: status.connected ? Colors.green : Colors.redAccent,
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Test error: $e'), backgroundColor: Colors.redAccent),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_isLoading) {
      return const Center(child: CircularProgressIndicator());
    }

    final isConnected = _authStatus?.connected ?? false;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(28.0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Settings & Configuration',
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold),
          ),
          const SizedBox(height: 24),

          // API Security Section
          _buildCard(
            title: 'API Authentication & Security',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'YTM Sync protects all API endpoints with an API key. '
                  'The key is stored in config/auth/api_key.txt or defined via YTM_SYNC_API_KEY.',
                  style: TextStyle(color: Colors.grey[400], fontSize: 12),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _apiKeyController,
                        obscureText: true,
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
                        decoration: InputDecoration(
                          hintText: 'Enter API Key',
                          labelText: 'API Key',
                          isDense: true,
                          border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                          filled: true,
                          fillColor: const Color(0xFF14141A),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    ElevatedButton.icon(
                      onPressed: () async {
                        final key = _apiKeyController.text.trim();
                        final messenger = ScaffoldMessenger.of(context);
                        await apiService.setApiKey(key);
                        if (!mounted) return;
                        messenger.showSnackBar(
                          const SnackBar(content: Text('API Key saved')),
                        );
                        _loadAll();
                      },
                      icon: const Icon(Icons.key, size: 16),
                      label: const Text('Save Key'),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),

          // 1. YouTube Music Auth Section
          _buildCard(
            title: '1. YouTube Music Connection',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      isConnected ? Icons.check_circle : Icons.warning_amber_rounded,
                      color: isConnected ? Colors.greenAccent : Colors.redAccent,
                      size: 20,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      isConnected ? 'Status: Connected' : 'Status: Not Connected',
                      style: TextStyle(
                        fontWeight: FontWeight.bold,
                        color: isConnected ? Colors.greenAccent : Colors.redAccent,
                      ),
                    ),
                    const Spacer(),
                    OutlinedButton.icon(
                      onPressed: _testConnection,
                      icon: const Icon(Icons.network_check, size: 16),
                      label: const Text('Test Connection'),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                const Text(
                  'Instructions to connect:',
                  style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                ),
                const SizedBox(height: 6),
                Text(
                  '1. Open music.youtube.com in your web browser and ensure you are logged in.\n'
                  '2. Press F12 to open Developer Tools, then click the Network tab.\n'
                  '3. In the Filter box, type "browse" (or click "Library" / "Explore" on YouTube Music).\n'
                  '4. Right-click on a "browse" request row ➔ hover over "Copy Value" ➔ click "Copy Request Headers" (or "Copy as cURL").\n'
                  '5. Paste the copied text directly into the box below and click "Connect YouTube Music".',
                  style: TextStyle(color: Colors.grey[400], height: 1.4, fontSize: 12),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _headersController,
                  maxLines: 4,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                  decoration: InputDecoration(
                    hintText: 'Paste request headers here (e.g. cookie: ..., authorization: ...)',
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                    filled: true,
                    fillColor: const Color(0xFF14141A),
                  ),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    ElevatedButton.icon(
                      onPressed: _isSavingAuth ? null : _submitAuthHeaders,
                      icon: _isSavingAuth
                          ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Icon(Icons.link),
                      label: const Text('Connect YouTube Music'),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFFFF0000),
                        foregroundColor: Colors.white,
                      ),
                    ),
                  ],
                ),
                if (_authMessage != null) ...[
                  const SizedBox(height: 10),
                  Text(_authMessage!, style: const TextStyle(color: Colors.amberAccent, fontSize: 12)),
                ],
              ],
            ),
          ),
          const SizedBox(height: 24),

          // 2. Root Folders Section (Radarr-Style)
          _buildCard(
            title: '2. Root Folders',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Container directories containing your music libraries. Scanned for audio files (.mp3, .flac, .m4a, .ogg, .wma).',
                  style: TextStyle(color: Colors.grey[400], fontSize: 13),
                ),
                const SizedBox(height: 16),

                if (_folderStats.isEmpty && _folders.isEmpty)
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(24),
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: const Color(0xFF14141A),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: Colors.white10),
                    ),
                    child: Text(
                      'No root folders configured yet. Click "Add Root Folder" to select a folder inside the container (e.g. /music).',
                      style: TextStyle(color: Colors.grey[500], fontSize: 13),
                    ),
                  )
                else
                  Container(
                    decoration: BoxDecoration(
                      color: const Color(0xFF14141A),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: Colors.white10),
                    ),
                    child: Table(
                      columnWidths: const {
                        0: FlexColumnWidth(3.5),
                        1: FlexColumnWidth(1.5),
                        2: FlexColumnWidth(1.2),
                        3: FlexColumnWidth(1.8),
                        4: FixedColumnWidth(96),
                      },
                      defaultVerticalAlignment: TableCellVerticalAlignment.middle,
                      children: [
                        // Header Row
                        TableRow(
                          decoration: const BoxDecoration(
                            border: Border(bottom: BorderSide(color: Colors.white12)),
                          ),
                          children: [
                            _buildTableHeader('Path'),
                            _buildTableHeader('Free Space'),
                            _buildTableHeader('Songs'),
                            _buildTableHeader('Unmapped Folders'),
                            _buildTableHeader('Actions'),
                          ],
                        ),
                        // Data Rows
                        ...(_folderStats.isNotEmpty
                            ? _folderStats
                            : _folders.map((f) => RootFolderStats(
                                  path: f,
                                  exists: true,
                                  freeSpace: 'N/A',
                                  totalSpace: 'N/A',
                                  songsCount: 0,
                                  unmappedCount: 0,
                                ))).map((stat) => TableRow(
                              decoration: const BoxDecoration(
                                border: Border(bottom: BorderSide(color: Colors.white10)),
                              ),
                              children: [
                                Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                                  child: Row(
                                    children: [
                                      Icon(
                                        stat.exists ? Icons.folder : Icons.folder_off_outlined,
                                        size: 18,
                                        color: stat.exists ? const Color(0xFF3EA6FF) : Colors.redAccent,
                                      ),
                                      const SizedBox(width: 8),
                                      Expanded(
                                        child: Text(
                                          stat.path,
                                          style: TextStyle(
                                            fontFamily: 'monospace',
                                            fontSize: 13,
                                            fontWeight: FontWeight.w600,
                                            color: stat.exists ? const Color(0xFF3EA6FF) : Colors.redAccent,
                                          ),
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                                Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                                  child: Text(
                                    stat.freeSpace,
                                    style: const TextStyle(fontSize: 13, fontFamily: 'monospace'),
                                  ),
                                ),
                                Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                                  child: Text(
                                    '${stat.songsCount}',
                                    style: const TextStyle(fontSize: 13),
                                  ),
                                ),
                                Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                                  child: Row(
                                    children: [
                                      Container(
                                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                                        decoration: BoxDecoration(
                                          color: stat.unmappedCount > 0
                                              ? Colors.amber.withValues(alpha: 0.15)
                                              : Colors.green.withValues(alpha: 0.15),
                                          borderRadius: BorderRadius.circular(4),
                                        ),
                                        child: Text(
                                          '${stat.unmappedCount}',
                                          style: TextStyle(
                                            fontSize: 12,
                                            fontWeight: FontWeight.bold,
                                            color: stat.unmappedCount > 0 ? Colors.amberAccent : Colors.greenAccent,
                                          ),
                                        ),
                                      ),
                                    ],
                                  ),
                                ),
                                Padding(
                                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                                  child: Row(
                                    mainAxisSize: MainAxisSize.min,
                                    children: [
                                      IconButton(
                                        icon: const Icon(Icons.sync, size: 18, color: Colors.grey),
                                        tooltip: 'Scan this root folder',
                                        onPressed: () => _scanFolder(stat.path),
                                      ),
                                      IconButton(
                                        icon: const Icon(Icons.delete_outline, size: 18, color: Colors.redAccent),
                                        tooltip: 'Remove root folder',
                                        onPressed: () => _removeFolder(stat.path),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            )),
                      ],
                    ),
                  ),
                const SizedBox(height: 16),

                // Button row: [Add Root Folder] + manual path input
                Row(
                  children: [
                    FilledButton.icon(
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFF3EA6FF),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                      ),
                      onPressed: _openFolderBrowser,
                      icon: const Icon(Icons.create_new_folder_outlined, size: 18),
                      label: const Text('Add Root Folder', style: TextStyle(fontWeight: FontWeight.bold)),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: TextField(
                        controller: _folderPathController,
                        style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
                        decoration: InputDecoration(
                          hintText: 'Or enter container path manually (e.g. /music)...',
                          isDense: true,
                          filled: true,
                          fillColor: const Color(0xFF14141A),
                          border: OutlineInputBorder(
                            borderRadius: BorderRadius.circular(8),
                            borderSide: BorderSide.none,
                          ),
                          contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        ),
                        onSubmitted: (_) => _addManualFolder(),
                      ),
                    ),
                    const SizedBox(width: 8),
                    FilledButton.tonal(
                      onPressed: _addManualFolder,
                      child: const Text('Add'),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),

          // 3. Sync Preferences
          _buildCard(
            title: '3. Synchronization Preferences',
            child: Column(
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Verify Uploads Post-Upload'),
                  subtitle: Text(
                    'Refreshes your YouTube Music library and confirms track existence before marking as verified.',
                    style: TextStyle(color: Colors.grey[400], fontSize: 12),
                  ),
                  value: _settings?.verifyUploads ?? true,
                  onChanged: (val) async {
                    await apiService.updateSettings(verifyUploads: val);
                    setState(() => _settings = AppSettings(
                      musicFolders: _settings!.musicFolders,
                      autoUpload: _settings!.autoUpload,
                      scanIntervalMinutes: _settings!.scanIntervalMinutes,
                      verifyUploads: val,
                    ));
                  },
                ),
                const Divider(color: Colors.white10),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Automatically Upload New Music'),
                  subtitle: Text(
                    'Automatically queue and upload newly detected files during periodic background scans (defaults to OFF).',
                    style: TextStyle(color: Colors.grey[400], fontSize: 12),
                  ),
                  value: _settings?.autoUpload ?? false,
                  onChanged: (val) async {
                    await apiService.updateSettings(autoUpload: val);
                    setState(() => _settings = AppSettings(
                      musicFolders: _settings!.musicFolders,
                      autoUpload: val,
                      scanIntervalMinutes: _settings!.scanIntervalMinutes,
                      verifyUploads: _settings!.verifyUploads,
                    ));
                  },
                ),
                const Divider(color: Colors.white10),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Periodic Scan Interval'),
                  subtitle: Text(
                    'How frequently local folders are rescanned for new music additions.',
                    style: TextStyle(color: Colors.grey[400], fontSize: 12),
                  ),
                  trailing: DropdownButton<int>(
                    value: _settings?.scanIntervalMinutes ?? 15,
                    dropdownColor: const Color(0xFF22222C),
                    items: const [
                      DropdownMenuItem(value: 5, child: Text('5 minutes')),
                      DropdownMenuItem(value: 15, child: Text('15 minutes')),
                      DropdownMenuItem(value: 30, child: Text('30 minutes')),
                      DropdownMenuItem(value: 60, child: Text('1 hour')),
                    ],
                    onChanged: (val) async {
                      if (val != null) {
                        await apiService.updateSettings(scanIntervalMinutes: val);
                        setState(() => _settings = AppSettings(
                          musicFolders: _settings!.musicFolders,
                          autoUpload: _settings!.autoUpload,
                          scanIntervalMinutes: val,
                          verifyUploads: _settings!.verifyUploads,
                        ));
                      }
                    },
                  ),
                ),
                const Divider(color: Colors.white10),
                const ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text('Sequential Uploads (One at a Time)'),
                  subtitle: Text(
                    'Strictly enforced for stability and avoiding YouTube Music rate limit blocks.',
                    style: TextStyle(color: Colors.grey, fontSize: 12),
                  ),
                  trailing: Icon(Icons.check_circle, color: Colors.greenAccent, size: 20),
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),

          // 4. Database Maintenance & Backup
          _buildCard(
            title: '4. Database Maintenance & Backup',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Create timestamped point-in-time backups of your local tracks, matches, and upload metadata.',
                  style: TextStyle(color: Colors.grey[400], fontSize: 12),
                ),
                const SizedBox(height: 12),
                ElevatedButton.icon(
                  onPressed: () async {
                    final messenger = ScaffoldMessenger.of(context);
                    try {
                      final path = await apiService.backupDatabase();
                      messenger.showSnackBar(
                        SnackBar(content: Text('Backup created: $path'), backgroundColor: Colors.green),
                      );
                    } catch (e) {
                      messenger.showSnackBar(
                        SnackBar(content: Text('Backup failed: $e'), backgroundColor: Colors.redAccent),
                      );
                    }
                  },
                  icon: const Icon(Icons.backup),
                  label: const Text('Create Database Backup'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTableHeader(String text) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Text(
        text,
        style: const TextStyle(
          fontWeight: FontWeight.bold,
          fontSize: 13,
          color: Colors.grey,
        ),
      ),
    );
  }

  Widget _buildCard({required String title, required Widget child}) {
    return Container(
      padding: const EdgeInsets.all(20),
      decoration: BoxDecoration(
        color: const Color(0xFF1B1B22),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white10),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
          const SizedBox(height: 16),
          child,
        ],
      ),
    );
  }
}
