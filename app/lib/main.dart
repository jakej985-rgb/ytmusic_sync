import 'package:flutter/material.dart';
import 'views/dashboard_view.dart';
import 'views/library_view.dart';
import 'views/uploads_view.dart';
import 'views/playlists_view.dart';
import 'views/queue_view.dart';
import 'views/history_view.dart';
import 'views/settings_view.dart';
import 'views/family_view.dart';
import 'views/components/auth_dialog.dart';

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
    apiService.onUnauthorized = _showAuthDialog;
  }

  void _showAuthDialog() {
    if (_isAuthDialogOpen || !mounted) return;
    _isAuthDialogOpen = true;
    AuthDialog.show(context).then((_) {
      _isAuthDialogOpen = false;
      if (mounted) setState(() {});
    });
  }

  void _navigateToTab(int index) {
    setState(() {
      _selectedIndex = index;
    });
  }

  Widget _buildAccountIndicator() {
    final user = apiService.currentUser;
    final ytmAccount = apiService.ytmAccount;
    final isYtmConnected = ytmAccount?.isConnected ?? false;

    if (user == null) {
      return SizedBox(
        width: 176,
        child: OutlinedButton.icon(
          onPressed: _showAuthDialog,
          icon: const Icon(Icons.login, size: 16),
          label: const Text('Sign In', style: TextStyle(fontSize: 12)),
          style: OutlinedButton.styleFrom(
            foregroundColor: Colors.white70,
            side: const BorderSide(color: Colors.white24),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          ),
        ),
      );
    }

    return Container(
      width: 176,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFF1E1E28),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.white12),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              CircleAvatar(
                radius: 12,
                backgroundColor: user.isAdmin ? const Color(0xFFFF0000) : const Color(0xFF3EA6FF),
                child: Text(
                  user.username.isNotEmpty ? user.username[0].toUpperCase() : 'U',
                  style: const TextStyle(color: Colors.white, fontSize: 11, fontWeight: FontWeight.bold),
                ),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      user.username,
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12),
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      user.role,
                      style: TextStyle(
                        fontSize: 9,
                        fontWeight: FontWeight.w600,
                        color: user.isAdmin ? const Color(0xFFFF4E4E) : const Color(0xFF3EA6FF),
                      ),
                    ),
                  ],
                ),
              ),
              PopupMenuButton<String>(
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                icon: const Icon(Icons.more_vert, size: 16, color: Colors.grey),
                color: const Color(0xFF20202A),
                onSelected: (val) async {
                  if (val == 'settings') {
                    _navigateToTab(6);
                  } else if (val == 'family') {
                    _navigateToTab(7);
                  } else if (val == 'switch') {
                    _showAuthDialog();
                  } else if (val == 'logout') {
                    await apiService.logout();
                    if (mounted) {
                      setState(() {});
                      _showAuthDialog();
                    }
                  }
                },
                itemBuilder: (ctx) => [
                  const PopupMenuItem(
                    value: 'family',
                    child: Row(
                      children: [
                        Icon(Icons.people, size: 16, color: Color(0xFF3EA6FF)),
                        SizedBox(width: 8),
                        Text('Family Mode', style: TextStyle(fontSize: 13)),
                      ],
                    ),
                  ),
                  const PopupMenuItem(
                    value: 'settings',
                    child: Row(
                      children: [
                        Icon(Icons.settings, size: 16),
                        SizedBox(width: 8),
                        Text('Settings', style: TextStyle(fontSize: 13)),
                      ],
                    ),
                  ),
                  const PopupMenuItem(
                    value: 'switch',
                    child: Row(
                      children: [
                        Icon(Icons.switch_account, size: 16),
                        SizedBox(width: 8),
                        Text('Switch User', style: TextStyle(fontSize: 13)),
                      ],
                    ),
                  ),
                  const PopupMenuItem(
                    value: 'logout',
                    child: Row(
                      children: [
                        Icon(Icons.logout, size: 16, color: Colors.redAccent),
                        SizedBox(width: 8),
                        Text('Log Out', style: TextStyle(fontSize: 13, color: Colors.redAccent)),
                      ],
                    ),
                  ),
                ],
              ),
            ],
          ),
          const SizedBox(height: 6),
          Row(
            children: [
              Icon(
                isYtmConnected ? Icons.check_circle : Icons.circle_outlined,
                size: 10,
                color: isYtmConnected ? Colors.greenAccent : Colors.grey,
              ),
              const SizedBox(width: 4),
              Expanded(
                child: Text(
                  isYtmConnected
                      ? (ytmAccount?.accountName ?? 'YTM Connected')
                      : 'YTM Disconnected',
                  style: TextStyle(
                    fontSize: 10,
                    color: isYtmConnected ? Colors.greenAccent : Colors.grey,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ],
      ),
    );
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
      FamilyView(onNavigateTab: _navigateToTab),
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
            trailing: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12.0, vertical: 12.0),
              child: _buildAccountIndicator(),
            ),
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
              NavigationRailDestination(
                icon: Icon(Icons.people_outline),
                selectedIcon: Icon(Icons.people, color: Color(0xFFFF0000)),
                label: Text('Family Mode'),
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
