import 'package:flutter/material.dart';
import '../../models/models.dart';
import '../../services/api_service.dart';

class MetadataEditorDialog extends StatefulWidget {
  final MusicFile song;

  const MetadataEditorDialog({super.key, required this.song});

  static Future<bool?> show(BuildContext context, MusicFile song) {
    return showDialog<bool>(
      context: context,
      builder: (context) => MetadataEditorDialog(song: song),
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

  @override
  void initState() {
    super.initState();
    _titleController = TextEditingController(text: widget.song.title ?? widget.song.filename.replaceAll(RegExp(r'\.[a-zA-Z0-9]+$'), ''));
    _artistController = TextEditingController(text: widget.song.artist ?? '');
    _albumController = TextEditingController(text: widget.song.album ?? '');
    _trackNumController = TextEditingController(text: widget.song.trackNumber != null ? widget.song.trackNumber.toString() : '');
  }

  @override
  void dispose() {
    _titleController.dispose();
    _artistController.dispose();
    _albumController.dispose();
    _trackNumController.dispose();
    super.dispose();
  }

  /// Automatically parses filename into Artist and Title based on requested order
  void _smartSplit({required bool artistFirst}) {
    final rawName = widget.song.filename.replaceAll(RegExp(r'\.[a-zA-Z0-9]+$'), '').trim();
    String cleanName = rawName;
    String? trackNum;

    // Check optional track number prefix (e.g. "01 - ", "01. ", "01 ")
    final trackPrefixMatch = RegExp(r'^(\d+)\s*[-._\s]\s*(.+)$').firstMatch(cleanName);
    if (trackPrefixMatch != null) {
      trackNum = trackPrefixMatch.group(1);
      cleanName = trackPrefixMatch.group(2)!.trim();
    }

    String? partA;
    String? partB;

    // 1. Try splitting by " - " or " _ " or " by "
    if (cleanName.contains(' - ')) {
      final parts = cleanName.split(' - ');
      partA = parts[0].trim();
      partB = parts.sublist(1).join(' - ').trim();
    } else if (cleanName.contains(' _ ')) {
      final parts = cleanName.split(' _ ');
      partA = parts[0].trim();
      partB = parts.sublist(1).join(' _ ').trim();
    } else if (RegExp(r'\s+by\s+', caseSensitive: false).hasMatch(cleanName)) {
      final match = RegExp(r'^(.+?)\s+by\s+(.+)$', caseSensitive: false).firstMatch(cleanName);
      if (match != null) {
        final title = match.group(1)?.trim();
        final artist = match.group(2)?.trim();
        setState(() {
          if (artistFirst) {
            _artistController.text = artist ?? '';
            _titleController.text = title ?? '';
          } else {
            _titleController.text = title ?? '';
            _artistController.text = artist ?? '';
          }
          if (trackNum != null && trackNum.isNotEmpty) _trackNumController.text = trackNum;
        });
        _notifyAutoFill(artistFirst ? 'Artist - Title' : 'Title - Artist');
        return;
      }
    } else if (cleanName.contains('_')) {
      final parts = cleanName.split('_');
      partA = parts[0].trim();
      partB = parts.sublist(1).join(' ').trim();
    } else if (cleanName.contains('-')) {
      final parts = cleanName.split('-');
      partA = parts[0].trim();
      partB = parts.sublist(1).join('-').trim();
    }

    if (partA != null && partB != null) {
      setState(() {
        if (artistFirst) {
          _artistController.text = partA!;
          _titleController.text = partB!;
        } else {
          _titleController.text = partA!;
          _artistController.text = partB!;
        }
        if (trackNum != null && trackNum.isNotEmpty) {
          _trackNumController.text = trackNum;
        }
      });
      _notifyAutoFill(artistFirst ? 'Artist - Title' : 'Title - Artist');
    } else {
      setState(() {
        _titleController.text = cleanName;
        if (trackNum != null && trackNum.isNotEmpty) {
          _trackNumController.text = trackNum;
        }
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('No separator found; used entire name as Title.'),
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

  Future<void> _saveMetadata({bool enqueueUpload = false}) async {
    final title = _titleController.text.trim();
    if (title.isEmpty) {
      setState(() => _errorMessage = 'Title is required');
      return;
    }

    if (widget.song.id == null) return;

    setState(() {
      _isSaving = true;
      _errorMessage = null;
    });

    try {
      final artist = _artistController.text.trim();
      final album = _albumController.text.trim();
      final trackNum = int.tryParse(_trackNumController.text.trim());

      await apiService.updateSongMetadata(
        widget.song.id!,
        title: title,
        artist: artist.isNotEmpty ? artist : null,
        album: album.isNotEmpty ? album : null,
        trackNumber: trackNum,
      );

      if (enqueueUpload) {
        await apiService.uploadSong(widget.song.id!);
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
    return Dialog(
      backgroundColor: const Color(0xFF181820),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: Container(
        width: 540,
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Title Header
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Row(
                  children: [
                    Icon(Icons.edit_note, color: Color(0xFFFF0000), size: 24),
                    SizedBox(width: 10),
                    Text(
                      'Edit Track Metadata',
                      style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
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
                      const Icon(Icons.audio_file_outlined, size: 14, color: Colors.grey),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          widget.song.filename,
                          style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, fontFamily: 'monospace'),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(
                    widget.song.path,
                    style: TextStyle(fontSize: 10, color: Colors.grey[500], fontFamily: 'monospace'),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),

            // Smart Split Controls
            Wrap(
              spacing: 8,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: const Color(0xFF3EA6FF),
                    side: const BorderSide(color: Color(0xFF3EA6FF)),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                  onPressed: () => _smartSplit(artistFirst: true),
                  icon: const Icon(Icons.auto_fix_high, size: 16),
                  label: const Text('Artist - Title', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                ),
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: const Color(0xFF3EA6FF),
                    side: const BorderSide(color: Color(0xFF3EA6FF)),
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                  onPressed: () => _smartSplit(artistFirst: false),
                  icon: const Icon(Icons.auto_fix_high, size: 16),
                  label: const Text('Title - Artist', style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                ),
                OutlinedButton.icon(
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.white70,
                    side: const BorderSide(color: Colors.white24),
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  ),
                  onPressed: _swapArtistTitle,
                  icon: const Icon(Icons.swap_vert, size: 16),
                  label: const Text('Swap (⇄)', style: TextStyle(fontSize: 12)),
                ),
              ],
            ),
            const SizedBox(height: 16),

            // Title Field
            TextField(
              controller: _titleController,
              decoration: InputDecoration(
                labelText: 'Song Title *',
                hintText: 'e.g. I Wanna Love You',
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
                hintText: 'e.g. Akon',
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
                      hintText: 'e.g. Konvicted',
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
            ),
          ],
        ),
      ),
    );
  }
}
