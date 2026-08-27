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

  Future<void> triggerScan() async {
    final response = await http.post(Uri.parse('$baseUrl/api/scan'));
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
}

final apiService = ApiService();
