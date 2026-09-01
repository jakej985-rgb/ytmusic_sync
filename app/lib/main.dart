import 'package:flutter/material.dart';
import 'views/dashboard_view.dart';
import 'views/library_view.dart';
import 'views/uploads_view.dart';
import 'views/playlists_view.dart';
import 'views/queue_view.dart';
import 'views/history_view.dart';
import 'views/settings_view.dart';

import 'services/api_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await apiService.initApiKey();
  runApp(const YTMSyncApp());
}

class YTMSyncApp extends StatelessWidget {
  const YTMSyncApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'YTM Sync',
      debugShowCheckedModeBanner: false,
      themeMode: ThemeMode.dark,
      darkTheme: ThemeData(
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF0F0F13),
        colorScheme: const ColorScheme.dark(
          primary: Color(0xFFFF0000),
          secondary: Color(0xFF3EA6FF),
          surface: Color(0xFF181820),
          error: Color(0xFFFF4E4E),
        ),
        cardTheme: CardThemeData(
          color: const Color(0xFF181820),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          elevation: 0,
        ),
        useMaterial3: true,
      ),
      home: const MainShell(),
    );
  }
}

class MainShell extends StatefulWidget {
  const MainShell({super.key});

  @override
  State<MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<MainShell> {
  int _selectedIndex = 0;
  bool _isAuthDialogOpen = false;

  @override
  void initState() {
    super.initState();
    apiService.onUnauthorized = _showApiKeyDialog;
  }

  void _showApiKeyDialog() {
    if (_isAuthDialogOpen || !mounted) return;
    _isAuthDialogOpen = true;
    final controller = TextEditingController(text: apiService.apiKey ?? '');
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (dialogCtx) => AlertDialog(
        backgroundColor: const Color(0xFF181820),
        title: const Row(
          children: [
            Icon(Icons.lock_outline, color: Color(0xFFFF4E4E), size: 22),
            SizedBox(width: 8),
            Text('API Authentication Required', style: TextStyle(fontSize: 18)),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Please enter your YTM Sync API Key to access backend services. '
              'This can be found in config/auth/api_key.txt or your YTM_SYNC_API_KEY environment variable.',
              style: TextStyle(color: Colors.grey, fontSize: 13),
            ),
            const SizedBox(height: 16),
            TextField(
              controller: controller,
              obscureText: true,
              decoration: const InputDecoration(
                labelText: 'API Key',
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () async {
              final key = controller.text.trim();
              if (key.isNotEmpty) {
                await apiService.setApiKey(key);
                if (mounted && dialogCtx.mounted) {
                  Navigator.of(dialogCtx).pop();
                  _isAuthDialogOpen = false;
                  setState(() {});
                }
              }
            },
            child: const Text('Save & Reconnect'),
          ),
        ],
      ),
    ).then((_) {
      _isAuthDialogOpen = false;
    });
  }

  void _navigateToTab(int index) {
    setState(() {
      _selectedIndex = index;
    });
  }

  @override
  Widget build(BuildContext context) {
    final views = [
      DashboardView(onNavigateTab: _navigateToTab),
      const LibraryView(),
      const UploadsView(),
      const PlaylistsView(),
      const QueueView(),
      const HistoryView(),
      const SettingsView(),
    ];

    return Scaffold(
      body: Row(
        children: [
          // Left Navigation Rail
          NavigationRail(
            backgroundColor: const Color(0xFF14141A),
            selectedIndex: _selectedIndex,
            onDestinationSelected: (int index) {
              setState(() {
                _selectedIndex = index;
              });
            },
            extended: true,
            minExtendedWidth: 200,
            leading: Padding(
              padding: const EdgeInsets.symmetric(vertical: 24.0, horizontal: 16.0),
              child: Row(
                children: [
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: const Color(0xFFFF0000),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Icon(Icons.music_note, color: Colors.white, size: 20),
                  ),
                  const SizedBox(width: 12),
                  const Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'YTM SYNC',
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w900,
                          letterSpacing: 1.2,
                        ),
                      ),
                      Text(
                        'Local ➔ YouTube Music',
                        style: TextStyle(
                          fontSize: 10,
                          color: Colors.grey,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            destinations: const [
              NavigationRailDestination(
                icon: Icon(Icons.dashboard_outlined),
                selectedIcon: Icon(Icons.dashboard, color: Color(0xFFFF0000)),
                label: Text('Dashboard'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.library_music_outlined),
                selectedIcon: Icon(Icons.library_music, color: Color(0xFFFF0000)),
                label: Text('Music Library'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.cloud_done_outlined),
                selectedIcon: Icon(Icons.cloud_done, color: Color(0xFFFF0000)),
                label: Text('YTM Uploads'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.playlist_play_outlined),
                selectedIcon: Icon(Icons.playlist_play, color: Color(0xFFFF0000)),
                label: Text('YTM Playlists'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.queue_music_outlined),
                selectedIcon: Icon(Icons.queue_music, color: Color(0xFFFF0000)),
                label: Text('Queue'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.history_outlined),
                selectedIcon: Icon(Icons.history, color: Color(0xFFFF0000)),
                label: Text('Sync History'),
              ),
              NavigationRailDestination(
                icon: Icon(Icons.settings_outlined),
                selectedIcon: Icon(Icons.settings, color: Color(0xFFFF0000)),
                label: Text('Settings'),
              ),
            ],
          ),
          const VerticalDivider(thickness: 1, width: 1, color: Colors.white10),

          // Main View Content
          Expanded(
            child: IndexedStack(
              index: _selectedIndex,
              children: views,
            ),
          ),
        ],
      ),
    );
  }
}
