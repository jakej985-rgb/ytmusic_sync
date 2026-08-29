import 'package:flutter/material.dart';
import '../../models/models.dart';
import '../../services/api_service.dart';

class _ParsedFilenameParts {
  final String partA;
  final String partB;
  final String? trackNum;
  final bool isBySeparator;

  _ParsedFilenameParts({
    required this.partA,
    required this.partB,
    this.trackNum,
    required this.isBySeparator,
  });
}

class MetadataEditorDialog extends StatefulWidget {
  final MusicFile? song;
  final YtmUpload? ytmUpload;

  const MetadataEditorDialog({super.key, this.song, this.ytmUpload})
      : assert(song != null || ytmUpload != null);

  static Future<bool?> show(BuildContext context, MusicFile song) {
    return showDialog<bool>(
      context: context,
      builder: (context) => MetadataEditorDialog(song: song),
    );
  }

  static Future<bool?> showForYtmUpload(BuildContext context, YtmUpload upload) {
    return showDialog<bool>(
      context: context,
      builder: (context) => MetadataEditorDialog(ytmUpload: upload),
    );
  }

  @override
  State<MetadataEditorDialog> createState() => _MetadataEditorDialogState();
}

class _MetadataEditorDialogState extends State<MetadataEditorDialog> {
  late TextEditingController _titleController;
  late TextEditingController _artistController;
  late TextEditingController _albumController;
  late TextEditingController _trackNumController;

  bool _isSaving = false;
  String? _errorMessage;

  bool _isSearchingMb = false;
  List<MusicBrainzMatch>? _mbMatches;
  String? _mbSearchMessage;

  @override
  void initState() {
    super.initState();
    final isYtm = widget.ytmUpload != null;
    final rawFilename = isYtm ? widget.ytmUpload!.title : widget.song!.filename;
    final rawName = rawFilename.replaceAll(RegExp(r'\.[a-zA-Z0-9]+$'), '').trim();
    String initialTitle = (isYtm ? widget.ytmUpload!.title : widget.song!.title) ?? rawName;
    if (initialTitle.toLowerCase().endsWith('.mp3') ||
        initialTitle.toLowerCase().endsWith('.flac') ||
        initialTitle.toLowerCase().endsWith('.m4a') ||
        initialTitle.toLowerCase().endsWith('.wav')) {
      initialTitle = rawName;
    }
    String initialArtist = (isYtm ? widget.ytmUpload!.artist : widget.song!.artist) ?? '';
    String initialAlbum = (isYtm ? widget.ytmUpload!.album : widget.song!.album) ?? '';
    String initialTrackNum = (!isYtm && widget.song?.trackNumber != null) ? widget.song!.trackNumber.toString() : '';

    // Auto-detect if artist is unknown or empty
    if (initialArtist.isEmpty || initialArtist.toLowerCase() == 'unknown artist') {
      final parts = _extractParts(rawName);
      if (parts != null) {
        if (parts.isBySeparator) {
          // "alone by tech nine" -> Title: "alone", Artist: "tech nine"
          initialTitle = parts.partA;
          initialArtist = parts.partB;
        } else {
          // "Akon - I Wanna Love You" -> Artist: "Akon", Title: "I Wanna Love You"
          initialArtist = parts.partA;
          initialTitle = parts.partB;
        }
        if (parts.trackNum != null) {
          initialTrackNum = parts.trackNum!;
        }
      }
    }

    _titleController = TextEditingController(text: initialTitle);
    _artistController = TextEditingController(text: initialArtist);
    _albumController = TextEditingController(text: initialAlbum);
    _trackNumController = TextEditingController(text: initialTrackNum);

    // If initial artist has feat/ft, normalize immediately
    _normalizeFeaturedArtists();
  }

  @override
  void dispose() {
    _titleController.dispose();
    _artistController.dispose();
    _albumController.dispose();
    _trackNumController.dispose();
    super.dispose();
  }

