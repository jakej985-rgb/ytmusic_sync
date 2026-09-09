import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';
import '../models/models.dart';

class ApiService {
  final String baseUrl;
  final http.Client _client;
  String? _apiKey;
  User? _currentUser;
  YouTubeMusicAccount? _ytmAccount;
  VoidCallback? onUnauthorized;

  ApiService({String? baseUrl, http.Client? client})
      : baseUrl = baseUrl ?? (kIsWeb ? Uri.base.origin : 'http://127.0.0.1:8765'),
        _client = client ?? http.Client();

  bool _isUnauthorized = false;

  String? get apiKey => _apiKey;
  bool get isUnauthorized => _isUnauthorized;
  User? get currentUser => _currentUser;
  YouTubeMusicAccount? get ytmAccount => _ytmAccount;

  Future<void> initApiKey() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final sessionToken = prefs.getString('ytm_sync_session_token');
      final apiKey = prefs.getString('ytm_sync_api_key');
      _apiKey = (sessionToken != null && sessionToken.isNotEmpty) ? sessionToken : apiKey;
      _isUnauthorized = false;
      if (_apiKey != null && _apiKey!.isNotEmpty) {
        try {
          await fetchCurrentUser();
          await fetchYtmAccount();
        } catch (_) {}
      }
    } catch (_) {}
  }

  Future<void> setApiKey(String? key) async {
    _apiKey = key?.trim();
    if (_apiKey != null && _apiKey!.isNotEmpty) {
      _isUnauthorized = false;
    } else {
      _currentUser = null;
      _ytmAccount = null;
    }
    try {
      final prefs = await SharedPreferences.getInstance();
      if (_apiKey == null || _apiKey!.isEmpty) {
        await prefs.remove('ytm_sync_api_key');
        await prefs.remove('ytm_sync_session_token');
      } else {
        await prefs.setString('ytm_sync_api_key', _apiKey!);
      }
    } catch (_) {}
  }

  Future<UserLoginResponse> login(String username, String password) async {
    final response = await _client.post(
      Uri.parse('$baseUrl/api/auth/login'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'username': username, 'password': password}),
    );
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      final loginResp = UserLoginResponse.fromJson(data);
      _apiKey = loginResp.token;
      _currentUser = loginResp.user;
      _isUnauthorized = false;
      try {
        final prefs = await SharedPreferences.getInstance();
        await prefs.setString('ytm_sync_session_token', loginResp.token);
      } catch (_) {}
      try {
        await fetchYtmAccount();
      } catch (_) {}
      return loginResp;
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Login failed');
  }

  Future<void> logout() async {
    if (_apiKey != null && _apiKey!.isNotEmpty) {
      try {
        await _client.post(
          Uri.parse('$baseUrl/api/auth/logout'),
          headers: _buildHeaders({'Content-Type': 'application/json'}),
        );
      } catch (_) {}
    }
    _apiKey = null;
    _currentUser = null;
    _ytmAccount = null;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove('ytm_sync_session_token');
      await prefs.remove('ytm_sync_api_key');
    } catch (_) {}
  }

  Future<User?> fetchCurrentUser() async {
    try {
      final response = await _get(Uri.parse('$baseUrl/api/auth/me'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _currentUser = User.fromJson(data);
        return _currentUser;
      }
    } catch (_) {}
    return null;
  }

  Future<YouTubeMusicAccount?> fetchYtmAccount() async {
    try {
      final response = await _get(Uri.parse('$baseUrl/api/ytm/account'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        _ytmAccount = YouTubeMusicAccount.fromJson(data);
        return _ytmAccount;
      }
    } catch (_) {}
    return null;
  }

  Future<List<User>> getUsers() async {
    final response = await _get(Uri.parse('$baseUrl/api/admin/users'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((u) => User.fromJson(u as Map<String, dynamic>)).toList();
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to fetch users');
  }

  Future<User> createUser(String username, String password, {String role = 'USER'}) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/admin/users'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'username': username,
        'password': password,
        'role': role,
      }),
    );
    if (response.statusCode == 201 || response.statusCode == 200) {
      return User.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to create user');
  }

  Future<User> updateUser(String userId, {String? password, String? role, bool? isActive}) async {
    final body = <String, dynamic>{};
    if (password != null && password.isNotEmpty) body['password'] = password;
    if (role != null) body['role'] = role;
    if (isActive != null) body['is_active'] = isActive;

    final response = await _put(
      Uri.parse('$baseUrl/api/admin/users/$userId'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(body),
    );
    if (response.statusCode == 200) {
      return User.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to update user');
  }

  Future<void> deleteUser(String userId) async {
    final response = await _delete(Uri.parse('$baseUrl/api/admin/users/$userId?confirm=true'));
    if (response.statusCode != 204 && response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to delete user');
    }
  }

  Map<String, String> _buildHeaders([Map<String, String>? base]) {
    final headers = <String, String>{};
    if (base != null) {
      headers.addAll(base);
    }
    if (_apiKey != null && _apiKey!.isNotEmpty) {
      headers['Authorization'] = 'Bearer $_apiKey';
    }
    return headers;
  }

  void _checkResponse(http.Response response) {
    if (response.statusCode == 401) {
      _isUnauthorized = true;
      setApiKey(null);
      onUnauthorized?.call();
      throw Exception('Unauthorized: Invalid or missing API key');
    }
  }

  Future<http.Response> _get(Uri uri, {Map<String, String>? headers}) async {
    if (_isUnauthorized) {
      throw Exception('Unauthorized: Invalid or missing API key');
    }
    final response = await _client.get(uri, headers: _buildHeaders(headers));
    _checkResponse(response);
    return response;
  }

  Future<http.Response> _post(Uri uri, {Map<String, String>? headers, Object? body, Encoding? encoding}) async {
    if (_isUnauthorized) {
      throw Exception('Unauthorized: Invalid or missing API key');
    }
    final response = await _client.post(uri, headers: _buildHeaders(headers), body: body, encoding: encoding);
    _checkResponse(response);
    return response;
  }

  Future<http.Response> _put(Uri uri, {Map<String, String>? headers, Object? body, Encoding? encoding}) async {
    if (_isUnauthorized) {
      throw Exception('Unauthorized: Invalid or missing API key');
    }
    final response = await _client.put(uri, headers: _buildHeaders(headers), body: body, encoding: encoding);
    _checkResponse(response);
    return response;
  }

  Future<http.Response> _delete(Uri uri, {Map<String, String>? headers, Object? body, Encoding? encoding}) async {
    if (_isUnauthorized) {
      throw Exception('Unauthorized: Invalid or missing API key');
    }
    final response = await _client.delete(uri, headers: _buildHeaders(headers), body: body, encoding: encoding);
    _checkResponse(response);
    return response;
  }

  Future<http.Response> _patch(Uri uri, {Map<String, String>? headers, Object? body, Encoding? encoding}) async {
    if (_isUnauthorized) {
      throw Exception('Unauthorized: Invalid or missing API key');
    }
    final response = await _client.patch(uri, headers: _buildHeaders(headers), body: body, encoding: encoding);
    _checkResponse(response);
    return response;
  }

  Future<DashboardStats> fetchDashboardStatus() async {
    final response = await _get(Uri.parse('$baseUrl/api/status'));
    if (response.statusCode == 200) {
      return DashboardStats.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to load dashboard status: ${response.body}');
  }

  Future<ConnectionStatus> fetchAuthStatus() async {
    final response = await _get(Uri.parse('$baseUrl/api/auth/status'));
    if (response.statusCode == 200) {
      return ConnectionStatus.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to load auth status');
  }

  Future<AuthSessionInfo> startAuth({String? originUrl}) async {
    final body = originUrl != null ? jsonEncode({'origin_url': originUrl}) : null;
    final response = await _post(
      Uri.parse('$baseUrl/api/auth/start'),
      headers: {'Content-Type': 'application/json'},
      body: body,
    );
    if (response.statusCode == 200) {
      return AuthSessionInfo.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to start auth session');
  }

  Future<AuthSessionInfo> getAuthSession(String sessionId) async {
    final response = await _get(Uri.parse('$baseUrl/api/auth/session/$sessionId'));
    if (response.statusCode == 200) {
      return AuthSessionInfo.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to get auth session');
  }

  Future<AuthSessionInfo> cancelAuthSession(String sessionId) async {
    final response = await _post(Uri.parse('$baseUrl/api/auth/session/$sessionId/cancel'));
    if (response.statusCode == 200) {
      return AuthSessionInfo.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to cancel auth session');
  }

  Future<ConnectionStatus> disconnectAuth() async {
    final response = await _post(Uri.parse('$baseUrl/api/auth/disconnect'));
    if (response.statusCode == 200) {
      return ConnectionStatus.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to disconnect account');
  }

  Future<ConnectionStatus> setupAuth(String rawHeaders) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/auth/setup'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'raw_headers': rawHeaders}),
    );
    if (response.statusCode == 200) {
      return ConnectionStatus.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to setup auth');
  }

  Future<ConnectionStatus> testAuth() async {
    final response = await _post(Uri.parse('$baseUrl/api/auth/test'));
    if (response.statusCode == 200) {
      return ConnectionStatus.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to test auth connection');
  }

  Future<List<String>> getFolders() async {
    final response = await _get(Uri.parse('$baseUrl/api/folders'));
    if (response.statusCode == 200) {
      return (jsonDecode(response.body) as List<dynamic>).map((e) => e.toString()).toList();
    }
    throw Exception('Failed to get folders');
  }

  Future<void> updateFolders(List<String> folders) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/folders'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'folders': folders}),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to update folders');
    }
  }

  Future<void> triggerScan([List<String>? folders]) async {
    final body = folders != null ? jsonEncode({'folders': folders}) : null;
    final headers = folders != null ? {'Content-Type': 'application/json'} : null;
    final response = await _post(
      Uri.parse('$baseUrl/api/scan'),
      headers: headers,
      body: body,
    );
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to trigger scan');
    }
  }

  Future<List<MusicFile>> getSongs({
    String? status,
    String? search,
    int limit = 200,
    int offset = 0,
  }) async {
    final queryParams = <String, String>{
      'limit': limit.toString(),
      'offset': offset.toString(),
    };
    if (status != null && status.isNotEmpty && status != 'all') {
      queryParams['status'] = status;
    }
    if (search != null && search.isNotEmpty) {
      queryParams['search'] = search;
    }

    final uri = Uri.parse('$baseUrl/api/songs').replace(queryParameters: queryParams);
    final response = await _get(uri);
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => MusicFile.fromJson(e)).toList();
    }
    throw Exception('Failed to fetch songs');
  }

  Future<MusicFile> updateSongMetadata(
    int fileId, {
    required String title,
    String? artist,
    String? album,
    int? trackNumber,
    String? coverUrl,
  }) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/songs/$fileId/metadata'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'title': title,
        'artist': (artist != null && artist.isNotEmpty) ? artist : null,
        'album': (album != null && album.isNotEmpty) ? album : null,
        'track_number': trackNumber,
        'cover_url': (coverUrl != null && coverUrl.isNotEmpty) ? coverUrl : null,
      }),
    );
    if (response.statusCode == 200) {
      return MusicFile.fromJson(jsonDecode(response.body));
    }
    try {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to update metadata');
    } catch (e) {
      if (e is Exception && !e.toString().contains('FormatException')) {
        rethrow;
      }
      throw Exception('Server error (${response.statusCode}): ${response.body}');
    }
  }

  Future<void> triggerSync() async {
    final response = await _post(Uri.parse('$baseUrl/api/sync'));
    if (response.statusCode != 200) {
      throw Exception('Failed to trigger sync');
    }
  }

  Future<void> uploadSong(int fileId) async {
    final response = await _post(Uri.parse('$baseUrl/api/upload/$fileId'));
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to enqueue upload');
    }
  }

  Future<int> uploadAllMissing() async {
    final response = await _post(Uri.parse('$baseUrl/api/upload/all-missing'));
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      return data['enqueued_count'] ?? 0;
    }
    throw Exception('Failed to enqueue all missing songs');
  }

  Future<UnifiedQueueResponse> getUnifiedQueue({
    String category = 'all',
    String status = 'all',
    int limit = 200,
  }) async {
    final uri = Uri.parse('$baseUrl/api/queue').replace(queryParameters: {
      'category': category,
      'status': status,
      'limit': limit.toString(),
    });
    final response = await _get(uri);
    if (response.statusCode == 200) {
      return UnifiedQueueResponse.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to fetch unified queue');
  }

  Future<void> cancelAllQueue() async {
    final response = await _post(Uri.parse('$baseUrl/api/queue/cancel-all'));
    if (response.statusCode != 200) {
      throw Exception('Failed to cancel queue');
    }
  }

  Future<void> clearCompletedQueue() async {
    final response = await _post(Uri.parse('$baseUrl/api/queue/clear-completed'));
    if (response.statusCode != 200) {
      throw Exception('Failed to clear completed items');
    }
  }

  Future<List<dynamic>> getNeedsHelpTracks() async {
    final response = await _get(Uri.parse('$baseUrl/api/needs-help'));
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as List<dynamic>;
    }
    throw Exception('Failed to fetch needs-help tracks');
  }

  Future<void> dismissNeedsHelpTrack(String videoId) async {
    final response = await _delete(Uri.parse('$baseUrl/api/needs-help/$videoId'));
    if (response.statusCode != 200) {
      throw Exception('Failed to dismiss track');
    }
  }

  Future<Map<String, dynamic>> resolveNeedsHelpTrack(
    String videoId, {
    required String title,
    String? artist,
    String? album,
    String? thumbnail,
    String? destinationDir,
  }) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/needs-help/$videoId/resolve'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'title': title,
        'artist': artist,
        'album': album,
        'thumbnail': thumbnail,
        'destination_dir': destinationDir,
      }),
    );
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    throw Exception('Failed to resolve track: ${response.body}');
  }

  Future<List<SyncJob>> getHistory() async {
    final response = await _get(Uri.parse('$baseUrl/api/history'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => SyncJob.fromJson(e)).toList();
    }
    throw Exception('Failed to fetch history');
  }

  Future<AppSettings> getSettings() async {
    final response = await _get(Uri.parse('$baseUrl/api/settings'));
    if (response.statusCode == 200) {
      return AppSettings.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to fetch settings');
  }

  Future<void> updateSettings({
    bool? autoUpload,
    int? scanIntervalMinutes,
    bool? verifyUploads,
  }) async {
    final body = <String, dynamic>{};
    if (autoUpload != null) body['auto_upload'] = autoUpload;
    if (scanIntervalMinutes != null) body['scan_interval_minutes'] = scanIntervalMinutes;
    if (verifyUploads != null) body['verify_uploads'] = verifyUploads;

    final response = await _post(
      Uri.parse('$baseUrl/api/settings'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(body),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to update settings');
    }
  }

  Future<String> backupDatabase() async {
    final response = await _post(Uri.parse('$baseUrl/api/database/backup'));
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      return data['backup_path'] ?? 'Database backup completed';
    }
    throw Exception('Failed to backup database');
  }

  Future<List<YTMPlaylist>> fetchPlaylists({String? userId}) async {
    final uri = Uri.parse('$baseUrl/api/ytm/playlists').replace(
      queryParameters: userId != null && userId.isNotEmpty ? {'user_id': userId} : null,
    );
    final response = await _get(uri);
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((item) => YTMPlaylist.fromJson(item as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to fetch playlists');
  }

  Future<YTMPlaylistDetails> fetchPlaylistDetails(String playlistId, {bool refresh = false}) async {
    final uri = Uri.parse('$baseUrl/api/ytm/playlists/$playlistId').replace(
      queryParameters: refresh ? {'refresh': 'true'} : null,
    );
    final response = await _get(uri);
    if (response.statusCode == 200) {
      return YTMPlaylistDetails.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
    throw Exception('Failed to fetch playlist details');
  }

  Future<Map<String, dynamic>> syncMissingPlaylistTracks(
    String playlistId, {
    String? destinationDir,
    List<String>? destinationUserIds,
  }) async {
    final uri = Uri.parse('$baseUrl/api/ytm/playlists/$playlistId/sync-missing');
    final Map<String, dynamic> body = {};
    if (destinationDir != null && destinationDir.isNotEmpty) {
      body['destination_dir'] = destinationDir;
    }
    if (destinationUserIds != null && destinationUserIds.isNotEmpty) {
      body['destination_user_ids'] = destinationUserIds;
    }
    final response = await _post(
      uri,
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(body),
    );
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to start playlist sync');
  }

  Future<PlaylistSyncStatusModel> getPlaylistSyncStatus() async {
    final response = await _get(Uri.parse('$baseUrl/api/ytm/playlists/sync-status'));
    if (response.statusCode == 200) {
      return PlaylistSyncStatusModel.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
    throw Exception('Failed to fetch sync status');
  }

  Future<PlaylistSyncStatusModel> cancelPlaylistSync() async {
    final response = await _post(Uri.parse('$baseUrl/api/ytm/playlists/cancel-sync'));
    if (response.statusCode == 200) {
      return PlaylistSyncStatusModel.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
    throw Exception('Failed to cancel sync');
  }

  Future<Map<String, dynamic>> downloadAndUploadPlaylistTrack(Map<String, dynamic> trackData) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/ytm/playlists/download-track'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(trackData),
    );
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to download and upload track');
  }

  Future<YTMPlaylistDetails> importPlaylistUrl(String url) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/ytm/playlists/import-url'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'url': url}),
    );
    if (response.statusCode == 200) {
      return YTMPlaylistDetails.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to import playlist URL');
  }

  Future<List<RootFolderStats>> fetchFolderStats() async {
    final response = await _get(Uri.parse('$baseUrl/api/folders/stats'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((item) => RootFolderStats.fromJson(item as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to fetch root folder statistics');
  }

  Future<FsBrowseResult> browseFilesystem([String? path]) async {
    final uri = Uri.parse('$baseUrl/api/fs/browse').replace(
      queryParameters: path != null && path.isNotEmpty ? {'path': path} : null,
    );
    final response = await _get(uri);
    if (response.statusCode == 200) {
      return FsBrowseResult.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to browse container filesystem');
  }

  Future<List<MusicBrainzMatch>> searchMusicBrainz({
    String? query,
    String? artist,
    String? title,
    String? provider,
    int limit = 6,
  }) async {
    final Map<String, String> queryParams = {'limit': limit.toString()};
    if (query != null && query.isNotEmpty) queryParams['query'] = query;
    if (artist != null && artist.isNotEmpty) queryParams['artist'] = artist;
    if (title != null && title.isNotEmpty) queryParams['title'] = title;
    if (provider != null && provider.isNotEmpty && provider != 'all') {
      queryParams['provider'] = provider;
    }

    final uri = Uri.parse('$baseUrl/api/musicbrainz/search').replace(queryParameters: queryParams);
    final response = await _get(uri);
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((item) => MusicBrainzMatch.fromJson(item as Map<String, dynamic>)).toList();
    }
    return [];
  }

  Future<Map<String, int>> getYtmUploadsSummary() async {
    try {
      final response = await _get(Uri.parse('$baseUrl/api/ytm/uploads/summary'));
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body) as Map<String, dynamic>;
        return {
          'total': (data['total'] as num?)?.toInt() ?? 0,
          'missing_metadata': (data['missing_metadata'] as num?)?.toInt() ?? 0,
          'proper': (data['proper'] as num?)?.toInt() ?? 0,
        };
      }
    } catch (_) {}
    return {'total': 0, 'missing_metadata': 0, 'proper': 0};
  }

  Future<Map<String, dynamic>> getYtmUploads({
    String filterType = 'all',
    String? search,
    int page = 1,
    int pageSize = 50,
  }) async {
    final Map<String, String> queryParams = {
      'filter_type': filterType,
      'page': page.toString(),
      'page_size': pageSize.toString(),
    };
    if (search != null && search.trim().isNotEmpty) {
      queryParams['search'] = search.trim();
    }

    final uri = Uri.parse('$baseUrl/api/ytm/uploads').replace(queryParameters: queryParams);
    final response = await _get(uri);
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body) as Map<String, dynamic>;
      final items = (data['items'] as List<dynamic>)
          .map((item) => YtmUpload.fromJson(item as Map<String, dynamic>))
          .toList();
      return {
        'items': items,
        'total': (data['total'] as num?)?.toInt() ?? 0,
        'page': (data['page'] as num?)?.toInt() ?? 1,
        'page_size': (data['page_size'] as num?)?.toInt() ?? 50,
        'total_pages': (data['total_pages'] as num?)?.toInt() ?? 1,
      };
    }
    return {'items': <YtmUpload>[], 'total': 0, 'page': 1, 'page_size': 50, 'total_pages': 1};
  }

  Future<Map<String, dynamic>> replaceYtmUpload(
    String entityId, {
    required String title,
    String? artist,
    String? album,
    int? trackNumber,
    String? coverUrl,
  }) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/ytm/uploads/$entityId/replace'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'title': title,
        'artist': artist,
        'album': album,
        'track_number': trackNumber,
        'cover_url': (coverUrl != null && coverUrl.isNotEmpty) ? coverUrl : null,
      }),
    );

    if (response.statusCode == 200) {
      return {'success': true, 'message': 'Upload successfully replaced'};
    }
    String error = 'Failed to replace upload';
    try {
      final err = jsonDecode(response.body);
      if (err is Map && err['detail'] != null) {
        error = err['detail'].toString();
      }
    } catch (_) {}
    return {'success': false, 'error': error};
  }

  Future<String?> fetchCoverArtUrl({
    required String artist,
    String? title,
    String? album,
  }) async {
    try {
      final Map<String, String> queryParams = {'artist': artist};
      if (title != null && title.isNotEmpty) queryParams['title'] = title;
      if (album != null && album.isNotEmpty) queryParams['album'] = album;
      final uri = Uri.parse('$baseUrl/api/metadata/cover-art').replace(queryParameters: queryParams);
      final response = await _get(uri);
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        return data['cover_url'] as String?;
      }
    } catch (_) {}
    return null;
  }

  Future<bool> deleteYtmUpload(String entityId) async {
    final response = await _delete(Uri.parse('$baseUrl/api/ytm/uploads/$entityId'));
    return response.statusCode == 200;
  }

  Future<Map<String, dynamic>> batchDeleteYtmUploads(List<String> entityIds) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/ytm/uploads/batch-delete'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'entity_ids': entityIds}),
    );
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    throw Exception('Failed to batch delete uploads');
  }

  Future<int> batchUploadSongs(List<int> fileIds) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/upload/batch'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'file_ids': fileIds}),
    );
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      return data['enqueued_count'] ?? 0;
    }
    throw Exception('Failed to batch upload songs');
  }

  Future<int> batchDeleteSongs(List<int> fileIds) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/songs/batch-delete'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'file_ids': fileIds}),
    );
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      return data['deleted'] ?? 0;
    }
    throw Exception('Failed to batch delete songs');
  }

  // ---------------------------------------------------------------------------
  // Replicated Playlists (1:1 Locker-Only Replica)
  // ---------------------------------------------------------------------------

  Future<List<ReplicatedPlaylistModel>> fetchReplicatedPlaylists({bool enabledOnly = false}) async {
    final uri = Uri.parse('$baseUrl/api/replicated-playlists?enabled_only=$enabledOnly');
    final response = await _get(uri);
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => ReplicatedPlaylistModel.fromJson(e as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to fetch replicated playlists');
  }

  Future<ReplicationPreviewModel> fetchReplicatedPlaylist(int id) async {
    final uri = Uri.parse('$baseUrl/api/replicated-playlists/$id');
    final response = await _get(uri);
    if (response.statusCode == 200) {
      return ReplicationPreviewModel.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to fetch replicated playlist #$id');
  }

  Future<ReplicatedPlaylistModel> createReplicatedPlaylist(Map<String, dynamic> data) async {
    final uri = Uri.parse('$baseUrl/api/replicated-playlists');
    final response = await _post(
      uri,
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(data),
    );
    if (response.statusCode == 200) {
      return ReplicatedPlaylistModel.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to create replicated playlist: ${response.body}');
  }

  Future<ReplicationPreviewModel> syncReplicatedPlaylist(int id) async {
    final uri = Uri.parse('$baseUrl/api/replicated-playlists/$id/sync');
    final response = await _post(uri);
    if (response.statusCode == 200) {
      return ReplicationPreviewModel.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to sync replicated playlist #$id: ${response.body}');
  }

  Future<ReplicationPreviewModel> dryRunReplicatedPlaylist(int id) async {
    final uri = Uri.parse('$baseUrl/api/replicated-playlists/$id/dry-run');
    final response = await _post(uri);
    if (response.statusCode == 200) {
      return ReplicationPreviewModel.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to dry-run replicated playlist #$id: ${response.body}');
  }

  Future<void> deleteReplicatedPlaylist(int id) async {
    final uri = Uri.parse('$baseUrl/api/replicated-playlists/$id');
    final response = await _delete(uri);
    if (response.statusCode != 200) {
      throw Exception('Failed to delete replicated playlist #$id');
    }
  }

  // ---------------------------------------------------------------------------
  // Family Mode & Multi-Account Methods (Sections 1-40)
  // ---------------------------------------------------------------------------

  Future<List<Family>> getFamilies() async {
    final response = await _get(Uri.parse('$baseUrl/api/families'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => Family.fromJson(e as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to fetch families: ${response.body}');
  }

  Future<Family> createFamily(String name) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/families'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'name': name}),
    );
    if (response.statusCode == 201 || response.statusCode == 200) {
      return Family.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to create family');
  }

  Future<Family> getFamily(String familyId) async {
    final response = await _get(Uri.parse('$baseUrl/api/families/$familyId'));
    if (response.statusCode == 200) {
      return Family.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to fetch family: ${response.body}');
  }

  Future<Family> updateFamily(String familyId, String name) async {
    final response = await _patch(
      Uri.parse('$baseUrl/api/families/$familyId'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'name': name}),
    );
    if (response.statusCode == 200) {
      return Family.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to update family');
  }

  Future<void> transferFamilyOwnership(String familyId, String newOwnerUserId, {bool confirm = true}) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/families/$familyId/transfer'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'new_owner_user_id': newOwnerUserId,
        'confirm': confirm,
      }),
    );
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to transfer ownership');
    }
  }

  Future<void> leaveFamily(String familyId) async {
    final response = await _post(Uri.parse('$baseUrl/api/families/$familyId/leave'));
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to leave family');
    }
  }

  Future<void> deleteFamily(String familyId) async {
    final response = await _delete(Uri.parse('$baseUrl/api/families/$familyId'));
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to delete family');
    }
  }

  Future<FamilyDashboardResponse> getFamilyDashboard(String familyId) async {
    final response = await _get(Uri.parse('$baseUrl/api/families/$familyId/dashboard'));
    if (response.statusCode == 200) {
      return FamilyDashboardResponse.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to load family dashboard');
  }

  Future<List<SelectableAccountItem>> getSelectableAccounts() async {
    final response = await _get(Uri.parse('$baseUrl/api/accounts'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => SelectableAccountItem.fromJson(e as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to fetch selectable accounts: ${response.body}');
  }

  Future<UploadDestinationResponse> uploadToDestinations(
    List<int> musicFileIds,
    List<String> destinationUserIds, {
    String? familyId,
  }) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/uploads/destinations'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'music_file_ids': musicFileIds,
        'destination_user_ids': destinationUserIds,
        'family_id': ?familyId,
      }),
    );
    if (response.statusCode == 200) {
      return UploadDestinationResponse.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to queue uploads');
  }

  Future<List<TrackDestinationDuplicateStatus>> getTrackDestinationsStatus(int fileId) async {
    final response = await _get(Uri.parse('$baseUrl/api/tracks/$fileId/destinations-status'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => TrackDestinationDuplicateStatus.fromJson(e as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to load track destinations status: ${response.body}');
  }

  Future<List<FamilyQueueItem>> getFamilyQueue(String familyId) async {
    final response = await _get(Uri.parse('$baseUrl/api/families/$familyId/queue'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => FamilyQueueItem.fromJson(e as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to load family queue: ${response.body}');
  }

  Future<List<FamilyUploadHistoryItem>> getFamilyHistory(String familyId, {int limit = 50}) async {
    final response = await _get(Uri.parse('$baseUrl/api/families/$familyId/history?limit=$limit'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => FamilyUploadHistoryItem.fromJson(e as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to load family history: ${response.body}');
  }

  Future<Map<String, dynamic>> triggerFamilySync(String familyId) async {
    final response = await _post(Uri.parse('$baseUrl/api/families/$familyId/sync'));
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to trigger family sync');
  }

  Future<List<FamilyPlaylistItem>> getFamilyPlaylists(String familyId) async {
    final response = await _get(Uri.parse('$baseUrl/api/families/$familyId/playlists'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => FamilyPlaylistItem.fromJson(e as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to load family playlists: ${response.body}');
  }

  Future<Map<String, dynamic>> createMultiPlaylists(
    String familyId,
    String title,
    List<String> targetUserIds, {
    String? description,
    List<String>? videoIds,
    String? sourcePlaylistId,
    String? sourceUserId,
    bool uploadMissingToTargets = false,
  }) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/families/$familyId/playlists/multi'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'name': title,
        'title': title,
        'destination_user_ids': targetUserIds,
        'target_user_ids': targetUserIds,
        'description': ?description,
        'video_ids': ?videoIds,
        'source_playlist_id': ?sourcePlaylistId,
        'source_user_id': ?sourceUserId,
        'upload_missing_to_targets': uploadMissingToTargets,
      }),
    );
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    String errorMsg = 'Failed to create multi-account playlists';
    try {
      final err = jsonDecode(response.body);
      if (err is Map && err['detail'] != null) {
        errorMsg = err['detail'].toString();
      }
    } catch (_) {
      if (response.body.isNotEmpty) {
        errorMsg = response.body;
      }
    }
    throw Exception(errorMsg);
  }

  Future<FamilyInvitation> createFamilyInvitation(
    String familyId, {
    int expiryHours = 48,
    int maxUses = 1,
  }) async {
    final response = await _post(
      Uri.parse('$baseUrl/api/families/$familyId/invitations'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'expiry_hours': expiryHours,
        'max_uses': maxUses,
      }),
    );
    if (response.statusCode == 201 || response.statusCode == 200) {
      return FamilyInvitation.fromJson(jsonDecode(response.body));
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to create invitation');
  }

  Future<List<FamilyInvitation>> listFamilyInvitations(String familyId) async {
    final response = await _get(Uri.parse('$baseUrl/api/families/$familyId/invitations'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => FamilyInvitation.fromJson(e as Map<String, dynamic>)).toList();
    }
    throw Exception('Failed to list invitations: ${response.body}');
  }

  Future<void> revokeFamilyInvitation(String familyId, String invitationId) async {
    final response = await _delete(Uri.parse('$baseUrl/api/families/$familyId/invitations/$invitationId'));
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to revoke invitation');
    }
  }

  Future<Map<String, dynamic>> getInvitationInfo(String token) async {
    final response = await _get(Uri.parse('$baseUrl/api/invitations/$token'));
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Invalid invitation');
  }

  Future<void> acceptFamilyInvitation(String token) async {
    final response = await _post(Uri.parse('$baseUrl/api/invitations/$token/accept'));
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to accept invitation');
    }
  }

  Future<void> updateFamilyMemberPrivacy(
    String familyId,
    String userId, {
    bool? showAccountInFamily,
    bool? allowFamilyUploads,
    bool? allowFamilyPlaylists,
    bool? allowFamilySync,
  }) async {
    final body = <String, dynamic>{};
    if (showAccountInFamily != null) body['show_account_in_family'] = showAccountInFamily;
    if (allowFamilyUploads != null) body['allow_family_uploads'] = allowFamilyUploads;
    if (allowFamilyPlaylists != null) body['allow_family_playlists'] = allowFamilyPlaylists;
    if (allowFamilySync != null) body['allow_family_sync'] = allowFamilySync;

    final response = await _patch(
      Uri.parse('$baseUrl/api/families/$familyId/members/$userId/permissions'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(body),
    );
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to update member privacy');
    }
  }

  Future<void> updateFamilyMemberRole(String familyId, String userId, String role) async {
    final response = await _put(
      Uri.parse('$baseUrl/api/families/$familyId/members/$userId/role'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'role': role}),
    );
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to update member role');
    }
  }

  Future<void> removeFamilyMember(String familyId, String userId) async {
    final response = await _delete(Uri.parse('$baseUrl/api/families/$familyId/members/$userId'));
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to remove member');
    }
  }
}

final apiService = ApiService();

