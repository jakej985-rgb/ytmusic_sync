export 'auth_state.dart';

class DashboardStats {
  final bool ytmConnected;
  final String? accountName;
  final int localSongsCount;
  final int ytmUploadsCount;
  final int missingCount;
  final int uploadedCount;
  final int failedCount;
  final int inQueueCount;
  final bool isScanning;
  final bool isUploading;

  DashboardStats({
    required this.ytmConnected,
    this.accountName,
    required this.localSongsCount,
    required this.ytmUploadsCount,
    required this.missingCount,
    required this.uploadedCount,
    required this.failedCount,
    required this.inQueueCount,
    required this.isScanning,
    required this.isUploading,
  });

  factory DashboardStats.fromJson(Map<String, dynamic> json) {
    return DashboardStats(
      ytmConnected: json['ytm_connected'] ?? false,
      accountName: json['account_name'],
      localSongsCount: json['local_songs_count'] ?? 0,
      ytmUploadsCount: json['ytm_uploads_count'] ?? 0,
      missingCount: json['missing_count'] ?? 0,
      uploadedCount: json['uploaded_count'] ?? 0,
      failedCount: json['failed_count'] ?? 0,
      inQueueCount: json['in_queue_count'] ?? 0,
      isScanning: json['is_scanning'] ?? false,
      isUploading: json['is_uploading'] ?? false,
    );
  }
}

class MusicFile {
  final int? id;
  final String path;
  final String filename;
  final String? artist;
  final String? album;
  final String? title;
  final int? trackNumber;
  final double? duration;
  final String format;
  final int fileSize;
  final String uploadStatus;
  final String? matchedUploadId;
  final double? matchScore;

  MusicFile({
    this.id,
    required this.path,
    required this.filename,
    this.artist,
    this.album,
    this.title,
    this.trackNumber,
    this.duration,
    required this.format,
    required this.fileSize,
    required this.uploadStatus,
    this.matchedUploadId,
    this.matchScore,
  });

  factory MusicFile.fromJson(Map<String, dynamic> json) {
    return MusicFile(
      id: json['id'],
      path: json['path'] ?? '',
      filename: json['filename'] ?? '',
      artist: json['artist'],
      album: json['album'],
      title: json['title'],
      trackNumber: json['track_number'],
      duration: json['duration'] != null ? (json['duration'] as num).toDouble() : null,
      format: json['format'] ?? '',
      fileSize: json['file_size'] ?? 0,
      uploadStatus: json['upload_status'] ?? 'not_uploaded',
      matchedUploadId: json['matched_upload_id'],
      matchScore: json['match_score'] != null ? (json['match_score'] as num).toDouble() : null,
    );
  }

  String get displayTitle => (title != null && title!.isNotEmpty) ? title! : filename;
  String get displayArtist => (artist != null && artist!.isNotEmpty) ? artist! : 'Unknown Artist';
  String get displayAlbum => (album != null && album!.isNotEmpty) ? album! : 'Unknown Album';
  
  String get formattedDuration {
    if (duration == null || duration! <= 0) return '--:--';
    final totalSec = duration!.round();
    final mins = totalSec ~/ 60;
    final secs = totalSec % 60;
    return '$mins:${secs.toString().padLeft(2, '0')}';
  }

  String get formattedSize {
    if (fileSize < 1024 * 1024) {
      return '${(fileSize / 1024).toStringAsFixed(1)} KB';
    }
    return '${(fileSize / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
}

class SyncJob {
  final int? id;
  final int musicFileId;
  final String status;
  final String? startedAt;
  final String? completedAt;
  final String? error;
  final int attempts;
  final String? ytmEntityId;
  final MusicFile? musicFile;

  SyncJob({
    this.id,
    required this.musicFileId,
    required this.status,
    this.startedAt,
    this.completedAt,
    this.error,
    required this.attempts,
    this.ytmEntityId,
    this.musicFile,
  });

  factory SyncJob.fromJson(Map<String, dynamic> json) {
    return SyncJob(
      id: json['id'],
      musicFileId: json['music_file_id'] ?? 0,
      status: json['status'] ?? 'queued',
      startedAt: json['started_at'],
      completedAt: json['completed_at'],
      error: json['error'],
      attempts: json['attempts'] ?? 0,
      ytmEntityId: json['ytm_entity_id'],
      musicFile: json['music_file'] != null ? MusicFile.fromJson(json['music_file']) : null,
    );
  }
}

class ConnectionStatus {
  final bool connected;
  final String message;
  final String? userName;

  ConnectionStatus({
    required this.connected,
    required this.message,
    this.userName,
  });

  factory ConnectionStatus.fromJson(Map<String, dynamic> json) {
    return ConnectionStatus(
      connected: json['connected'] ?? false,
      message: json['message'] ?? '',
      userName: json['user_name'],
    );
  }
}

class AppSettings {
  final List<String> musicFolders;
  final bool autoUpload;
  final int scanIntervalMinutes;
  final bool verifyUploads;