  _ParsedFilenameParts? _extractParts(String rawName) {
    String cleanName = rawName.trim();
    String? trackNum;

    final trackPrefixMatch = RegExp(r'^(\d+)\s*[-._\s]\s*(.+)$').firstMatch(cleanName);
    if (trackPrefixMatch != null) {
      trackNum = trackPrefixMatch.group(1);
      cleanName = trackPrefixMatch.group(2)!.trim();
    }

    // 0. Pattern: "Artist ft. Features Title" without hyphens
    // e.g. "C-Mob ft. Brotha Lynch Hung, Twisted Insane, & C. Ray For Some Strange Reason"
    final featMatchNoHyphen = RegExp(
      r"^(.*?)\s+(?:ft\.|feat\.|featuring)\s+(.+?)\s+([A-Z0-9][a-zA-Z0-9\s,&’'-]+)$",
      caseSensitive: false,
    ).firstMatch(cleanName);
    if (featMatchNoHyphen != null && !cleanName.contains(' - ') && !cleanName.contains(' by ')) {
      final mainArtist = featMatchNoHyphen.group(1)!.trim();
      final features = featMatchNoHyphen.group(2)!.trim();
      final songTitle = featMatchNoHyphen.group(3)!.trim();
      return _ParsedFilenameParts(
        partA: mainArtist,
        partB: '$songTitle ft. $features',
        trackNum: trackNum,
        isBySeparator: false,
      );
    }

    // 1. "by" separator: e.g. "alone by tech nine"
    final matchBy = RegExp(r'^(.+?)\s+by\s+(.+)$', caseSensitive: false).firstMatch(cleanName);
    if (matchBy != null) {
      var songTitle = matchBy.group(1)!.trim();
      var artistName = matchBy.group(2)!.trim();

      final featInArtist = RegExp(r'^(.*?)\s+(?:ft\.|feat\.|featuring)\s+(.+)$', caseSensitive: false).firstMatch(artistName);
      if (featInArtist != null) {
        final mainArtist = featInArtist.group(1)!.trim();
        final features = featInArtist.group(2)!.trim();
        artistName = mainArtist;
        if (!RegExp(r'\b(?:ft\.|feat\.|featuring)\b', caseSensitive: false).hasMatch(songTitle)) {
          songTitle = '$songTitle ft. $features';
        }
      }

      return _ParsedFilenameParts(
        partA: songTitle,
        partB: artistName,
        trackNum: trackNum,
        isBySeparator: true,
      );
    }

    // 2. " - " separator
    if (cleanName.contains(' - ')) {
      final parts = cleanName.split(' - ');
      var pA = parts[0].trim();
      var pB = parts.sublist(1).join(' - ').trim();

      final featInA = RegExp(r'^(.*?)\s+(?:ft\.|feat\.|featuring)\s+(.+)$', caseSensitive: false).firstMatch(pA);
      if (featInA != null) {
        final mainArtist = featInA.group(1)!.trim();
        final features = featInA.group(2)!.trim();
        pA = mainArtist;
        if (!RegExp(r'\b(?:ft\.|feat\.|featuring)\b', caseSensitive: false).hasMatch(pB)) {
          pB = '$pB ft. $features';
        }
      }

      return _ParsedFilenameParts(
        partA: pA,
        partB: pB,
        trackNum: trackNum,
        isBySeparator: false,
      );
    }

    // 3. " _ " separator
    if (cleanName.contains(' _ ')) {
      final parts = cleanName.split(' _ ');
      return _ParsedFilenameParts(
        partA: parts[0].trim(),
        partB: parts.sublist(1).join(' _ ').trim(),
        trackNum: trackNum,
        isBySeparator: false,
      );
    }

    // 4. Underscores
    if (cleanName.contains('_')) {
      final parts = cleanName.split('_');
      return _ParsedFilenameParts(
        partA: parts[0].trim(),
        partB: parts.sublist(1).join(' ').trim(),
        trackNum: trackNum,
        isBySeparator: false,
      );
    }

    // 5. Hyphen
    if (cleanName.contains('-')) {
      final parts = cleanName.split('-');
      return _ParsedFilenameParts(
        partA: parts[0].trim(),
        partB: parts.sublist(1).join('-').trim(),
        trackNum: trackNum,
        isBySeparator: false,
      );
    }

    return null;
  }

  void _normalizeFeaturedArtists() {
    final artist = _artistController.text.trim();
    final featMatch = RegExp(r'^(.*?)\s+(?:ft\.|feat\.|featuring)\s+(.+)$', caseSensitive: false).firstMatch(artist);
    if (featMatch != null) {
      final mainArtist = featMatch.group(1)!.trim();
      final features = featMatch.group(2)!.trim();
      final title = _titleController.text.trim();

      setState(() {
        _artistController.text = mainArtist;
        if (!RegExp(r'\b(?:ft\.|feat\.|featuring)\b', caseSensitive: false).hasMatch(title)) {
          _titleController.text = '$title ft. $features';
        }
      });
    }
  }

