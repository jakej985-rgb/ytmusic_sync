import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

import 'package:app/models/models.dart';
import 'package:app/services/api_service.dart';
import 'package:app/views/components/auth_dialog.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  group('User and Account Models', () {
    test('User JSON parsing and serialization', () {
      final json = {
        'id': 'usr_123',
        'username': 'alice',
        'role': 'ADMIN',
        'is_active': true,
        'created_at': '2026-09-06T12:00:00Z',
        'updated_at': '2026-09-06T12:00:00Z',
      };

      final user = User.fromJson(json);
      expect(user.id, 'usr_123');
      expect(user.username, 'alice');
      expect(user.role, 'ADMIN');
      expect(user.isAdmin, isTrue);
      expect(user.isActive, isTrue);
      expect(user.createdAt, isNotNull);

      final outJson = user.toJson();
      expect(outJson['id'], 'usr_123');
      expect(outJson['username'], 'alice');
      expect(outJson['role'], 'ADMIN');
    });

    test('UserLoginResponse JSON parsing', () {
      final json = {
        'token': 'sess_tok_abc',
        'user': {
          'id': 'usr_456',
          'username': 'bob',
          'role': 'USER',
          'is_active': true,
        },
        'session_expires_at': '2026-09-13T12:00:00Z',
      };

      final resp = UserLoginResponse.fromJson(json);
      expect(resp.token, 'sess_tok_abc');
      expect(resp.user.username, 'bob');
      expect(resp.user.isAdmin, isFalse);
      expect(resp.sessionExpiresAt, isNotNull);
    });

    test('YouTubeMusicAccount JSON parsing', () {
      final json = {
        'id': 'ytm_acc_789',
        'user_id': 'usr_123',
        'account_name': 'Alice YTM',
        'account_email': 'alice@example.com',
        'status': 'CONNECTED',
      };

      final account = YouTubeMusicAccount.fromJson(json);
      expect(account.id, 'ytm_acc_789');
      expect(account.userId, 'usr_123');
      expect(account.accountName, 'Alice YTM');
      expect(account.accountEmail, 'alice@example.com');
      expect(account.status, 'CONNECTED');
      expect(account.isConnected, isTrue);
    });
  });

  group('ApiService Multi-User Session Management', () {
    test('login sets token and user, persists to SharedPreferences', () async {
      final mockClient = MockClient((request) async {
        if (request.url.path == '/api/auth/login') {
          final body = jsonDecode(request.body);
          expect(body['username'], 'alice');
          expect(body['password'], 'secret123');
          return http.Response(
            jsonEncode({
              'token': 'sess_mock_token_123',
              'user': {
                'id': 'usr_1',
                'username': 'alice',
                'role': 'ADMIN',
                'is_active': true,
              },
              'session_expires_at': '2026-09-13T00:00:00Z',
            }),
            200,
          );
        } else if (request.url.path == '/api/ytm/account') {
          return http.Response(
            jsonEncode({
              'id': 'acc_1',
              'user_id': 'usr_1',
              'account_name': 'Alice Channel',
              'status': 'CONNECTED',
            }),
            200,
          );
        }
        return http.Response('Not Found', 404);
      });

      final service = ApiService(baseUrl: 'http://test', client: mockClient);
      final resp = await service.login('alice', 'secret123');

      expect(resp.token, 'sess_mock_token_123');
      expect(service.apiKey, 'sess_mock_token_123');
      expect(service.currentUser?.username, 'alice');
      expect(service.currentUser?.isAdmin, isTrue);
      expect(service.ytmAccount?.accountName, 'Alice Channel');

      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('ytm_sync_session_token'), 'sess_mock_token_123');
    });

    test('logout revokes session on server and clears local state', () async {
      bool logoutCalled = false;
      final mockClient = MockClient((request) async {
        if (request.url.path == '/api/auth/logout') {
          logoutCalled = true;
          expect(request.headers['Authorization'], 'Bearer sess_mock_token_123');
          return http.Response(jsonEncode({'detail': 'Logged out'}), 200);
        }
        return http.Response('Not Found', 404);
      });

      final service = ApiService(baseUrl: 'http://test', client: mockClient);
      await service.setApiKey('sess_mock_token_123');
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('ytm_sync_session_token', 'sess_mock_token_123');

      await service.logout();

      expect(logoutCalled, isTrue);
      expect(service.apiKey, isNull);
      expect(service.currentUser, isNull);
      expect(service.ytmAccount, isNull);
      expect(prefs.getString('ytm_sync_session_token'), isNull);
    });

    test('Admin CRUD operations send correct bearer token and payloads', () async {
      final mockClient = MockClient((request) async {
        expect(request.headers['Authorization'], 'Bearer admin_token');

        if (request.method == 'GET' && request.url.path == '/api/admin/users') {
          return http.Response(
            jsonEncode([
              {'id': 'u1', 'username': 'admin', 'role': 'ADMIN', 'is_active': true},
              {'id': 'u2', 'username': 'bob', 'role': 'USER', 'is_active': true},
            ]),
            200,
          );
        } else if (request.method == 'POST' && request.url.path == '/api/admin/users') {
          final data = jsonDecode(request.body);
          expect(data['username'], 'carol');
          expect(data['role'], 'USER');
          return http.Response(
            jsonEncode({'id': 'u3', 'username': 'carol', 'role': 'USER', 'is_active': true}),
            201,
          );
        } else if (request.method == 'PUT' && request.url.path == '/api/admin/users/u2') {
          final data = jsonDecode(request.body);
          expect(data['is_active'], false);
          return http.Response(
            jsonEncode({'id': 'u2', 'username': 'bob', 'role': 'USER', 'is_active': false}),
            200,
          );
        } else if (request.method == 'DELETE' && request.url.path == '/api/admin/users/u2') {
          return http.Response('', 204);
        }
        return http.Response('Not Found', 404);
      });

      final service = ApiService(baseUrl: 'http://test', client: mockClient);
      await service.setApiKey('admin_token');

      final users = await service.getUsers();
      expect(users.length, 2);

      final created = await service.createUser('carol', 'carolpass');
      expect(created.username, 'carol');

      final updated = await service.updateUser('u2', isActive: false);
      expect(updated.isActive, isFalse);

      await service.deleteUser('u2');
    });
  });

  group('AuthDialog Widget Tests', () {
    testWidgets('AuthDialog switches between tabs and renders fields', (WidgetTester tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: AuthDialog(),
          ),
        ),
      );

      // Verify header
      expect(find.text('Red Music Locker Authentication'), findsOneWidget);
      expect(find.text('User Login'), findsOneWidget);
      expect(find.text('API Key'), findsOneWidget);

      // Verify User Login fields
      expect(find.widgetWithText(TextField, 'Username'), findsOneWidget);
      expect(find.widgetWithText(TextField, 'Password'), findsOneWidget);
      expect(find.widgetWithText(ElevatedButton, 'Sign In'), findsOneWidget);

      // Switch to API Key tab
      await tester.tap(find.text('API Key'));
      await tester.pumpAndSettle();

      // Verify API Key fields
      expect(find.widgetWithText(TextField, 'Master API Key'), findsOneWidget);
      expect(find.widgetWithText(ElevatedButton, 'Save & Connect'), findsOneWidget);
    });
  });
}
