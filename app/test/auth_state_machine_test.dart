import 'package:flutter_test/flutter_test.dart';
import 'package:app/models/auth_state.dart';

void main() {
  group('Phase 19: UI Auth State Machine Transitions', () {
    test('Flow 1: Disconnected -> Connect -> Starting -> Waiting -> Authenticating -> Verifying -> Connected', () {
      // 1. Initial Disconnected state
      AuthState state = AuthState.disconnected;
      expect(state, equals(AuthState.disconnected));
      expect(state.isConnecting, isFalse);
      expect(state.canRetry, isFalse);
      expect(state.label, equals('Not Connected'));

      // 2. User presses Connect -> Starting
      state = AuthState.starting;
      expect(state.isConnecting, isTrue);
      expect(state.label, equals('Starting connection...'));

      // 3. Backend returns session -> Waiting for Browser
      state = AuthState.waitingForBrowser;
      expect(state.isConnecting, isTrue);
      expect(state.label, equals('Complete authentication in browser'));

      // 4. Browser / companion intercepts -> Authenticating
      state = AuthState.authenticating;
      expect(state.isConnecting, isTrue);
      expect(state.label, equals('Connecting to YouTube Music...'));

      // 5. Credentials received -> Verifying
      state = AuthState.verifying;
      expect(state.isConnecting, isTrue);
      expect(state.label, equals('Verifying account...'));

      // 6. Test connection succeeds -> Connected
      state = AuthState.connected;
      expect(state.isConnecting, isFalse);
      expect(state.canRetry, isFalse);
      expect(state.label, equals('Connected'));
    });

    test('Flow 2: Connect -> Cancelled -> Disconnected', () {
      // 1. User starts connection
      AuthState state = AuthState.starting;
      expect(state.isConnecting, isTrue);

      state = AuthState.waitingForBrowser;
      expect(state.isConnecting, isTrue);

      // 2. User cancels
      state = AuthState.cancelled;
      expect(state.isConnecting, isFalse);
      expect(state.canRetry, isTrue);
      expect(state.label, equals('Authentication cancelled'));

      // 3. Reset to disconnected
      state = AuthState.disconnected;
      expect(state, equals(AuthState.disconnected));
      expect(state.label, equals('Not Connected'));
    });

    test('Flow 3: Connect -> Expired -> Retry', () {
      // 1. User connects and waits
      AuthState state = AuthState.waitingForBrowser;
      expect(state.isConnecting, isTrue);

      // 2. 10-minute session expires
      state = AuthState.expired;
      expect(state.isConnecting, isFalse);
      expect(state.canRetry, isTrue);
      expect(state.label, equals('Session expired'));

      // 3. User clicks Retry / Try Again -> restarts flow
      expect(state.canRetry, isTrue);
      state = AuthState.starting;
      expect(state.isConnecting, isTrue);
      expect(state.label, equals('Starting connection...'));

      state = AuthState.connected;
      expect(state, equals(AuthState.connected));
    });

    test('Flow 4: Connect -> Failed -> Retry', () {
      // 1. User connects
      AuthState state = AuthState.waitingForBrowser;
      expect(state.isConnecting, isTrue);

      // 2. Backend verification fails (e.g. invalid cookies)
      state = AuthState.failed;
      expect(state.isConnecting, isFalse);
      expect(state.canRetry, isTrue);
      expect(state.label, equals('Connection failed'));

      // 3. User clicks Retry / Try Again -> restarts flow
      expect(state.canRetry, isTrue);
      state = AuthState.starting;
      expect(state.isConnecting, isTrue);

      state = AuthState.connected;
      expect(state, equals(AuthState.connected));
    });
  });
}