  /// Automatically parses filename into Artist and Title based on requested order
  void _smartSplit({required bool artistFirst}) {
    final rawFilename = widget.ytmUpload != null ? widget.ytmUpload!.title : (widget.song?.filename ?? '');
    final rawName = rawFilename.replaceAll(RegExp(r'\.[a-zA-Z0-9]+$'), '').trim();
    final parts = _extractParts(rawName);

    if (parts != null) {
      setState(() {
        if (artistFirst) {
          // Artist gets partA, Title gets partB
          _artistController.text = parts.partA;
          _titleController.text = parts.partB;
        } else {
          // Title gets partA, Artist gets partB
          _titleController.text = parts.partA;
          _artistController.text = parts.partB;
        }
        if (parts.trackNum != null && parts.trackNum!.isNotEmpty) {
          _trackNumController.text = parts.trackNum!;
        }
      });
      _normalizeFeaturedArtists();
      _notifyAutoFill(artistFirst ? 'Artist - Title' : 'Title - Artist');
    } else {
      setState(() {
        _titleController.text = rawName;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('No separator found; used filename as Title.'),
          duration: Duration(seconds: 2),
        ),
      );
    }
  }

  void _swapArtistTitle() {
    final curTitle = _titleController.text;
    final curArtist = _artistController.text;
    setState(() {
      _titleController.text = curArtist;
      _artistController.text = curTitle;
    });
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('Swapped Title ⇄ Artist'),
        duration: Duration(seconds: 1),
      ),
    );
  }

  void _notifyAutoFill(String mode) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Auto-filled as $mode!'),
        duration: const Duration(seconds: 2),
      ),
    );
  }

  Future<void> _searchMusicBrainz({String? query}) async {
    setState(() {
      _isSearchingMb = true;
      _mbSearchMessage = null;
    });

    try {
      final artist = _artistController.text.trim();
      final title = _titleController.text.trim();
      final fallbackName = (widget.ytmUpload != null ? widget.ytmUpload!.title : (widget.song?.filename ?? '')).replaceAll(RegExp(r'\.[a-zA-Z0-9]+$'), '');
      final q = query ?? (artist.isNotEmpty && title.isNotEmpty ? null : fallbackName);

      final results = await apiService.searchMusicBrainz(
        query: q,
        artist: artist.isNotEmpty ? artist : null,
        title: title.isNotEmpty ? title : null,
        limit: 5,
      );

      setState(() {
        _mbMatches = results;
        _isSearchingMb = false;
        if (results.isEmpty) {
          _mbSearchMessage = 'No matching tracks found on MusicBrainz.';
        }
      });
    } catch (e) {
      setState(() {
        _isSearchingMb = false;
        _mbSearchMessage = 'Error searching MusicBrainz: $e';
      });
    }
  }

  void _applyMbMatch(MusicBrainzMatch match, {bool andUpload = false}) {
    setState(() {
      _titleController.text = match.title;
      _artistController.text = match.artist;
      if (match.album != null && match.album!.isNotEmpty) {
        _albumController.text = match.album!;
      }
      if (match.trackNumber != null) {
        _trackNumController.text = match.trackNumber.toString();
      }
    });

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Applied MusicBrainz tags: "${match.title}"!'),
        duration: const Duration(seconds: 2),
      ),
    );

    if (andUpload) {
      _saveMetadata(enqueueUpload: true);
    }
  }

  Future<void> _saveMetadata({bool enqueueUpload = false}) async {
    final title = _titleController.text.trim();
    if (title.isEmpty) {
      setState(() => _errorMessage = 'Title is required');
      return;
    }

    setState(() {
      _isSaving = true;
      _errorMessage = null;
    });

    try {
      final artist = _artistController.text.trim();
      final album = _albumController.text.trim();
      final trackNum = int.tryParse(_trackNumController.text.trim());

      if (widget.ytmUpload != null) {
        // YTM Upload Replace Mode
        final res = await apiService.replaceYtmUpload(
          widget.ytmUpload!.entityId,
          title: title,
          artist: artist.isNotEmpty ? artist : null,
          album: album.isNotEmpty ? album : null,
          trackNumber: trackNum,
        );
        if (res['success'] != true) {
          throw Exception(res['error'] ?? 'Failed to replace upload');
        }

        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text('Replaced & uploaded clean version of "$title" to YouTube Music!'),
              backgroundColor: Colors.green,
            ),
          );
          Navigator.of(context).pop(true);
        }
        return;
      }

      if (widget.song?.id == null) return;

      await apiService.updateSongMetadata(
        widget.song!.id!,
        title: title,
        artist: artist.isNotEmpty ? artist : null,
        album: album.isNotEmpty ? album : null,
        trackNumber: trackNum,
      );

      if (enqueueUpload) {
        await apiService.uploadSong(widget.song!.id!);
      }

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(enqueueUpload ? 'Saved & enqueued "$title" for upload!' : 'Metadata updated for "$title"'),
            backgroundColor: Colors.green,
          ),
        );
        Navigator.of(context).pop(true);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _errorMessage = e.toString().replaceFirst('Exception: ', '');
          _isSaving = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final hasFeatInArtist = _artistController.text.contains(RegExp(r'\b(?:ft\.|feat\.|featuring)\b', caseSensitive: false));

    return Dialog(
      backgroundColor: const Color(0xFF181820),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Container(
        width: 580,
        constraints: BoxConstraints(
          maxHeight: MediaQuery.of(context).size.height * 0.88,
        ),
        padding: const EdgeInsets.all(24),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Title Header
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      Icon(
                        widget.ytmUpload != null ? Icons.cloud_sync_outlined : Icons.edit_note,
                        color: const Color(0xFFFF0000),
                        size: 24,
                      ),
                      const SizedBox(width: 10),
                      Text(
                        widget.ytmUpload != null ? 'Retag & Replace YTM Upload' : 'Edit Track Metadata',
                        style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                  IconButton(
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close, size: 20),
                  ),
                ],
              ),
              const SizedBox(height: 4),

              // Filename Info Card
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: const Color(0xFF14141A),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: Colors.white10),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(
                          widget.ytmUpload != null ? Icons.cloud_done_outlined : Icons.audio_file_outlined,
                          size: 14,
                          color: widget.ytmUpload != null ? const Color(0xFF00B4D8) : Colors.grey,
                        ),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            widget.ytmUpload != null ? widget.ytmUpload!.title : (widget.song?.filename ?? ''),
                            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, fontFamily: 'monospace'),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      widget.ytmUpload != null
                          ? 'YouTube Music Upload • ID: ${widget.ytmUpload!.videoId ?? widget.ytmUpload!.entityId}'
                          : (widget.song?.path ?? ''),
                      style: TextStyle(fontSize: 10, color: Colors.grey[500], fontFamily: 'monospace'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),

              // Smart Split & Lookup Controls
              Wrap(
                spacing: 8,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: const Color(0xFF3EA6FF),
                      side: const BorderSide(color: Color(0xFF3EA6FF)),
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    ),
                    onPressed: () => _smartSplit(artistFirst: true),
                    icon: const Icon(Icons.auto_fix_high, size: 15),
                    label: const Text('Artist - Title', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                  ),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: const Color(0xFF3EA6FF),
                      side: const BorderSide(color: Color(0xFF3EA6FF)),
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    ),
                    onPressed: () => _smartSplit(artistFirst: false),
                    icon: const Icon(Icons.auto_fix_high, size: 15),
                    label: const Text('Title - Artist', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                  ),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Colors.white70,
                      side: const BorderSide(color: Colors.white24),
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    ),
                    onPressed: _swapArtistTitle,
                    icon: const Icon(Icons.swap_vert, size: 15),
                    label: const Text('Swap (⇄)', style: TextStyle(fontSize: 12)),
                  ),
                  if (hasFeatInArtist)
                    OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.orangeAccent,
                        side: const BorderSide(color: Colors.orangeAccent),
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                      ),
                      onPressed: _normalizeFeaturedArtists,
                      icon: const Icon(Icons.drive_file_move_outline, size: 15),
                      label: const Text('Move ft. to Title', style: TextStyle(fontSize: 12)),
                    ),
                  OutlinedButton.icon(
                    style: OutlinedButton.styleFrom(
                      foregroundColor: const Color(0xFF00B4D8),
                      side: const BorderSide(color: Color(0xFF00B4D8)),
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    ),
                    onPressed: _isSearchingMb ? null : _searchMusicBrainz,
                    icon: _isSearchingMb
                        ? const SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF00B4D8)))
                        : const Icon(Icons.search, size: 15),
                    label: const Text('Search MusicBrainz', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                  ),
                ],
              ),
              const SizedBox(height: 14),

              // MusicBrainz Loading Indicator
              if (_isSearchingMb) ...[
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: const Color(0xFF14141A),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: const Color(0xFF00B4D8).withOpacity(0.3)),
                  ),
                  child: const Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF00B4D8))),
                      SizedBox(width: 10),
                      Text('Searching MusicBrainz database...', style: TextStyle(fontSize: 12, color: Colors.white70)),
                    ],
                  ),
                ),
                const SizedBox(height: 14),
              ],

              // MusicBrainz Search Feedback
              if (_mbSearchMessage != null && !_isSearchingMb) ...[
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  decoration: BoxDecoration(
                    color: Colors.amber.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: Colors.amber.withOpacity(0.3)),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.info_outline, size: 14, color: Colors.amber),
                      const SizedBox(width: 8),
                      Expanded(child: Text(_mbSearchMessage!, style: const TextStyle(fontSize: 11, color: Colors.amber))),
                      IconButton(
                        padding: EdgeInsets.zero,
                        constraints: const BoxConstraints(),
                        icon: const Icon(Icons.close, size: 14, color: Colors.white54),
                        onPressed: () => setState(() => _mbSearchMessage = null),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 14),
              ],

              // MusicBrainz Candidate List
              if (_mbMatches != null && _mbMatches!.isNotEmpty && !_isSearchingMb) ...[
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: const Color(0xFF14141A),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: const Color(0xFF00B4D8).withOpacity(0.4)),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Row(
                            children: [
                              const Icon(Icons.library_music, size: 14, color: Color(0xFF00B4D8)),
                              const SizedBox(width: 6),
                              Text(
                                'MusicBrainz Matches (${_mbMatches!.length})',
                                style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Color(0xFF00B4D8)),
                              ),
                            ],
                          ),
                          IconButton(
                            padding: EdgeInsets.zero,
                            constraints: const BoxConstraints(),
                            icon: const Icon(Icons.close, size: 14, color: Colors.white54),
                            onPressed: () => setState(() => _mbMatches = null),
                            tooltip: 'Close matches',
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      ..._mbMatches!.map((match) {
                        return Container(
                          margin: const EdgeInsets.only(bottom: 8),
                          padding: const EdgeInsets.all(8),
                          decoration: BoxDecoration(
                            color: const Color(0xFF1E1E26),
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(color: Colors.white10),
                          ),
                          child: Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Row(
                                      children: [
                                        Expanded(
                                          child: Text(
                                            match.title,
                                            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold),
                                            maxLines: 1,
                                            overflow: TextOverflow.ellipsis,
                                          ),
                                        ),
                                        const SizedBox(width: 4),
                                        Container(
                                          padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                                          decoration: BoxDecoration(
                                            color: match.score >= 90 ? Colors.green.withOpacity(0.2) : Colors.amber.withOpacity(0.2),
                                            borderRadius: BorderRadius.circular(4),
                                          ),
                                          child: Text(
                                            '${match.score}%',
                                            style: TextStyle(
                                              fontSize: 10,
                                              fontWeight: FontWeight.bold,
                                              color: match.score >= 90 ? Colors.greenAccent : Colors.amberAccent,
                                            ),
                                          ),
                                        ),
                                      ],
                                    ),
                                    const SizedBox(height: 2),
                                    Text(
                                      'Artist: ${match.artist}',
                                      style: const TextStyle(fontSize: 11, color: Colors.white70),
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                    ),
                                    if (match.album != null && match.album!.isNotEmpty)
                                      Text(
                                        'Album: ${match.album}${match.releaseDate != null ? ' (${match.releaseDate!.split('-')[0]})' : ''}${match.trackNumber != null ? ' • Track #${match.trackNumber}' : ''}',
                                        style: TextStyle(fontSize: 10, color: Colors.grey[400]),
                                        maxLines: 1,
                                        overflow: TextOverflow.ellipsis,
                                      ),
                                  ],
                                ),
                              ),
                              const SizedBox(width: 8),
                              Column(
                                children: [
                                  OutlinedButton(
                                    style: OutlinedButton.styleFrom(
                                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                                      minimumSize: const Size(54, 26),
                                      side: const BorderSide(color: Color(0xFF00B4D8)),
                                      foregroundColor: const Color(0xFF00B4D8),
                                    ),
                                    onPressed: () => _applyMbMatch(match),
                                    child: const Text('Apply', style: TextStyle(fontSize: 11)),
                                  ),
                                  const SizedBox(height: 4),
                                  FilledButton(
                                    style: FilledButton.styleFrom(
                                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                                      minimumSize: const Size(54, 26),
                                      backgroundColor: const Color(0xFFFF0000),
                                      foregroundColor: Colors.white,
                                    ),
                                    onPressed: () => _applyMbMatch(match, andUpload: true),
                                    child: const Text('Upload', style: TextStyle(fontSize: 11)),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        );
                      }),
                    ],
                  ),
                ),
                const SizedBox(height: 14),
              ],

              // Title Field
              TextField(
                controller: _titleController,
                decoration: InputDecoration(
                  labelText: 'Song Title *',
                  hintText: 'e.g. For Some Strange Reason ft. Brotha Lynch Hung',
                  isDense: true,
                  filled: true,
                  fillColor: const Color(0xFF14141A),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                  prefixIcon: const Icon(Icons.title, size: 18),
                ),
              ),
              const SizedBox(height: 12),

              // Artist Field
              TextField(
                controller: _artistController,
                decoration: InputDecoration(
                  labelText: 'Artist Name *',
                  hintText: 'e.g. C-Mob',
                  isDense: true,
                  filled: true,
                  fillColor: const Color(0xFF14141A),
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                  prefixIcon: const Icon(Icons.person_outline, size: 18),
                ),
              ),
              const SizedBox(height: 12),

              // Album & Track # Row
              Row(
                children: [
                  Expanded(
                    flex: 3,
                    child: TextField(
                      controller: _albumController,
                      decoration: InputDecoration(
                        labelText: 'Album (Optional)',
                        hintText: 'e.g. Masterpiece of Mind',
                        isDense: true,
                        filled: true,
                        fillColor: const Color(0xFF14141A),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                        prefixIcon: const Icon(Icons.album_outlined, size: 18),
                      ),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    flex: 1,
                    child: TextField(
                      controller: _trackNumController,
                      keyboardType: TextInputType.number,
                      decoration: InputDecoration(
                        labelText: 'Track #',
                        hintText: '1',
                        isDense: true,
                        filled: true,
                        fillColor: const Color(0xFF14141A),
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(8)),
                      ),
                    ),
                  ),
                ],
              ),

              if (_errorMessage != null) ...[
                const SizedBox(height: 12),
                Text(_errorMessage!, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
              ],

              if (_isSaving && widget.ytmUpload != null) ...[
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: const Color(0xFF00B4D8).withValues(alpha: 0.1),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: const Color(0xFF00B4D8).withValues(alpha: 0.3)),
                  ),
                  child: const Row(
                    children: [
                      SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFF00B4D8))),
                      SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          'Downloading audio from YouTube Music, applying tags, and replacing upload... This may take up to a minute.',
                          style: TextStyle(fontSize: 11, color: Color(0xFF00B4D8)),
                        ),
                      ),
                    ],
                  ),
                ),
              ],

              const SizedBox(height: 20),

              // Bottom Actions
              Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  OutlinedButton(
                    onPressed: _isSaving ? null : () => Navigator.of(context).pop(),
                    child: const Text('Cancel'),
                  ),
                  const SizedBox(width: 10),
                  if (widget.ytmUpload != null) ...[
                    FilledButton.icon(
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFFFF0000),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                      ),
                      onPressed: _isSaving ? null : () => _saveMetadata(),
                      icon: _isSaving
                          ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                          : const Icon(Icons.published_with_changes, size: 16),
                      label: Text(_isSaving ? 'Replacing on YTM...' : 'Retag & Replace on YTM'),
                    ),
                  ] else ...[
                    FilledButton.tonal(
                      onPressed: _isSaving ? null : () => _saveMetadata(enqueueUpload: false),
                      child: _isSaving
                          ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Text('Save Metadata'),
                    ),
                    const SizedBox(width: 10),
                    FilledButton(
                      style: FilledButton.styleFrom(
                        backgroundColor: const Color(0xFFFF0000),
                        foregroundColor: Colors.white,
                      ),
                      onPressed: _isSaving ? null : () => _saveMetadata(enqueueUpload: true),
                      child: _isSaving
                          ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                          : const Row(
                              children: [
                                Icon(Icons.cloud_upload, size: 16),
                                SizedBox(width: 6),
                                Text('Save & Upload'),
                              ],
                            ),
                    ),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

