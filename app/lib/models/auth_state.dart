enum AuthState {
  disconnected,
  starting,
  waitingForBrowser,
  authenticating,
  verifying,
  connected,
  failed,
  cancelled,
  expired,
}

extension AuthStateX on AuthState {
  String get label {
    switch (this) {
      case AuthState.disconnected:
        return 'Not Connected';
      case AuthState.starting:
        return 'Starting connection...';
      case AuthState.waitingForBrowser:
        return 'Complete authentication in browser';
      case AuthState.authenticating:
        return 'Connecting to YouTube Music...';
      case AuthState.verifying:
        return 'Verifying account...';
      case AuthState.connected:
        return 'Connected';
      case AuthState.failed:
        return 'Connection failed';
      case AuthState.cancelled:
        return 'Authentication cancelled';
      case AuthState.expired:
        return 'Session expired';
    }
  }

  bool get isConnecting {
    return this == AuthState.starting ||
        this == AuthState.waitingForBrowser ||
        this == AuthState.authenticating ||
        this == AuthState.verifying;
  }

  bool get canRetry {
    return this == AuthState.failed ||
        this == AuthState.cancelled ||
        this == AuthState.expired;
  }
}

class AuthSessionInfo {
  final String sessionId;
  final String authUrl;
  final AuthState status;
  final bool connected;
  final String? userName;
  final String? errorMessage;
  final double expiresAt;

  AuthSessionInfo({
    required this.sessionId,
    required this.authUrl,
    required this.status,
    this.connected = false,
    this.userName,
    this.errorMessage,
    required this.expiresAt,
  });

  factory AuthSessionInfo.fromJson(Map<String, dynamic> json) {
    AuthState parseStatus(String? s) {
      switch (s?.toLowerCase()) {
        case 'pending':
          return AuthState.waitingForBrowser;
        case 'authenticating':
          return AuthState.authenticating;
        case 'processing':
          return AuthState.verifying;
        case 'connected':
          return AuthState.connected;
        case 'failed':
          return AuthState.failed;
        case 'cancelled':
          return AuthState.cancelled;
        case 'expired':
          return AuthState.expired;
        default:
          return AuthState.waitingForBrowser;
      }
    }

    return AuthSessionInfo(
      sessionId: json['session_id'] ?? '',
      authUrl: json['auth_url'] ?? '',
      status: parseStatus(json['status']),
      connected: json['connected'] ?? false,
      userName: json['user_name'],
      errorMessage: json['error_message'],
      expiresAt: (json['expires_at'] is num) ? (json['expires_at'] as num).toDouble() : 0.0,
    );
  }
}
