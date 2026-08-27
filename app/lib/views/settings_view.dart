import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';
import '../models/models.dart';
import '../services/api_service.dart';

class SettingsView extends StatefulWidget {
  const SettingsView({super.key});

  @override
  State<SettingsView> createState() => _SettingsViewState();
}

class _SettingsViewState extends State<SettingsView> {
  final TextEditingController _headersController = TextEditingController();
  final TextEditingController _folderPathController = TextEditingController();

  List<String> _folders = [];
  AppSettings? _settings;
  ConnectionStatus? _authStatus;
  bool _isLoading = true;
  bool _isSavingAuth = false;
  String? _authMessage;

  @override
  void initState() {
    super.initState();
    _loadAll();
  }

  @override
  void dispose() {
    _headersController.dispose();
    _folderPathController.dispose();
    super.dispose();
  }

  Future<void> _loadAll() async {
    setState(() => _isLoading = true);
    try {
      final folders = await apiService.getFolders();
      final settings = await apiService.getSettings();
      final authStatus = await apiService.fetchAuthStatus();
      if (mounted) {
        setState(() {
          _folders = folders;
          _settings = settings;
          _authStatus = authStatus;
          _isLoading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _pickFolder() async {
    final selectedDirectory = await FilePicker.getDirectoryPath();
    if (selectedDirectory != null && !_folders.contains(selectedDirectory)) {
      final updated = List<String>.from(_folders)..add(selectedDirectory);
      await apiService.updateFolders(updated);
      if (mounted) {
        setState(() => _folders = updated);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Added folder: $selectedDirectory')),
        );
      }
    }
  }

  Future<void> _addManualFolder() async {
    final path = _folderPathController.text.trim();
    if (path.isNotEmpty && !_folders.contains(path)) {
      final updated = List<String>.from(_folders)..add(path);
      await apiService.updateFolders(updated);
      if (mounted) {
        setState(() {
          _folders = updated;
          _folderPathController.clear();
        });
      }
    }
  }

  Future<void> _removeFolder(String folder) async {
    final updated = List<String>.from(_folders)..remove(folder);
    await apiService.updateFolders(updated);
    if (mounted) {
      setState(() => _folders = updated);
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
                  '1. Open music.youtube.com in your web browser and log in.\n'
                  '2. Press F12 to open Developer Tools, then navigate to the Network tab.\n'
                  '3. Click on any request to music.youtube.com (e.g. browse or player).\n'
                  '4. Under Request Headers, right click and Copy Request Headers (or copy everything).\n'
                  '5. Paste the copied text into the field below and click "Connect Account".',
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

          // 2. Music Folders Section
          _buildCard(
            title: '2. Local Music Folders',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Directories containing your audio files (.mp3, .flac, .m4a, .ogg, .wma):',
                  style: TextStyle(color: Colors.grey[400], fontSize: 13),
                ),
                const SizedBox(height: 12),
                Row(
                  children: [
                    ElevatedButton.icon(
                      onPressed: _pickFolder,
                      icon: const Icon(Icons.folder_open),
                      label: const Text('Browse & Add Folder'),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: TextField(
                        controller: _folderPathController,
                        decoration: InputDecoration(
                          hintText: 'Or enter directory path manually...',
                          isDense: true,
                          border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
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
                const SizedBox(height: 16),
                if (_folders.isEmpty)
                  Text('No folders configured yet.', style: TextStyle(color: Colors.grey[500]))
                else
                  ListView.separated(
                    shrinkWrap: true,
                    physics: const NeverScrollableScrollPhysics(),
                    itemCount: _folders.length,
                    separatorBuilder: (context, index) => const Divider(height: 1, color: Colors.white10),
                    itemBuilder: (context, index) {
                      final folder = _folders[index];
                      return ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        leading: const Icon(Icons.folder, color: Colors.amberAccent),
                        title: Text(folder, style: const TextStyle(fontSize: 13, fontFamily: 'monospace')),
                        trailing: IconButton(
                          icon: const Icon(Icons.delete_outline, size: 20, color: Colors.redAccent),
                          onPressed: () => _removeFolder(folder),
                        ),
                      );
                    },
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
