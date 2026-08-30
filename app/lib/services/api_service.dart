import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import '../models/models.dart';

class ApiService {
  final String baseUrl;

  ApiService({String? baseUrl})
      : baseUrl = baseUrl ?? (kIsWeb ? Uri.base.origin : 'http://127.0.0.1:8765');

  Future<DashboardStats> fetchDashboardStatus() async {
    final response = await http.get(Uri.parse('$baseUrl/api/status'));
    if (response.statusCode == 200) {
      return DashboardStats.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to load dashboard status: ${response.body}');
  }

  Future<ConnectionStatus> fetchAuthStatus() async {
    final response = await http.get(Uri.parse('$baseUrl/api/auth/status'));
    if (response.statusCode == 200) {
      return ConnectionStatus.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to load auth status');
  }

  Future<ConnectionStatus> setupAuth(String rawHeaders) async {
    final response = await http.post(
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
    final response = await http.post(Uri.parse('$baseUrl/api/auth/test'));
    if (response.statusCode == 200) {
      return ConnectionStatus.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to test auth connection');
  }

  Future<List<String>> getFolders() async {
    final response = await http.get(Uri.parse('$baseUrl/api/folders'));
    if (response.statusCode == 200) {
      return (jsonDecode(response.body) as List<dynamic>).map((e) => e.toString()).toList();
    }
    throw Exception('Failed to get folders');
  }

  Future<void> updateFolders(List<String> folders) async {
    final response = await http.post(
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
    final response = await http.post(
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
    final response = await http.get(uri);
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
    final response = await http.post(
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
    final response = await http.post(Uri.parse('$baseUrl/api/sync'));
    if (response.statusCode != 200) {
      throw Exception('Failed to trigger sync');
    }
  }

  Future<void> uploadSong(int fileId) async {
    final response = await http.post(Uri.parse('$baseUrl/api/upload/$fileId'));
    if (response.statusCode != 200) {
      final err = jsonDecode(response.body);
      throw Exception(err['detail'] ?? 'Failed to enqueue upload');
    }
  }

  Future<int> uploadAllMissing() async {
    final response = await http.post(Uri.parse('$baseUrl/api/upload/all-missing'));
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
    final response = await http.get(uri);
    if (response.statusCode == 200) {
      return UnifiedQueueResponse.fromJson(jsonDecode(response.body));
    }
    throw Exception('Failed to fetch unified queue');
  }

  Future<void> cancelAllQueue() async {
    final response = await http.post(Uri.parse('$baseUrl/api/queue/cancel-all'));
    if (response.statusCode != 200) {
      throw Exception('Failed to cancel queue');
    }
  }

  Future<void> clearCompletedQueue() async {
    final response = await http.post(Uri.parse('$baseUrl/api/queue/clear-completed'));
    if (response.statusCode != 200) {
      throw Exception('Failed to clear completed items');
    }
  }

  Future<List<dynamic>> getNeedsHelpTracks() async {
    final response = await http.get(Uri.parse('$baseUrl/api/needs-help'));
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as List<dynamic>;
    }
    throw Exception('Failed to fetch needs-help tracks');
  }

  Future<void> dismissNeedsHelpTrack(String videoId) async {
    final response = await http.delete(Uri.parse('$baseUrl/api/needs-help/$videoId'));
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
  }) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/needs-help/$videoId/resolve'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'title': title,
        'artist': artist,
        'album': album,
        'thumbnail': thumbnail,
      }),
    );
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    throw Exception('Failed to resolve track: ${response.body}');
  }

  Future<List<SyncJob>> getHistory() async {
    final response = await http.get(Uri.parse('$baseUrl/api/history'));
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((e) => SyncJob.fromJson(e)).toList();
    }
    throw Exception('Failed to fetch history');
  }

  Future<AppSettings> getSettings() async {
    final response = await http.get(Uri.parse('$baseUrl/api/settings'));
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

    final response = await http.post(
      Uri.parse('$baseUrl/api/settings'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode(body),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to update settings');
    }
  }

  Future<String> backupDatabase() async {
    final response = await http.post(Uri.parse('$baseUrl/api/database/backup'));
    if (response.statusCode == 200) {
      final data = jsonDecode(response.body);
      return data['backup_path'] ?? 'Database backup completed';
    }
    throw Exception('Failed to backup database');
  }

  Future<List<YTMPlaylist>> fetchPlaylists() async {
    final response = await http.get(Uri.parse('$baseUrl/api/ytm/playlists'));
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
    final response = await http.get(uri);
    if (response.statusCode == 200) {
      return YTMPlaylistDetails.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
    throw Exception('Failed to fetch playlist details');
  }

  Future<Map<String, dynamic>> syncMissingPlaylistTracks(String playlistId, {String? destinationDir}) async {
    final uri = Uri.parse('$baseUrl/api/ytm/playlists/$playlistId/sync-missing').replace(
      queryParameters: destinationDir != null && destinationDir.isNotEmpty ? {'destination_dir': destinationDir} : null,
    );
    final response = await http.post(uri);
    if (response.statusCode == 200) {
      return jsonDecode(response.body) as Map<String, dynamic>;
    }
    final err = jsonDecode(response.body);
    throw Exception(err['detail'] ?? 'Failed to start playlist sync');
  }

  Future<PlaylistSyncStatusModel> getPlaylistSyncStatus() async {
    final response = await http.get(Uri.parse('$baseUrl/api/ytm/playlists/sync-status'));
    if (response.statusCode == 200) {
      return PlaylistSyncStatusModel.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
    throw Exception('Failed to fetch sync status');
  }

  Future<Map<String, dynamic>> downloadAndUploadPlaylistTrack(Map<String, dynamic> trackData) async {
    final response = await http.post(
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
    final response = await http.post(
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
    final response = await http.get(Uri.parse('$baseUrl/api/folders/stats'));
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
    final response = await http.get(uri);
    if (response.statusCode == 200) {
      return FsBrowseResult.fromJson(jsonDecode(response.body) as Map<String, dynamic>);
    }
    throw Exception('Failed to browse container filesystem');
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
    final response = await http.get(uri);
    if (response.statusCode == 200) {
      final list = jsonDecode(response.body) as List<dynamic>;
      return list.map((item) => MusicBrainzMatch.fromJson(item as Map<String, dynamic>)).toList();
    }
    return [];
  }

  Future<Map<String, int>> getYtmUploadsSummary() async {
    try {
      final response = await http.get(Uri.parse('$baseUrl/api/ytm/uploads/summary'));
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
    final response = await http.get(uri);
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
    final response = await http.post(
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
      final response = await http.get(uri);
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        return data['cover_url'] as String?;
      }
    } catch (_) {}
    return null;
  }

  Future<bool> deleteYtmUpload(String entityId) async {
    final response = await http.delete(Uri.parse('$baseUrl/api/ytm/uploads/$entityId'));
    return response.statusCode == 200;
  }

  Future<Map<String, dynamic>> batchDeleteYtmUploads(List<String> entityIds) async {
    final response = await http.post(
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
    final response = await http.post(
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
    final response = await http.post(
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
}

final apiService = ApiService();