  AppSettings({
    required this.musicFolders,
    required this.autoUpload,
    required this.scanIntervalMinutes,
    required this.verifyUploads,
  });

  factory AppSettings.fromJson(Map<String, dynamic> json) {
    return AppSettings(
      musicFolders: (json['music_folders'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? [],
      autoUpload: json['auto_upload'] ?? false,
      scanIntervalMinutes: json['scan_interval_minutes'] ?? 15,
      verifyUploads: json['verify_uploads'] ?? true,
    );
  }
}

class YTMPlaylist {
  final String id;
  final String title;
  final String description;
  final int? trackCount;
  final String? thumbnail;

  YTMPlaylist({
    required this.id,
    required this.title,
    required this.description,
    this.trackCount,
    this.thumbnail,
  });

  factory YTMPlaylist.fromJson(Map<String, dynamic> json) {
    return YTMPlaylist(
      id: json['id'] ?? '',
      title: json['title'] ?? 'Untitled Playlist',
      description: json['description'] ?? '',
      trackCount: json['track_count'],
      thumbnail: json['thumbnail'],
    );
  }
}

class YTMPlaylistTrack {
  final String? videoId;
  final String title;
  final String? artist;
  final String? album;
  final dynamic duration;
  final String? thumbnail;
  final bool inLocal;
  final bool inUploads;
  final String? localPath;

  YTMPlaylistTrack({
    this.videoId,
    required this.title,
    this.artist,
    this.album,
    this.duration,
    this.thumbnail,
    required this.inLocal,
    required this.inUploads,
    this.localPath,
  });

  factory YTMPlaylistTrack.fromJson(Map<String, dynamic> json) {
    return YTMPlaylistTrack(
      videoId: json['video_id'],
      title: json['title'] ?? '',
      artist: json['artist'],
      album: json['album'],
      duration: json['duration'],
      thumbnail: json['thumbnail'],
      inLocal: json['in_local'] ?? false,
      inUploads: json['in_uploads'] ?? false,
      localPath: json['local_path'],
    );
  }

  YTMPlaylistTrack copyWith({
    String? videoId,
    String? title,
    String? artist,
    String? album,
    dynamic duration,
    String? thumbnail,
    bool? inLocal,
    bool? inUploads,
    String? localPath,
  }) {
    return YTMPlaylistTrack(
      videoId: videoId ?? this.videoId,
      title: title ?? this.title,
      artist: artist ?? this.artist,
      album: album ?? this.album,
      duration: duration ?? this.duration,
      thumbnail: thumbnail ?? this.thumbnail,
      inLocal: inLocal ?? this.inLocal,
      inUploads: inUploads ?? this.inUploads,
      localPath: localPath ?? this.localPath,
    );
  }

  String get displayArtist => (artist != null && artist!.isNotEmpty) ? artist! : 'Unknown Artist';
  String get displayAlbum => (album != null && album!.isNotEmpty) ? album! : 'Unknown Album';
  
  String get formattedDuration {
    if (duration == null) return '--:--';
    if (duration is String) return duration;
    if (duration is num) {
      final totalSec = duration.round();
      final mins = totalSec ~/ 60;
      final secs = totalSec % 60;
      return '$mins:${secs.toString().padLeft(2, '0')}';
    }
    return '--:--';
  }
}

class YTMPlaylistDetails {
  final String id;
  final String title;
  final String description;
  final int trackCount;
  final String? thumbnail;
  final List<YTMPlaylistTrack> tracks;

  YTMPlaylistDetails({
    required this.id,
    required this.title,
    required this.description,
    required this.trackCount,
    this.thumbnail,
    required this.tracks,
  });

  factory YTMPlaylistDetails.fromJson(Map<String, dynamic> json) {
    final rawTracks = json['tracks'] as List<dynamic>? ?? [];
    return YTMPlaylistDetails(
      id: json['id'] ?? '',
      title: json['title'] ?? '',
      description: json['description'] ?? '',
      trackCount: json['track_count'] ?? rawTracks.length,
      thumbnail: json['thumbnail'],
      tracks: rawTracks.map((t) => YTMPlaylistTrack.fromJson(t as Map<String, dynamic>)).toList(),
    );
  }
}

class RootFolderStats {
  final String path;
  final bool exists;
  final String freeSpace;
  final String totalSpace;
  final int songsCount;
  final int unmappedCount;

  RootFolderStats({
    required this.path,
    required this.exists,
    required this.freeSpace,
    required this.totalSpace,
    required this.songsCount,
    required this.unmappedCount,
  });

  factory RootFolderStats.fromJson(Map<String, dynamic> json) {
    return RootFolderStats(
      path: json['path'] ?? '',
      exists: json['exists'] ?? false,
      freeSpace: json['free_space'] ?? 'N/A',
      totalSpace: json['total_space'] ?? 'N/A',
      songsCount: json['songs_count'] ?? 0,
      unmappedCount: json['unmapped_count'] ?? 0,
    );
  }
}

class FsDirectoryItem {
  final String name;
  final String path;

  FsDirectoryItem({
    required this.name,
    required this.path,
  });

  factory FsDirectoryItem.fromJson(Map<String, dynamic> json) {
    return FsDirectoryItem(
      name: json['name'] ?? '',
      path: json['path'] ?? '',
    );
  }
}

class FsBrowseResult {
  final String currentPath;
  final String? parentPath;
  final List<FsDirectoryItem> directories;
  final String freeSpace;
  final String totalSpace;

  FsBrowseResult({
    required this.currentPath,
    this.parentPath,
    required this.directories,
    required this.freeSpace,
    required this.totalSpace,
  });

  factory FsBrowseResult.fromJson(Map<String, dynamic> json) {
    final dirs = (json['directories'] as List<dynamic>?)
            ?.map((d) => FsDirectoryItem.fromJson(d as Map<String, dynamic>))
            .toList() ??
        [];
    return FsBrowseResult(
      currentPath: json['current_path'] ?? '/',
      parentPath: json['parent_path'],
      directories: dirs,
      freeSpace: json['free_space'] ?? 'N/A',
      totalSpace: json['total_space'] ?? 'N/A',
    );
  }
}

class MusicBrainzMatch {
  final String mbid;
  final String title;
  final String primaryTitle;
  final String artist;
  final String? featuredArtists;
  final String? album;
  final int? trackNumber;
  final String? releaseDate;
  final String? coverUrl;
  final String source;
  final int score;

  MusicBrainzMatch({
    required this.mbid,
    required this.title,
    required this.primaryTitle,
    required this.artist,
    this.featuredArtists,
    this.album,
    this.trackNumber,
    this.releaseDate,
    this.coverUrl,
    this.source = 'YouTube Music',
    required this.score,
  });

  factory MusicBrainzMatch.fromJson(Map<String, dynamic> json) {
    return MusicBrainzMatch(
      mbid: json['mbid'] ?? '',
      title: json['title'] ?? '',
      primaryTitle: json['primary_title'] ?? '',
      artist: json['artist'] ?? '',
      featuredArtists: json['featured_artists'],
      album: json['album'],
      trackNumber: json['track_number'],
      releaseDate: json['release_date'],
      coverUrl: json['cover_url'],
      source: json['source'] ?? 'YouTube Music',
      score: json['score'] ?? 100,
    );
  }
}

class YtmUpload {
  final int? id;
  final String entityId;
  final String? videoId;
  final String title;
  final String? artist;
  final String? album;
  final double? duration;
  final String? likeStatus;
  final String? thumbnail;
  final String? firstSeen;
  final String? lastSeen;

  YtmUpload({
    this.id,
    required this.entityId,
    this.videoId,
    required this.title,
    this.artist,
    this.album,
    this.duration,
    this.likeStatus,
    this.thumbnail,
    this.firstSeen,
    this.lastSeen,
  });

  factory YtmUpload.fromJson(Map<String, dynamic> json) {
    return YtmUpload(
      id: json['id'],
      entityId: json['entity_id'] ?? '',
      videoId: json['video_id'],
      title: json['title'] ?? '',
      artist: json['artist'],
      album: json['album'],
      duration: json['duration'] != null ? (json['duration'] as num).toDouble() : null,
      likeStatus: json['like_status'],
      thumbnail: json['thumbnail'],
      firstSeen: json['first_seen'],
      lastSeen: json['last_seen'],
    );
  }

  bool get hasNoArtist => artist == null || artist!.trim().isEmpty || artist!.trim().toLowerCase() == 'unknown artist' || artist!.trim().toLowerCase() == 'unknown';
  bool get hasNoAlbum => album == null || album!.trim().isEmpty || album!.trim().toLowerCase() == 'unknown album' || album!.trim().toLowerCase() == 'unknown';
  bool get hasNoArtwork => thumbnail == null || thumbnail!.trim().isEmpty;
  bool get hasFileExt {
    final lowerTitle = title.toLowerCase();
    return lowerTitle.endsWith('.mp3') ||
        lowerTitle.endsWith('.flac') ||
        lowerTitle.endsWith('.m4a') ||
        lowerTitle.endsWith('.wav') ||
        lowerTitle.endsWith('.opus') ||
        lowerTitle.endsWith('.webm') ||
        lowerTitle.startsWith('y2mate') ||
        lowerTitle.startsWith('snapsave') ||
        lowerTitle.startsWith('tuberipper');
  }

  bool get isMissingMetadata => hasNoArtist || hasNoAlbum || hasNoArtwork || hasFileExt;

  YtmUpload copyWith({
    int? id,
    String? entityId,
    String? videoId,
    String? title,
    String? artist,
    String? album,
    double? duration,
    String? likeStatus,
    String? thumbnail,
    String? firstSeen,
    String? lastSeen,
  }) {
    return YtmUpload(
      id: id ?? this.id,
      entityId: entityId ?? this.entityId,
      videoId: videoId ?? this.videoId,
      title: title ?? this.title,
      artist: artist ?? this.artist,
      album: album ?? this.album,
      duration: duration ?? this.duration,
      likeStatus: likeStatus ?? this.likeStatus,
      thumbnail: thumbnail ?? this.thumbnail,
      firstSeen: firstSeen ?? this.firstSeen,
      lastSeen: lastSeen ?? this.lastSeen,
    );
  }

  String get displayTitle => title.isNotEmpty ? title : 'Untitled';
  String get displayArtist => (artist != null && artist!.isNotEmpty) ? artist! : 'Unknown Artist';
  String get displayAlbum => (album != null && album!.isNotEmpty) ? album! : 'Unknown Album';

  String get formattedDuration {
    if (duration == null || duration! <= 0) return '--:--';
    final totalSec = duration!.round();
    final mins = totalSec ~/ 60;
    final secs = totalSec % 60;
    return '$mins:${secs.toString().padLeft(2, '0')}';
  }

  bool get isSkitOrShort =>
      (duration != null && duration! > 0 && duration! < 60) ||
      (duration != null &&
          duration! < 90 &&
          (title.toLowerCase().contains('skit') ||
              title.toLowerCase().contains('interlude') ||
              title.toLowerCase().contains('intro') ||
              title.toLowerCase().contains('outro')));
}

class PlaylistSyncStatusModel {
  final bool isRunning;
  final String? playlistId;
  final String? playlistTitle;
  final int totalTracks;
  final int completedTracks;
  final int failedTracks;
  final int needsHelpTracks;
  final String? currentTrack;
  final List<String> errors;

  PlaylistSyncStatusModel({
    required this.isRunning,
    this.playlistId,
    this.playlistTitle,
    required this.totalTracks,
    required this.completedTracks,
    required this.failedTracks,
    this.needsHelpTracks = 0,
    this.currentTrack,
    required this.errors,
  });

  factory PlaylistSyncStatusModel.fromJson(Map<String, dynamic> json) {
    return PlaylistSyncStatusModel(
      isRunning: json['is_running'] ?? false,
      playlistId: json['playlist_id'],
      playlistTitle: json['playlist_title'],
      totalTracks: json['total_tracks'] ?? 0,
      completedTracks: json['completed_tracks'] ?? 0,
      failedTracks: json['failed_tracks'] ?? 0,
      needsHelpTracks: json['needs_help_tracks'] ?? 0,
      currentTrack: json['current_track'],
      errors: (json['errors'] as List<dynamic>?)?.map((e) => e.toString()).toList() ?? [],
    );
  }

  double get progress => totalTracks > 0 ? (completedTracks / totalTracks).clamp(0.0, 1.0) : 0.0;
}

class UnifiedQueueItem {
  final String id;
  final String? videoId;
  final String category; // 'download', 'upload', 'local_upload', 'metadata_change', 'needs_help'
  final String title;
  final String? artist;
  final String? album;
  final String? thumbnail;
  final String status; // 'in_progress', 'queued', 'completed', 'failed', 'needs_help'
  final String? currentStep;
  final String? source;
  final String? createdAt;
  final String? error;

  UnifiedQueueItem({
    required this.id,
    this.videoId,
    required this.category,
    required this.title,
    this.artist,
    this.album,
    this.thumbnail,
    required this.status,
    this.currentStep,
    this.source,
    this.createdAt,
    this.error,
  });

  factory UnifiedQueueItem.fromJson(Map<String, dynamic> json) {
    return UnifiedQueueItem(
      id: json['id'] ?? '',
      videoId: json['video_id'],
      category: json['category'] ?? 'upload',
      title: json['title'] ?? 'Untitled',
      artist: json['artist'],
      album: json['album'],
      thumbnail: json['thumbnail'],
      status: json['status'] ?? 'queued',
      currentStep: json['current_step'],
      source: json['source'],
      createdAt: json['created_at'],
      error: json['error'],
    );
  }
}

class UnifiedQueueResponse {
  final Map<String, int> summary;
  final bool isActive;
  final String activeDescription;
  final List<UnifiedQueueItem> items;

  UnifiedQueueResponse({
    required this.summary,
    required this.isActive,
    required this.activeDescription,
    required this.items,
  });

  factory UnifiedQueueResponse.fromJson(Map<String, dynamic> json) {
    final rawSummary = json['summary'] as Map<String, dynamic>? ?? {};
    final summary = <String, int>{
      'all': rawSummary['all'] ?? 0,
      'needs_help': rawSummary['needs_help'] ?? 0,
      'metadata_change': rawSummary['metadata_change'] ?? 0,
      'download': rawSummary['download'] ?? 0,
      'upload': rawSummary['upload'] ?? 0,
      'local_upload': rawSummary['local_upload'] ?? 0,
      'active': rawSummary['active'] ?? 0,
    };

    final itemsRaw = json['items'] as List<dynamic>? ?? [];
    final items = itemsRaw.map((e) => UnifiedQueueItem.fromJson(e as Map<String, dynamic>)).toList();

    return UnifiedQueueResponse(
      summary: summary,
      isActive: json['is_active'] ?? false,
      activeDescription: json['active_description'] ?? '',
      items: items,
    );
  }
}

class ReplicatedPlaylistModel {
  final int id;
  final String sourcePlaylistId;
  final String sourcePlaylistName;
  final String destinationPlaylistId;
  final String destinationPlaylistName;
  final bool enabled;
  final int syncIntervalSeconds;
  final String? lastSourceRevision;
  final String? lastSyncAt;
  final String? lastSyncStatus;

  ReplicatedPlaylistModel({
    required this.id,
    required this.sourcePlaylistId,
    required this.sourcePlaylistName,
    required this.destinationPlaylistId,
    required this.destinationPlaylistName,
    required this.enabled,
    required this.syncIntervalSeconds,
    this.lastSourceRevision,
    this.lastSyncAt,
    this.lastSyncStatus,
  });

  factory ReplicatedPlaylistModel.fromJson(Map<String, dynamic> json) {
    return ReplicatedPlaylistModel(
      id: json['id'] ?? 0,
      sourcePlaylistId: json['source_playlist_id'] ?? '',
      sourcePlaylistName: json['source_playlist_name'] ?? '',
      destinationPlaylistId: json['destination_playlist_id'] ?? '',
      destinationPlaylistName: json['destination_playlist_name'] ?? '',
      enabled: json['enabled'] ?? true,
      syncIntervalSeconds: json['sync_interval_seconds'] ?? 300,
      lastSourceRevision: json['last_source_revision'],
      lastSyncAt: json['last_sync_at'],
      lastSyncStatus: json['last_sync_status'],
    );
  }
}

class ExcludedTrackItem {
  final String videoId;
  final String title;
  final String artist;
  final String reason;
  final String humanReason;

  ExcludedTrackItem({
    required this.videoId,
    required this.title,
    required this.artist,
    required this.reason,
    required this.humanReason,
  });

  factory ExcludedTrackItem.fromJson(Map<String, dynamic> json) {
    return ExcludedTrackItem(
      videoId: json['video_id'] ?? '',
      title: json['title'] ?? 'Unknown',
      artist: json['artist'] ?? 'Unknown',
      reason: json['reason'] ?? 'NOT_PRESENT_IN_LOCKER',
      humanReason: json['human_reason'] ?? json['reason'] ?? 'Not present in upload locker',
    );
  }
}

class ReplicationPreviewModel {
  final int replicatedId;
  final String sourcePlaylistName;
  final String destinationPlaylistName;
  final String? destinationPlaylistId;
  final String? revision;
  final int sourceTracksCount;
  final int desiredTracksCount;
  final int excludedCount;
  final List<ExcludedTrackItem> excludedTracks;
  final String status;
  final List<dynamic> actions;
  final bool dryRun;

  ReplicationPreviewModel({
    required this.replicatedId,
    required this.sourcePlaylistName,
    required this.destinationPlaylistName,
    this.destinationPlaylistId,
    this.revision,
    required this.sourceTracksCount,
    required this.desiredTracksCount,
    required this.excludedCount,
    required this.excludedTracks,
    required this.status,
    required this.actions,
    required this.dryRun,
  });

  factory ReplicationPreviewModel.fromJson(Map<String, dynamic> rawJson) {
    final json = (rawJson['preview'] is Map<String, dynamic>)
        ? rawJson['preview'] as Map<String, dynamic>
        : rawJson;
    final rawEx = json['excluded_tracks'] as List<dynamic>? ?? [];
    return ReplicationPreviewModel(
      replicatedId: json['replicated_id'] ?? 0,
      sourcePlaylistName: json['source_playlist_name'] ?? '',
      destinationPlaylistName: json['destination_playlist_name'] ?? '',
      destinationPlaylistId: json['destination_playlist_id'],
      revision: json['revision'],
      sourceTracksCount: json['source_tracks_count'] ?? 0,
      desiredTracksCount: json['desired_tracks_count'] ?? 0,
      excludedCount: json['excluded_count'] ?? 0,
      excludedTracks: rawEx.map((e) => ExcludedTrackItem.fromJson(e as Map<String, dynamic>)).toList(),
      status: json['status'] ?? 'UNKNOWN',
      actions: json['actions'] as List<dynamic>? ?? [],
      dryRun: json['dry_run'] ?? false,
    );
  }
}

class User {
  final String id;
  final String username;
  final String role;
  final bool isActive;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  User({
    required this.id,
    required this.username,
    required this.role,
    this.isActive = true,
    this.createdAt,
    this.updatedAt,
  });

  bool get isAdmin => role.toUpperCase() == 'ADMIN';

  factory User.fromJson(Map<String, dynamic> json) {
    return User(
      id: json['id']?.toString() ?? '',
      username: json['username']?.toString() ?? '',
      role: json['role']?.toString() ?? 'USER',
      isActive: json['is_active'] ?? true,
      createdAt: json['created_at'] != null ? DateTime.tryParse(json['created_at'].toString()) : null,
      updatedAt: json['updated_at'] != null ? DateTime.tryParse(json['updated_at'].toString()) : null,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'username': username,
    'role': role,
    'is_active': isActive,
    'created_at': createdAt?.toIso8601String(),
    'updated_at': updatedAt?.toIso8601String(),
  };
}

class UserLoginResponse {
  final String token;
  final User user;
  final DateTime? sessionExpiresAt;

  UserLoginResponse({
    required this.token,
    required this.user,
    this.sessionExpiresAt,
  });

  factory UserLoginResponse.fromJson(Map<String, dynamic> json) {
    return UserLoginResponse(
      token: json['token']?.toString() ?? '',
      user: User.fromJson(json['user'] as Map<String, dynamic>? ?? {}),
      sessionExpiresAt: json['session_expires_at'] != null
          ? DateTime.tryParse(json['session_expires_at'].toString())
          : null,
    );
  }
}

class YouTubeMusicAccount {
  final String id;
  final String userId;
  final String? accountName;
  final String? accountEmail;
  final String status;
  final DateTime? lastSyncAt;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  YouTubeMusicAccount({
    required this.id,
    required this.userId,
    this.accountName,
    this.accountEmail,
    required this.status,
    this.lastSyncAt,
    this.createdAt,
    this.updatedAt,
  });

  bool get isConnected => status.toUpperCase() == 'CONNECTED';

  factory YouTubeMusicAccount.fromJson(Map<String, dynamic> json) {
    return YouTubeMusicAccount(
      id: json['id']?.toString() ?? '',
      userId: json['user_id']?.toString() ?? '',
      accountName: json['account_name']?.toString(),
      accountEmail: json['account_email']?.toString(),
      status: json['status']?.toString() ?? 'DISCONNECTED',
      lastSyncAt: json['last_sync_at'] != null ? DateTime.tryParse(json['last_sync_at'].toString()) : null,
      createdAt: json['created_at'] != null ? DateTime.tryParse(json['created_at'].toString()) : null,
      updatedAt: json['updated_at'] != null ? DateTime.tryParse(json['updated_at'].toString()) : null,
    );
  }
}

// ---------------------------------------------------------------------------
// Family Mode & Multi-Account Models (Sections 1-40)
// ---------------------------------------------------------------------------

class Family {
  final String id;
  final String name;
  final String ownerUserId;
  final DateTime? createdAt;
  final DateTime? updatedAt;
  final String? userRole;

  Family({
    required this.id,
    required this.name,
    required this.ownerUserId,
    this.createdAt,
    this.updatedAt,
    this.userRole,
  });

  factory Family.fromJson(Map<String, dynamic> json) {
    return Family(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      ownerUserId: json['owner_user_id']?.toString() ?? '',
      createdAt: json['created_at'] != null ? DateTime.tryParse(json['created_at'].toString()) : null,
      updatedAt: json['updated_at'] != null ? DateTime.tryParse(json['updated_at'].toString()) : null,
      userRole: json['user_role']?.toString(),
    );
  }
}

class FamilyMember {
  final String id;
  final String familyId;
  final String userId;
  final String? username;
  final String role;
  final String status;
  final bool showAccountInFamily;
  final bool allowFamilyUploads;
  final bool allowFamilyPlaylists;
  final bool allowFamilySync;
  final DateTime? joinedAt;

  FamilyMember({
    required this.id,
    required this.familyId,
    required this.userId,
    this.username,
    required this.role,
    required this.status,
    this.showAccountInFamily = true,
    this.allowFamilyUploads = true,
    this.allowFamilyPlaylists = false,
    this.allowFamilySync = false,
    this.joinedAt,
  });

  factory FamilyMember.fromJson(Map<String, dynamic> json) {
    return FamilyMember(
      id: json['id']?.toString() ?? '',
      familyId: json['family_id']?.toString() ?? '',
      userId: json['user_id']?.toString() ?? '',
      username: json['username']?.toString(),
      role: json['role']?.toString() ?? 'MEMBER',
      status: json['status']?.toString() ?? 'ACTIVE',
      showAccountInFamily: json['show_account_in_family'] == true || json['show_account_in_family'] == 1,
      allowFamilyUploads: json['allow_family_uploads'] == true || json['allow_family_uploads'] == 1,
      allowFamilyPlaylists: json['allow_family_playlists'] == true || json['allow_family_playlists'] == 1,
      allowFamilySync: json['allow_family_sync'] == true || json['allow_family_sync'] == 1,
      joinedAt: json['joined_at'] != null ? DateTime.tryParse(json['joined_at'].toString()) : null,
    );
  }
}

class FamilyInvitation {
  final String id;
  final String familyId;
  final String invitationToken;
  final String createdByUserId;
  final int maxUses;
  final int timesUsed;
  final String status;
  final DateTime? expiresAt;
  final DateTime? createdAt;

  FamilyInvitation({
    required this.id,
    required this.familyId,
    required this.invitationToken,
    required this.createdByUserId,
    this.maxUses = 1,
    this.timesUsed = 0,
    required this.status,
    this.expiresAt,
    this.createdAt,
  });

  factory FamilyInvitation.fromJson(Map<String, dynamic> json) {
    return FamilyInvitation(
      id: json['id']?.toString() ?? '',
      familyId: json['family_id']?.toString() ?? '',
      invitationToken: json['invitation_token']?.toString() ?? '',
      createdByUserId: json['created_by_user_id']?.toString() ?? '',
      maxUses: json['max_uses'] ?? 1,
      timesUsed: json['times_used'] ?? 0,
      status: json['status']?.toString() ?? 'PENDING',
      expiresAt: json['expires_at'] != null ? DateTime.tryParse(json['expires_at'].toString()) : null,
      createdAt: json['created_at'] != null ? DateTime.tryParse(json['created_at'].toString()) : null,
    );
  }
}

class FamilyDashboardMemberItem {
  final String userId;
  final String username;
  final String role;
  final bool ytmConnected;
  final String? accountName;
  final int? uploadsCount;
  final bool allowFamilyUploads;
  final bool allowFamilyPlaylists;
  final bool allowFamilySync;

  FamilyDashboardMemberItem({
    required this.userId,
    required this.username,
    required this.role,
    required this.ytmConnected,
    this.accountName,
    this.uploadsCount,
    required this.allowFamilyUploads,
    required this.allowFamilyPlaylists,
    required this.allowFamilySync,
  });

  factory FamilyDashboardMemberItem.fromJson(Map<String, dynamic> json) {
    return FamilyDashboardMemberItem(
      userId: json['user_id']?.toString() ?? '',
      username: json['username']?.toString() ?? 'User',
      role: json['role']?.toString() ?? 'MEMBER',
      ytmConnected: json['ytm_connected'] == true,
      accountName: json['account_name']?.toString(),
      uploadsCount: json['uploads_count'] is int ? json['uploads_count'] : null,
      allowFamilyUploads: json['allow_family_uploads'] == true,
      allowFamilyPlaylists: json['allow_family_playlists'] == true,
      allowFamilySync: json['allow_family_sync'] == true,
    );
  }
}

class FamilyDashboardResponse {
  final String familyId;
  final String familyName;
  final String callerRole;
  final int totalMembers;
  final int activeConnectedMembers;
  final List<FamilyDashboardMemberItem> members;

  FamilyDashboardResponse({
    required this.familyId,
    required this.familyName,
    required this.callerRole,
    required this.totalMembers,
    required this.activeConnectedMembers,
    required this.members,
  });

  factory FamilyDashboardResponse.fromJson(Map<String, dynamic> json) {
    return FamilyDashboardResponse(
      familyId: json['family_id']?.toString() ?? '',
      familyName: json['family_name']?.toString() ?? '',
      callerRole: json['caller_role']?.toString() ?? 'MEMBER',
      totalMembers: json['total_members'] ?? 0,
      activeConnectedMembers: json['active_connected_members'] ?? 0,
      members: (json['members'] as List<dynamic>? ?? [])
          .map((m) => FamilyDashboardMemberItem.fromJson(m as Map<String, dynamic>))
          .toList(),
    );
  }
}

class SelectableAccountItem {
  final String userId;
  final String username;
  final String? accountName;
  final String? accountIdentifier;
  final bool isConnected;
  final bool isSelf;
  final String? familyId;
  final String? familyName;
  final bool allowFamilyUploads;
  final bool allowFamilyPlaylists;
  final bool allowFamilySync;

  bool get ytmConnected => isConnected;

  SelectableAccountItem({
    required this.userId,
    required this.username,
    this.accountName,
    this.accountIdentifier,
    required this.isConnected,
    required this.isSelf,
    this.familyId,
    this.familyName,
    required this.allowFamilyUploads,
    this.allowFamilyPlaylists = false,
    this.allowFamilySync = false,
  });

  String get displayName => isSelf ? '$username (You)' : username;
  String get displayAccount => accountName ?? accountIdentifier ?? 'Connected';

  factory SelectableAccountItem.fromJson(Map<String, dynamic> json) {
    return SelectableAccountItem(
      userId: json['user_id']?.toString() ?? '',
      username: json['username']?.toString() ?? '',
      accountName: json['account_name']?.toString(),
      accountIdentifier: json['account_identifier']?.toString(),
      isConnected: json['is_connected'] == true || json['ytm_connected'] == true,
      isSelf: json['is_self'] == true,
      familyId: json['family_id']?.toString(),
      familyName: json['family_name']?.toString(),
      allowFamilyUploads: json['allow_family_uploads'] == true,
      allowFamilyPlaylists: json['allow_family_playlists'] == true,
      allowFamilySync: json['allow_family_sync'] == true,
    );
  }
}

class UploadDestinationResponse {
  final int jobsCreated;
  final List<int> jobIds;
  final List<String> destinations;

  UploadDestinationResponse({
    required this.jobsCreated,
    required this.jobIds,
    required this.destinations,
  });

  factory UploadDestinationResponse.fromJson(Map<String, dynamic> json) {
    return UploadDestinationResponse(
      jobsCreated: json['jobs_created'] ?? 0,
      jobIds: (json['job_ids'] as List<dynamic>? ?? []).map((e) => (e as num).toInt()).toList(),
      destinations: (json['destinations'] as List<dynamic>? ?? []).map((e) => e.toString()).toList(),
    );
  }
}

class TrackDestinationDuplicateStatus {
  final String destinationUserId;
  final String destinationUsername;
  final bool isUploaded;
  final String status;
  final String? error;

  TrackDestinationDuplicateStatus({
    required this.destinationUserId,
    required this.destinationUsername,
    required this.isUploaded,
    required this.status,
    this.error,
  });

  factory TrackDestinationDuplicateStatus.fromJson(Map<String, dynamic> json) {
    return TrackDestinationDuplicateStatus(
      destinationUserId: json['destination_user_id']?.toString() ?? '',
      destinationUsername: json['destination_username']?.toString() ?? '',
      isUploaded: json['is_uploaded'] == true,
      status: json['status']?.toString() ?? 'not_uploaded',
      error: json['error']?.toString(),
    );
  }
}

class FamilyQueueDestinationSubItem {
  final String destinationUserId;
  final String destinationUsername;
  final int jobId;
  final String status;
  final int attempts;
  final String? error;

  FamilyQueueDestinationSubItem({
    required this.destinationUserId,
    required this.destinationUsername,
    required this.jobId,
    required this.status,
    required this.attempts,
    this.error,
  });

  factory FamilyQueueDestinationSubItem.fromJson(Map<String, dynamic> json) {
    return FamilyQueueDestinationSubItem(
      destinationUserId: json['destination_user_id']?.toString() ?? '',
      destinationUsername: json['destination_username']?.toString() ?? '',
      jobId: json['job_id'] ?? 0,
      status: json['status']?.toString() ?? 'queued',
      attempts: json['attempts'] ?? 0,
      error: json['error']?.toString(),
    );
  }
}

class FamilyQueueItem {
  final int musicFileId;
  final String filename;
  final String? title;
  final String? artist;
  final List<FamilyQueueDestinationSubItem> destinations;

  FamilyQueueItem({
    required this.musicFileId,
    required this.filename,
    this.title,
    this.artist,
    required this.destinations,
  });

  String get displayTitle => (title != null && title!.isNotEmpty) ? title! : filename;

  factory FamilyQueueItem.fromJson(Map<String, dynamic> json) {
    return FamilyQueueItem(
      musicFileId: json['music_file_id'] ?? 0,
      filename: json['filename']?.toString() ?? '',
      title: json['title']?.toString(),
      artist: json['artist']?.toString(),
      destinations: (json['destinations'] as List<dynamic>? ?? [])
          .map((d) => FamilyQueueDestinationSubItem.fromJson(d as Map<String, dynamic>))
          .toList(),
    );
  }
}

class FamilyUploadHistoryItem {
  final int jobId;
  final int musicFileId;
  final String filename;
  final String? title;
  final String? artist;
  final String requestedByUserId;
  final String requestedByUsername;
  final String destinationUserId;
  final String destinationUsername;
  final String status;
  final DateTime? completedAt;
  final String? error;

  FamilyUploadHistoryItem({
    required this.jobId,
    required this.musicFileId,
    required this.filename,
    this.title,
    this.artist,
    required this.requestedByUserId,
    required this.requestedByUsername,
    required this.destinationUserId,
    required this.destinationUsername,
    required this.status,
    this.completedAt,
    this.error,
  });

  String get displayTitle => (title != null && title!.isNotEmpty) ? title! : filename;

  factory FamilyUploadHistoryItem.fromJson(Map<String, dynamic> json) {
    return FamilyUploadHistoryItem(
      jobId: json['job_id'] ?? 0,
      musicFileId: json['music_file_id'] ?? 0,
      filename: json['filename']?.toString() ?? '',
      title: json['title']?.toString(),
      artist: json['artist']?.toString(),
      requestedByUserId: json['requested_by_user_id']?.toString() ?? '',
      requestedByUsername: json['requested_by_username']?.toString() ?? '',
      destinationUserId: json['destination_user_id']?.toString() ?? '',
      destinationUsername: json['destination_username']?.toString() ?? '',
      status: json['status']?.toString() ?? 'queued',
      completedAt: json['completed_at'] != null ? DateTime.tryParse(json['completed_at'].toString()) : null,
      error: json['error']?.toString(),
    );
  }
}

class FamilyPlaylistItem {
  final String playlistId;
  final String title;
  final String? description;
  final String ownerUserId;
  final String ownerUsername;
  final int trackCount;

  FamilyPlaylistItem({
    required this.playlistId,
    required this.title,
    this.description,
    required this.ownerUserId,
    required this.ownerUsername,
    this.trackCount = 0,
  });

  factory FamilyPlaylistItem.fromJson(Map<String, dynamic> json) {
    return FamilyPlaylistItem(
      playlistId: json['playlist_id']?.toString() ?? '',
      title: json['title']?.toString() ?? '',
      description: json['description']?.toString(),
      ownerUserId: json['owner_user_id']?.toString() ?? '',
      ownerUsername: json['owner_username']?.toString() ?? '',
      trackCount: json['track_count'] ?? 0,
    );
  }
}

