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


