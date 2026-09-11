import 'package:flutter_test/flutter_test.dart';
import 'package:app/main.dart';

void main() {
  testWidgets('Red Music Locker app shell renders', (WidgetTester tester) async {
    await tester.pumpWidget(const YTMSyncApp());
    expect(find.text('RED MUSIC LOCKER'), findsOneWidget);
    expect(find.text('Dashboard'), findsOneWidget);
  });
}
