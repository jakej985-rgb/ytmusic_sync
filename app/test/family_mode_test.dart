import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:app/models/models.dart';
import 'package:app/views/components/account_selector_widget.dart';
import 'package:app/views/components/upload_destination_dialog.dart';
import 'package:app/views/family_view.dart';

void main() {
  group('Family Mode Models JSON Parsing', () {
    test('Family JSON deserialization', () {
      final json = {
        'id': 'fam_123',
        'name': 'Johnson Family',
        'owner_user_id': 'user_dad',
        'created_at': '2026-09-07T00:00:00Z',
        'user_role': 'OWNER',
      };
      final fam = Family.fromJson(json);
      expect(fam.id, 'fam_123');
      expect(fam.name, 'Johnson Family');
      expect(fam.ownerUserId, 'user_dad');
      expect(fam.userRole, 'OWNER');
    });

    test('FamilyMember JSON deserialization with privacy defaults', () {
      final json = {
        'id': 'mem_1',
        'family_id': 'fam_123',
        'user_id': 'user_mom',
        'username': 'Mom',
        'role': 'MEMBER',
        'status': 'ACTIVE',
        'show_account_in_family': 1,
        'allow_family_uploads': 1,
        'allow_family_playlists': 0,
        'allow_family_sync': 0,
      };
      final member = FamilyMember.fromJson(json);
      expect(member.username, 'Mom');
      expect(member.showAccountInFamily, isTrue);
      expect(member.allowFamilyUploads, isTrue);
      expect(member.allowFamilyPlaylists, isFalse);
      expect(member.allowFamilySync, isFalse);
    });

    test('FamilyDashboardResponse JSON deserialization', () {
      final json = {
        'family_id': 'fam_123',
        'family_name': 'Johnson Family',
        'caller_role': 'OWNER',
        'total_members': 3,
        'active_connected_members': 2,
        'members': [
          {
            'user_id': 'u1',
            'username': 'Dad',
            'role': 'OWNER',
            'ytm_connected': true,
            'account_name': 'Dad YTM',
            'allow_family_uploads': true,
            'allow_family_playlists': false,
            'allow_family_sync': false,
          },
        ],
      };
      final dash = FamilyDashboardResponse.fromJson(json);
      expect(dash.familyId, 'fam_123');
      expect(dash.familyName, 'Johnson Family');
      expect(dash.totalMembers, 3);
      expect(dash.members.length, 1);
      expect(dash.members.first.username, 'Dad');
      expect(dash.members.first.ytmConnected, isTrue);
    });

    test('SelectableAccountItem and UploadDestinationResponse JSON parsing', () {
      final accJson = {
        'user_id': 'dest_1',
        'username': 'Mom',
        'account_name': 'Mom Account',
        'is_connected': true,
        'is_self': false,
        'family_name': 'Johnson Family',
        'allow_family_uploads': true,
      };
      final acc = SelectableAccountItem.fromJson(accJson);
      expect(acc.displayName, 'Mom');
      expect(acc.displayAccount, 'Mom Account');
      expect(acc.allowFamilyUploads, isTrue);

      final uploadResp = UploadDestinationResponse.fromJson({
        'jobs_created': 2,
        'job_ids': [101, 102],
        'destinations': ['dest_1', 'dest_2'],
      });
      expect(uploadResp.jobsCreated, 2);
      expect(uploadResp.jobIds, [101, 102]);
      expect(uploadResp.destinations.length, 2);
    });
  });

  group('Family Mode UI Components Rendering', () {
    testWidgets('AccountSelectorWidget renders correctly', (WidgetTester tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: Center(
              child: AccountSelectorWidget(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(AccountSelectorWidget), findsOneWidget);
    });

    testWidgets('UploadDestinationDialog renders with tracks and action buttons', (WidgetTester tester) async {
      final testTrack = MusicFile(
        id: 1,
        path: '/music/test.mp3',
        filename: 'test.mp3',
        title: 'Test Song',
        artist: 'Test Artist',
        format: 'mp3',
        fileSize: 1024,
        uploadStatus: 'not_uploaded',
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Builder(
              builder: (ctx) => ElevatedButton(
                onPressed: () => UploadDestinationDialog.show(ctx, [testTrack]),
                child: const Text('Open Dialog'),
              ),
            ),
          ),
        ),
      );

      await tester.tap(find.text('Open Dialog'));
      await tester.pumpAndSettle();

      expect(find.text('Select Upload Destination'), findsOneWidget);
      expect(find.text('Cancel'), findsOneWidget);
    });

    testWidgets('FamilyView renders empty state or header', (WidgetTester tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: FamilyView(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('Family Mode'), findsOneWidget);
      expect(find.text('Create Family'), findsOneWidget);
      expect(find.text('Join Family'), findsOneWidget);
    });
  });
}
