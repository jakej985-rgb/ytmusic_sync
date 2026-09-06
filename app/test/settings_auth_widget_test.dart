import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:app/models/models.dart';
import 'package:app/views/settings_view.dart';

void main() {
  group('AuthState Model Tests', () {
    test('AuthState transitions and helpers', () {
      expect(AuthState.disconnected.isConnecting, isFalse);
      expect(AuthState.starting.isConnecting, isTrue);
      expect(AuthState.waitingForBrowser.isConnecting, isTrue);
      expect(AuthState.authenticating.isConnecting, isTrue);
      expect(AuthState.verifying.isConnecting, isTrue);
      expect(AuthState.connected.isConnecting, isFalse);
      expect(AuthState.failed.canRetry, isTrue);
      expect(AuthState.cancelled.canRetry, isTrue);
      expect(AuthState.expired.canRetry, isTrue);
      expect(AuthState.connected.canRetry, isFalse);
    });

    test('AuthSessionInfo JSON parsing', () {
      final json = {
        'session_id': 'test_sess_123',
        'auth_url': 'https://music.youtube.com?ytm_sync_session=test_sess_123',
        'status': 'pending',
        'connected': false,
        'user_name': null,
        'error_message': null,
        'expires_at': 1700000000.0,
      };

      final info = AuthSessionInfo.fromJson(json);
      expect(info.sessionId, equals('test_sess_123'));
      expect(info.status, equals(AuthState.waitingForBrowser));
      expect(info.connected, isFalse);
      expect(info.authUrl, contains('ytm_sync_session=test_sess_123'));
    });
  });

  group('SettingsView Auth UI Tests', () {
    testWidgets('SettingsView renders clean Connect YouTube Music button without normal DevTools text',
        (WidgetTester tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: SettingsView(),
          ),
        ),
      );

      // Wait for async load to finish
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));

      // 1. Check title
      expect(find.text('1. YouTube Music Account'), findsOneWidget);

      // 2. Normal view should contain the primary Connect button
      expect(find.text('Connect YouTube Music'), findsOneWidget);

      // 3. Normal view should NOT have F12 instructions visible
      expect(find.textContaining('Press F12 to open Developer Tools'), findsNothing);

      // 4. Advanced / Developer section should exist
      expect(find.text('Advanced / Developer Authentication'), findsOneWidget);
    });
  });
}
