import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:app/services/api_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({'ytm_sync_api_key': 'initial_key'});
  });

  test('ApiService initializes key from SharedPreferences', () async {
    final service = ApiService(baseUrl: 'http://127.0.0.1:8765');
    await service.initApiKey();
    expect(service.apiKey, 'initial_key');
    expect(service.isUnauthorized, false);
  });

  test('401 response clears apiKey, marks unauthorized, triggers callback, and prevents loops', () async {
    int requestCount = 0;
    final mockClient = MockClient((request) async {
      requestCount++;
      // Check Bearer authorization header was sent
      expect(request.headers['Authorization'], 'Bearer bad_key');
      return http.Response('{"detail":"Unauthorized: Invalid API Key"}', 401);
    });

    final service = ApiService(baseUrl: 'http://127.0.0.1:8765', client: mockClient);
    await service.setApiKey('bad_key');
    expect(service.apiKey, 'bad_key');
    expect(service.isUnauthorized, false);

    bool callbackTriggered = false;
    service.onUnauthorized = () {
      callbackTriggered = true;
    };

    // First request: receives 401
    await expectLater(
      service.fetchDashboardStatus(),
      throwsA(isA<Exception>().having((e) => e.toString(), 'message', contains('Unauthorized'))),
    );

    // Verify 1: callback was triggered
    expect(callbackTriggered, isTrue);

    // Verify 2: apiKey was cleared from memory
    expect(service.apiKey, isNull);

    // Verify 3: isUnauthorized flag is set
    expect(service.isUnauthorized, isTrue);

    // Verify 4: apiKey was removed from SharedPreferences
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('ytm_sync_api_key'), isNull);

    // Verify 5: Subsequent requests fail immediately without making network calls (preventing loops)
    await expectLater(
      service.fetchDashboardStatus(),
      throwsA(isA<Exception>().having((e) => e.toString(), 'message', contains('Unauthorized'))),
    );
    // Request count should still be 1! Network was NOT hit a second time
    expect(requestCount, 1);

    // Verify 6: Setting a new key clears unauthorized state and allows requests
    final succeedingClient = MockClient((request) async {
      expect(request.headers['Authorization'], 'Bearer valid_key');
      return http.Response('{"folders":[],"counts":{"total_tracks":10}}', 200);
    });

    final recoveryService = ApiService(baseUrl: 'http://127.0.0.1:8765', client: succeedingClient);
    await recoveryService.setApiKey('valid_key');
    expect(recoveryService.isUnauthorized, isFalse);
    expect(recoveryService.apiKey, 'valid_key');

    final stats = await recoveryService.fetchDashboardStatus();
    expect(stats, isNotNull);
  });
}
