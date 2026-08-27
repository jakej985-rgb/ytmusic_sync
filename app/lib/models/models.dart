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
